"""Incomplete, changing or nonterminal legacy histories cannot certify a drain."""

from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import Any
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "inventory", ROOT / "scripts/image_cutover_inventory.py"
)
assert spec and spec.loader
inv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inv)


def fixture_read(
    rows: list[dict[str, Any]],
    *,
    total: int | None = None,
    wrong_identity: bool = False,
):
    def read(endpoint: str) -> Any:
        if endpoint == f"repos/{inv.REPOSITORY}":
            return {"id": inv.REPOSITORY_ID}
        if "?" not in endpoint:
            return {
                "id": inv.WORKFLOW_ID,
                "path": "wrong" if wrong_identity else inv.WORKFLOW_PATH,
            }
        assert (
            "event=" not in endpoint
            and "status=" not in endpoint
            and "created=" not in endpoint
        )
        page = int(endpoint.rsplit("page=", 1)[1])
        return {
            "total_count": len(rows) if total is None else total,
            "workflow_runs": rows[(page - 1) * 100 : page * 100],
        }

    return read


def row(
    index: int, *, status: str = "completed", event: str = "push"
) -> dict[str, Any]:
    return {
        "id": index,
        "workflow_id": inv.WORKFLOW_ID,
        "repository": {"id": inv.REPOSITORY_ID},
        "run_attempt": 2,
        "head_sha": "a" * 40,
        "status": status,
        "event": event,
        "conclusion": "cancelled" if status == "completed" else None,
    }


def test_unfiltered_inventory_exceeds_a_thousand() -> None:
    result = inv.inventory(fixture_read([row(i) for i in range(1, 1092)]))
    assert (
        result["complete"]
        and len(result["runs"]) == 1091
        and len(result["pages"]) == 11
    )
    assert not result["activation_allowed"] and not result["registry_writes"]


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "pending",
        "waiting",
        "requested",
        "in_progress",
        "future_platform_status",
    ],
)
def test_every_nonterminal_non_pr_status_blocks(status: str) -> None:
    result = inv.inventory(fixture_read([row(1, status=status)]))
    assert result["complete"] and result["non_pr_not_completed"][0]["status"] == status
    assert not result["activation_allowed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "truncated",
        "duplicate",
        "missing-status",
        "wrong-workflow",
        "wrong-repository",
        "wrong-attempt",
        "wrong-sha",
        "wrong-identity",
    ],
)
def test_incomplete_or_ambiguous_evidence_fails_closed(mutation: str) -> None:
    rows = [row(1)]
    if mutation == "duplicate":
        rows.append(row(1))
    if mutation == "missing-status":
        rows[0].pop("status")
    if mutation == "wrong-workflow":
        rows[0]["workflow_id"] = 1
    if mutation == "wrong-repository":
        rows[0]["repository"] = {"id": 1}
    if mutation == "wrong-attempt":
        rows[0]["run_attempt"] = None
    if mutation == "wrong-sha":
        rows[0]["head_sha"] = "x" * 40
    result = inv.inventory(
        fixture_read(
            rows,
            total=1001 if mutation == "truncated" else None,
            wrong_identity=mutation == "wrong-identity",
        )
    )
    assert (
        not result["complete"]
        and not result["activation_allowed"]
        and "error" in result
    )


def test_prs_are_enumerated_then_excluded_from_drain() -> None:
    result = inv.inventory(
        fixture_read([row(1, status="in_progress", event="pull_request"), row(2)])
    )
    assert (
        result["complete"]
        and len(result["runs"]) == 2
        and not result["non_pr_not_completed"]
    )


@pytest.mark.parametrize("value", [None, {}, 0, False, "", " ", [], "x" * 129])
def test_terminal_conclusion_must_be_actual_and_usable(value: Any) -> None:
    record = row(1)
    record["conclusion"] = value
    assert not inv.inventory(fixture_read([record]))["complete"]


def test_conclusion_must_be_present() -> None:
    record = row(1)
    record.pop("conclusion")
    assert not inv.inventory(fixture_read([record]))["complete"]


def test_recorded_identity_contains_actual_sha_and_conclusion() -> None:
    record = row(1)
    result = inv.inventory(fixture_read([record]))
    expected = {
        k: record[k]
        for k in ("id", "run_attempt", "head_sha", "event", "status", "conclusion")
    }
    assert result["runs"] == [expected] and result["stability_verified"]
    assert len(result["verification_passes"]) == 2
    assert all(p["runs"] == [expected] for p in result["verification_passes"])


@pytest.mark.parametrize("mutation", ["rerun", "same-total-new-run", "result-changed"])
def test_two_passes_detect_changes_even_with_same_total(mutation: str) -> None:
    passes = 0
    first = [row(1)]
    second = [row(1)]
    if mutation == "rerun":
        second[0].update(run_attempt=3, status="queued", conclusion=None)
    elif mutation == "same-total-new-run":
        second = [row(2, status="queued")]
    else:
        second[0]["conclusion"] = "failure"

    def read(endpoint: str) -> Any:
        nonlocal passes
        if endpoint.endswith(str(inv.WORKFLOW_ID)):
            passes += 1
        return fixture_read(first if passes == 1 else second)(endpoint)

    result = inv.inventory(read)
    assert not result["complete"] and not result["stability_verified"]
    assert len(result["verification_passes"]) == 2
    assert not result["activation_allowed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "changed-total",
        "root-repository",
        "overfull-page",
        "empty-event",
        "negative-count",
        "reader-failure",
    ],
)
def test_response_failures_cannot_become_complete(mutation: str) -> None:
    rows = [row(i) for i in range(1, 102)]
    if mutation == "empty-event":
        rows[0]["event"] = ""
    valid = fixture_read(rows)

    def read(endpoint: str) -> Any:
        if mutation == "reader-failure":
            raise OSError("private-reader-canary")
        data = valid(endpoint)
        if mutation == "root-repository" and endpoint == f"repos/{inv.REPOSITORY}":
            data["id"] = 1
        if "?" in endpoint:
            if mutation == "changed-total" and endpoint.endswith("page=2"):
                data["total_count"] += 1
            if mutation == "overfull-page":
                data["workflow_runs"] = rows if endpoint.endswith("page=1") else []
            if mutation == "negative-count":
                data["total_count"] = -1
        return data

    result = inv.inventory(read)
    assert not result["complete"] and not result["activation_allowed"]
    import json

    assert "private-reader-canary" not in json.dumps(result)


def test_cli_refuses_existing_output_and_marks_crash_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import sys

    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["inventory", "--output", str(output)])

    def fail() -> Any:
        raise RuntimeError("simulated collector crash")

    monkeypatch.setattr(inv, "inventory", fail)
    with pytest.raises(RuntimeError):
        inv.main()
    assert json.loads(output.read_text())["complete"] is False
    output.write_text('{"complete": true, "old": true}')
    with pytest.raises(FileExistsError):
        inv.main()
    assert json.loads(output.read_text())["old"] is True


def test_cli_incomplete_evidence_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setattr(
        sys, "argv", ["inventory", "--output", str(tmp_path / "report.json")]
    )
    monkeypatch.setattr(
        inv,
        "inventory",
        lambda: {
            "complete": False,
            "activation_allowed": False,
            "non_pr_not_completed": [],
        },
    )
    assert inv.main() == 1


# --- CLR1-01: the exit predicate requires an empty non-PR blocker list ------


@pytest.mark.parametrize("status", ["in_progress", "future_platform_status"])
def test_cli_complete_inventory_with_a_non_pr_blocker_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    import sys

    blocker = {
        "id": 7,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "event": "push",
        "status": status,
        "conclusion": None,
    }
    monkeypatch.setattr(
        sys, "argv", ["inventory", "--output", str(tmp_path / "report.json")]
    )
    monkeypatch.setattr(
        inv,
        "inventory",
        lambda: {
            "complete": True,
            "activation_allowed": False,
            "runs": [blocker],
            "non_pr_not_completed": [blocker],
        },
    )
    assert inv.main() == 1


def test_cli_complete_inventory_without_blockers_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setattr(
        sys, "argv", ["inventory", "--output", str(tmp_path / "report.json")]
    )
    monkeypatch.setattr(
        inv,
        "inventory",
        lambda: {
            "complete": True,
            "activation_allowed": False,
            "runs": [],
            "non_pr_not_completed": [],
        },
    )
    assert inv.main() == 0


# --- CLR1-02: the reader is a bounded, GET-only gh api call -----------------

INCOMPLETE_TEXT = "inventory incomplete; all new registry writes must remain disabled"


def test_reader_argv_is_exactly_a_get_with_bounded_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import subprocess

    calls: list[tuple[Any, dict[str, Any]]] = []
    rows = [row(1)]
    valid = fixture_read(rows)

    def fake(argv: Any, **kwargs: Any) -> bytes:
        calls.append((argv, kwargs))
        return json.dumps(valid(argv[2])).encode()

    monkeypatch.setattr(subprocess, "check_output", fake)
    result = inv.inventory()
    assert result["complete"] and calls
    for argv, kwargs in calls:
        assert type(argv) is list and argv[:2] == ["gh", "api"] and len(argv) == 3
        assert not argv[2].startswith("-")
        assert kwargs == {"timeout": 60, "stderr": subprocess.PIPE}
        joined = " ".join(argv)
        for flag in ("--method", "-X", "--field", "-f", "-F", "--input", "--paginate"):
            assert flag not in argv and f" {flag}" not in joined


@pytest.mark.parametrize(
    "failure", ["oversize", "invalid-json", "called-process-error", "timeout"]
)
def test_reader_failures_are_incomplete_with_the_fixed_text(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import subprocess

    def fake(argv: Any, **kwargs: Any) -> bytes:
        if failure == "oversize":
            return b" " * (16 * 1024 * 1024 + 1)
        if failure == "invalid-json":
            return b"{not json"
        if failure == "called-process-error":
            raise subprocess.CalledProcessError(1, argv, stderr=b"private-canary")
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(subprocess, "check_output", fake)
    result = inv.inventory()
    assert result["complete"] is False
    assert result["error"] == INCOMPLETE_TEXT
    assert not result["activation_allowed"]


# --- CLR1-05: PR rows count toward stability but never block a drain --------


def test_a_pr_row_changing_between_passes_is_unstable() -> None:
    passes = 0
    first = [row(1, status="in_progress", event="pull_request"), row(2)]
    second = [row(1, event="pull_request"), row(2)]

    def read(endpoint: str) -> Any:
        nonlocal passes
        if endpoint.endswith(str(inv.WORKFLOW_ID)):
            passes += 1
        return fixture_read(first if passes == 1 else second)(endpoint)

    result = inv.inventory(read)
    assert result["stability_verified"] is False and result["complete"] is False


def test_a_stable_nonterminal_pr_row_is_kept_but_never_blocks() -> None:
    pr_row = row(1, status="queued", event="pull_request")
    result = inv.inventory(fixture_read([pr_row, row(2)]))
    assert result["complete"] and result["stability_verified"]
    assert 1 in {record["id"] for record in result["runs"]}
    assert 1 not in {record["id"] for record in result["non_pr_not_completed"]}


# --- #443 item 4: the listing endpoint is exact and unfiltered --------------


def test_listing_endpoint_is_the_exact_unfiltered_workflow_run_page() -> None:
    import re

    seen: list[str] = []
    valid = fixture_read([row(i) for i in range(1, 151)])

    def read(endpoint: str) -> Any:
        seen.append(endpoint)
        return valid(endpoint)

    assert inv.inventory(read)["complete"]
    listings = [e for e in seen if "?" in e]
    assert listings
    pattern = re.compile(
        r"repos/Consiliency/pmcp/actions/workflows/219424499/runs\?per_page=100&page=[1-9][0-9]*"
    )
    assert all(pattern.fullmatch(e) for e in listings), listings
    assert [int(e.rsplit("=", 1)[1]) for e in listings] == [1, 2, 1, 2]
