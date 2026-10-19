"""A published-wheel observation never accepts an ambiguous or foreign archive."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_published_wheel", ROOT / "scripts/check_published_wheel.py"
)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "foreign-host",
        "http",
        "missing-digest",
        "duplicate",
        "yanked",
        "wrong-filename",
    ],
)
def test_index_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    wheel = {
        "filename": "pmcp-3.0.0-py3-none-any.whl",
        "packagetype": "bdist_wheel",
        "url": "https://files.pythonhosted.org/packages/archive.whl",
        "digests": {"sha256": "a" * 64},
    }
    doc = {"info": {"version": "3.0.0"}, "urls": [wheel]}
    if mutation == "foreign-host":
        wheel["url"] = "https://example.org/archive.whl"
    elif mutation == "http":
        wheel["url"] = "http://files.pythonhosted.org/archive.whl"
    elif mutation == "missing-digest":
        wheel["digests"]["sha256"] = "invalid"
    elif mutation == "duplicate":
        doc["urls"].append(dict(wheel))
    elif mutation == "yanked":
        wheel["yanked"] = True
    elif mutation == "wrong-filename":
        wheel["filename"] = "extra.whl"
    monkeypatch.setattr(
        checker.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(json.dumps(doc).encode()),
    )
    if mutation == "none":
        assert checker.current_wheel()["sha256"] == "a" * 64
    else:
        with pytest.raises(ValueError):
            checker.current_wheel()


def test_schedule_is_read_only_and_distinct_from_main_source() -> None:
    doc = yaml.safe_load((ROOT / ".github/workflows/published-wheel.yml").read_text())
    assert set(doc.get("on", doc.get(True))) == {"schedule", "workflow_dispatch"}
    assert doc["permissions"] == {"contents": "read"}
    assert doc["jobs"]["published-wheel"]["timeout-minutes"] == 10
    assert all(
        "packages" not in job.get("permissions", {}) for job in doc["jobs"].values()
    )


def test_failed_probe_retains_only_ready_safe_report() -> None:
    doc = yaml.safe_load((ROOT / ".github/workflows/published-wheel.yml").read_text())
    steps = doc["jobs"]["published-wheel"]["steps"]
    probe = next(s for s in steps if s.get("id") == "published")
    upload = next(
        s for s in steps if s.get("name") == "Retain public compatibility identity"
    )
    assert (
        upload["if"]
        == "${{ always() && steps.published.outputs.report_ready == 'true' && vars.PMCP_CI_DISABLE_REPORTS == '' }}"
    )
    assert steps.index(probe) < steps.index(upload)
    assert upload["with"] == {
        "name": "published-wheel-${{ github.run_id }}-${{ github.run_attempt }}",
        "path": "${{ runner.temp }}/published-wheel/report.json",
        "retention-days": 14,
        "if-no-files-found": "error",
    }
    assert (
        "continue-on-error" not in probe
        and "continue-on-error" not in doc["jobs"]["published-wheel"]
    )
    node = next(s for s in steps if s.get("name") == "Verify Node and npm environment")
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert node["run"] == next(
        s["run"]
        for s in release["jobs"]["build"]["steps"]
        if s.get("name") == "Verify Node and npm environment"
    )
    assert steps.index(node) < steps.index(probe)
