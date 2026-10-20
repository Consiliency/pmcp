"""Exercise the ACTUAL embedded bootstrap with same-run GitHub result fixtures."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
spec = importlib.util.spec_from_file_location(
    "full_contract", ROOT / ".github/ci-policy/full_contract.py"
)
assert spec and spec.loader
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def embedded(job: str) -> str:
    code = WORKFLOW["jobs"][job]["steps"][0]["run"]
    assert code.startswith("python3 -I -B - <<'PY'\n") and code.endswith("PY\n")
    return code.split("\n", 1)[1].rsplit("\nPY", 1)[0]


@pytest.fixture
def binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for key, value in {
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY_ID": "456",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "FORCE_FULL": "",
    }.items():
        monkeypatch.setenv(key, value)
    return {job: {"result": "success"} for job in contract.FULL_JOBS}


def evaluate(
    needs: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any] | None = None,
) -> None:
    output = Path(os.environ["GITHUB_OUTPUT"])
    exec(compile(embedded("ci-scope"), "<scope>", "exec"), {})
    decision = json.loads(output.read_text().split("decision=")[-1])
    if change:
        decision.update(change)
    monkeypatch.setenv("SCOPE_DECISION", json.dumps(decision))
    monkeypatch.setenv("NEEDS_JSON", json.dumps(needs))
    exec(compile(embedded("ci-required"), "<gate>", "exec"), {})


def test_full_bootstrap_passes_without_checkout_or_policy(
    binding: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluate(binding, monkeypatch)
    jobs = WORKFLOW["jobs"]
    assert "needs" not in jobs["test"] and "if" not in jobs["test"]
    assert jobs["ci-required"]["if"] == "always()"
    assert jobs["ci-required"]["needs"] == list(contract.FULL_JOBS)
    assert all(
        "uses" not in s for j in ("ci-scope", "ci-required") for s in jobs[j]["steps"]
    )


@pytest.mark.parametrize("job", contract.FULL_JOBS)
@pytest.mark.parametrize(
    "result", ["failure", "skipped", "cancelled", "neutral", "", "unknown"]
)
def test_every_unsatisfied_pr_job_is_red(
    binding: dict[str, Any], monkeypatch: pytest.MonkeyPatch, job: str, result: str
) -> None:
    binding[job]["result"] = result
    assert not contract.validate_results("pull_request", binding)
    with pytest.raises(AssertionError):
        evaluate(binding, monkeypatch)


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
def test_only_enumerated_non_pr_skips_apply(
    binding: dict[str, Any], monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    for job in contract.PR_ONLY:
        binding[job]["result"] = "skipped"
    assert contract.validate_results(event, binding)
    evaluate(binding, monkeypatch)
    binding["audit"]["result"] = "failure"
    with pytest.raises(AssertionError):
        evaluate(binding, monkeypatch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "ASSET_V1"),
        ("proof_valid", True),
        ("schema", "unknown"),
        ("identity", {}),
        ("reason", "asset"),
    ],
)
def test_forged_decision_never_passes(
    binding: dict[str, Any], monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    with pytest.raises(AssertionError):
        evaluate(binding, monkeypatch, {field: value})


def test_restriction_remains_full_and_cannot_waive_a_red_suite(
    binding: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_FULL", "anything")
    evaluate(binding, monkeypatch)
    binding["test"]["result"] = "failure"
    with pytest.raises(AssertionError):
        evaluate(binding, monkeypatch)


def test_missing_dependencies_and_unknown_events_fail(
    binding: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = copy.deepcopy(binding)
    missing.pop("test")
    assert not contract.validate_results("pull_request", missing)
    with pytest.raises(AssertionError):
        evaluate(missing, monkeypatch)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "merge_group")
    assert not contract.validate_results("merge_group", binding)
    with pytest.raises(AssertionError):
        evaluate(binding, monkeypatch)


def test_stale_attempt_names_full_rerun_recovery(
    binding: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssertionError, match="re-run all jobs"):
        evaluate(binding, monkeypatch, {"identity": {}})
