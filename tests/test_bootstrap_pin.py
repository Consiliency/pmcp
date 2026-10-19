"""Privileged bootstrap must fail closed before any mutable tool install."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_installs_only_exact_verified_lock() -> None:
    doc = yaml.safe_load(
        (ROOT / ".github/workflows/pipeline-bootstrap.yml").read_text()
    )
    step = next(
        s
        for s in doc["jobs"]["bootstrap"]["steps"]
        if s.get("name") == "Install governed-pipeline CLI"
    )
    assert (
        step["env"]["GOVERNED_PIPELINE_REF"]
        == "798109d271405378c9f15bdf9db9a383814d5f42"
    )
    assert (
        step["env"]["GOVERNED_PIPELINE_LOCK_SHA256"]
        == "72ce0d6e867d66a99ba8965802b8740135b40d204a3fa49196a4f2079eed3afc"
    )
    assert (
        step["env"]["GOVERNED_PIPELINE_REPOSITORY"] == "Consiliency/governed-pipeline"
    )
    script = step["run"]
    assert script.index("::add-mask::") < script.index("GIT_CONFIG_VALUE_0=")
    assert "||" not in script and "git clone" not in script
    assert "npm install" not in script
    assert 'npm ci --ignore-scripts --prefix "$gp_dir"' in script
    assert "rev-parse FETCH_HEAD)" in script
    assert (
        script.index("rev-parse FETCH_HEAD)")
        < script.index("sha256sum -c -")
        < script.index("npm ci")
    )
    assert "x-access-token:${" not in script
    assert doc["jobs"]["bootstrap"]["permissions"] == {
        "id-token": "write",
        "contents": "write",
    }


def test_bootstrap_dispatch_boundary_remains_separate() -> None:
    doc = yaml.safe_load(
        (ROOT / ".github/workflows/pipeline-bootstrap.yml").read_text()
    )
    events = doc.get("on", doc.get(True))
    assert set(events) == {"repository_dispatch", "workflow_dispatch"}
