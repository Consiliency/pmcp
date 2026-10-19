"""Public-report canaries and failure-path checks (raw reports must stay local)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ci_reports", ROOT / "scripts/ci_reports.py"
)
assert spec and spec.loader
reports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reports)


def inputs(raw: Path) -> None:
    raw.mkdir()
    (raw / "junit.xml").write_text(
        '<testsuites><testsuite><testcase name="test_ok" time="0.5"/></testsuite></testsuites>'
    )
    (raw / "timing-gw0.json").write_text(
        json.dumps(
            {
                "worker": "gw0",
                "selected": 1,
                "phases": [
                    {
                        "id": "a" * 64,
                        "phase": "teardown",
                        "outcome": "passed",
                        "seconds": 0.5,
                    }
                ],
            }
        )
    )
    (raw / "environment.json").write_text(
        json.dumps(
            {
                "tools": dict.fromkeys(("python", "uv", "node", "npm"), "1.2.3"),
                "dependencies": dict.fromkeys(
                    (
                        "pytest",
                        "pytest-xdist",
                        "pytest-cov",
                        "coverage",
                        "mcp",
                        "anyio",
                        "uvicorn",
                        "sse-starlette",
                    ),
                    "1.2.3",
                ),
                "runner": {"ImageOS": "ubuntu24", "ImageVersion": "20261019.1.0"},
            }
        )
    )


@pytest.fixture
def run_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "GITHUB_SHA": "b" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_EVENT_NAME": "pull_request",
    }.items():
        monkeypatch.setenv(key, value)


def test_text_attributes_properties_and_parameter_canaries_are_discarded() -> None:
    canary = "REJECTED-CANARY-DO-NOT-UPLOAD"
    data = f'''<testsuites><testsuite name="{canary}" hostname="/private/home">
    <properties><property name="secret" value="{canary}"/></properties>
    <testcase classname="/private/{canary}" name="test_x[{canary}]" file="{canary}" time="1.25">
    <failure message="{canary}">{canary}&#27;[31m&#10;forged line</failure>
    <system-out>{canary}</system-out><system-err>{canary}</system-err>
    </testcase></testsuite></testsuites>'''.replace("&#27;", "[ESC]")
    safe, count = reports.sanitize_junit(data.encode())
    assert count == 1
    assert canary.encode() not in safe
    assert b"private" not in safe and b"forged" not in safe and b"ESC" not in safe
    assert b"failure" in safe and b' time="1.25"' in safe
    assert reports.sanitize_junit(data.encode())[0] == safe


@pytest.mark.parametrize(
    "data",
    [
        b'<!DOCTYPE x [<!ENTITY x "secret">]><testsuite/>',
        b"<broken",
        b"<testsuite/>",
        b'<testsuite><testcase time="NaN"/></testsuite>',
        b"<testsuite><testcase><failure/><skipped/></testcase></testsuite>",
    ],
)
def test_malformed_empty_entities_and_ambiguous_reports_are_refused(
    data: bytes,
) -> None:
    with pytest.raises((ValueError, reports.ET.ParseError)):
        reports.sanitize_junit(data)


def test_success_is_bound_and_only_allowlisted_files_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_identity: None
) -> None:
    raw, safe = tmp_path / "raw", tmp_path / "safe"
    inputs(raw)
    (raw / "unsafe.log").write_text("SECRET-CANARY")
    monkeypatch.setattr(sys, "argv", ["ci_reports", str(raw), str(safe)])
    assert reports.main() == 0
    assert {p.name for p in safe.iterdir()} == {
        "junit.xml",
        "timings.json",
        "environment.json",
    }
    result = json.loads((safe / "timings.json").read_text())
    assert result["run"] == {
        "sha": "b" * 40,
        "run_id": "123",
        "run_attempt": "2",
        "event_name": "pull_request",
    }
    assert result["workers"][0]["phases"][0]["phase"] == "teardown"
    assert all("SECRET-CANARY" not in p.read_text() for p in safe.iterdir())


@pytest.mark.parametrize(
    "change",
    [
        "missing-junit",
        "missing-worker",
        "invalid-worker",
        "invalid-identity",
        "invalid-json",
    ],
)
def test_failure_never_installs_safe_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_identity: None,
    change: str,
) -> None:
    raw, safe = tmp_path / "raw", tmp_path / "safe"
    inputs(raw)
    if change == "missing-junit":
        (raw / "junit.xml").unlink()
    elif change == "missing-worker":
        (raw / "timing-gw0.json").unlink()
    elif change == "invalid-worker":
        (raw / "timing-gw0.json").write_text('{"worker":"SECRET-CANARY"}')
    elif change == "invalid-identity":
        monkeypatch.setenv("GITHUB_SHA", "SECRET-CANARY")
    else:
        (raw / "timing-gw0.json").write_text("SECRET-CANARY")
    monkeypatch.setattr(sys, "argv", ["ci_reports", str(raw), str(safe)])
    assert reports.main() == 1
    assert not safe.exists()
    assert "SECRET-CANARY" not in capsys.readouterr().out


def test_report_size_is_bounded_before_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "large"
    file.write_bytes(b"x" * 20)
    monkeypatch.setattr(reports, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        reports.bounded_read(file)


def test_workflow_upload_cannot_run_on_sanitizer_failure() -> None:
    doc: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github/workflows/test.yml").read_text()
    )
    steps = doc["jobs"]["test"]["steps"]
    sanitize = next(step for step in steps if step.get("id") == "sanitize")
    upload = next(step for step in steps if step.get("name") == "Upload safe reports")
    assert sanitize["if"] == "always()"
    assert not sanitize.get("continue-on-error")
    assert "steps.sanitize.outcome == 'success'" in upload["if"]
    assert "vars.PMCP_CI_DISABLE_REPORTS == ''" in upload["if"]
    assert upload["with"]["path"] == ".ci-reports/safe/"
    assert upload["with"]["retention-days"] == 14
