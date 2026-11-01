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
        "created_at": "2026-10-01T00:00:00Z",
        "run_started_at": "2026-10-01T00:00:05Z",
        "head_branch": "main",
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
    expected = {k: record[k] for k in inv.RECORD_KEYS}
    assert set(expected) >= {"created_at", "run_started_at", "head_branch"}
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


# --- T3: baseline comparison (#443 items 2 and 3) ---------------------------

STARTED = "2026-12-23T00:00:00+00:00"
MERGE = "2026-10-15T00:00:00+00:00"


def run_row(index: int, **fields: Any) -> dict[str, Any]:
    record = row(index)
    record.update(conclusion="success")
    record.update(fields)
    return record


def stable_doc(rows: list[dict[str, Any]], started_at: str = STARTED) -> Any:
    result = inv.inventory(fixture_read(rows))
    assert result["complete"] and result["stability_verified"]
    result["started_at"] = started_at
    return result


def diff(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    mode: str = "pre-merge",
    **kwargs: Any,
) -> Any:
    baseline = stable_doc(before)
    assert inv.baseline_refusals(baseline, mode) == []
    return inv.compare(baseline, stable_doc(after), mode, **kwargs)


def test_full_drain_sequence_is_accepted() -> None:
    b0_rows = [run_row(1, status="in_progress", conclusion=None), run_row(2)]
    b0 = stable_doc(b0_rows)
    scan = stable_doc([dict(r) for r in b0_rows])
    first = inv.compare(b0, scan, "pre-merge", merge_time=inv.parse_time(MERGE))
    assert first["stops"] == [] and first["changed"] == []
    assert scan["non_pr_not_completed"]  # still draining, but no stop
    b1 = stable_doc([run_row(1, status="completed"), run_row(2)])
    second = inv.compare(b0, b1, "pre-merge", merge_time=inv.parse_time(MERGE))
    assert second["stops"] == [] and [c["id"] for c in second["changed"]] == [1]
    assert inv.baseline_refusals(b1, "drained") == []


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            {"status": "queued", "conclusion": None, "run_started_at": None},
            {"status": "in_progress", "conclusion": None},
        ),
        (
            {"status": "queued", "conclusion": None, "run_started_at": None},
            {"status": "completed", "conclusion": "success"},
        ),
        (
            {"status": "requested", "conclusion": None, "run_started_at": None},
            {"status": "completed", "conclusion": "success"},
        ),
        (
            {"status": "requested", "conclusion": None, "run_started_at": None},
            {"status": "queued", "conclusion": None, "run_started_at": None},
        ),
    ],
)
def test_allowed_same_attempt_progress(
    before: dict[str, Any], after: dict[str, Any]
) -> None:
    result = diff([run_row(1, **before)], [run_row(1, **after)])
    assert result["stops"] == [] and result["changed"]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ({"status": "completed"}, {"status": "in_progress", "conclusion": None}),
        ({"status": "completed"}, {"conclusion": "failure"}),
        (
            {"status": "in_progress", "conclusion": None},
            {"status": "queued", "conclusion": None},
        ),
        (
            {"status": "in_progress", "conclusion": None},
            {"status": "in_progress", "conclusion": None, "run_started_at": None},
        ),
        (
            {"status": "future_platform_status", "conclusion": None},
            {"status": "completed", "conclusion": "success"},
        ),
        (
            {"status": "queued", "conclusion": None},
            {"status": "in_progress", "conclusion": "success"},
        ),
    ],
)
def test_other_changes_on_a_non_pr_row_stop(
    before: dict[str, Any], after: dict[str, Any]
) -> None:
    result = diff([run_row(1, **before)], [run_row(1, **after)])
    assert result["stops"]


def test_an_unchanged_unknown_status_stays_a_blocker_without_a_stop() -> None:
    rows = [run_row(1, status="future_platform_status", conclusion=None)]
    result = diff(rows, [dict(r) for r in rows])
    assert result["stops"] == []


@pytest.mark.parametrize(
    "field",
    ["run_attempt", "head_sha", "event", "head_branch", "created_at"],
)
@pytest.mark.parametrize("status", ["completed", "queued"])
def test_an_identity_change_stops(field: str, status: str) -> None:
    before = run_row(1, status=status, conclusion="success" if status else None)
    if status == "queued":
        before["conclusion"] = None
    after = dict(before)
    after[field] = {
        "run_attempt": 3,
        "head_sha": "b" * 40,
        "event": "workflow_dispatch",
        "head_branch": "release",
        "created_at": "2026-10-01T00:00:01Z",
    }[field]
    result = diff([before], [after])
    assert any("identity changed" in stop for stop in result["stops"])


def test_a_rerun_of_a_b0_row_stops() -> None:
    # Negative control for an attempt change: a rerun keeps the ID.
    result = diff(
        [run_row(1)], [run_row(1, run_attempt=3, status="queued", conclusion=None)]
    )
    assert result["stops"]


def test_drained_mode_allows_no_change_on_a_non_pr_row() -> None:
    result = diff(
        [run_row(1)],
        [run_row(1, run_started_at="2026-10-01T00:00:09Z")],
        mode="drained",
    )
    assert result["stops"]


def test_a_progressed_row_off_main_stops() -> None:
    result = diff(
        [run_row(1, status="queued", conclusion=None, head_branch="release")],
        [run_row(1, status="completed", head_branch="release")],
    )
    assert result["stops"]


def test_deletion_stops_and_new_pr_ids_only_report() -> None:
    result = diff(
        [run_row(1), run_row(2)],
        [run_row(1), run_row(3, event="pull_request")],
    )
    assert result["missing_ids"] == [2] and result["new_ids"] == [3]
    assert result["stops"] == ["missing run 2"]


def test_a_missing_pr_row_also_stops() -> None:
    result = diff([run_row(1), run_row(2, event="pull_request")], [run_row(1)])
    assert result["stops"] == ["missing run 2"]


def test_creation_at_or_after_the_merge_stops() -> None:
    late = run_row(2, created_at=MERGE)
    result = diff([run_row(1)], [run_row(1), late], merge_time=inv.parse_time(MERGE))
    assert "new non-PR run 2" in result["stops"]
    assert "non-PR run 2 created at or after the merge" in result["stops"]
    existing = run_row(1, created_at="2026-10-16T00:00:00Z")
    result = diff([existing], [dict(existing)], merge_time=inv.parse_time(MERGE))
    assert result["stops"] == ["non-PR run 1 created at or after the merge"]


def test_a_new_non_pr_run_stops_even_on_main() -> None:
    result = diff([run_row(1)], [run_row(1), run_row(2, status="queued")])
    assert "new non-PR run 2" in result["stops"]


def test_issue_443_delete_plus_new_queued_run_with_the_same_total() -> None:
    result = diff(
        [run_row(1), run_row(2)],
        [run_row(1), run_row(3, status="queued", conclusion=None)],
    )
    assert "missing run 2" in result["stops"] and "new non-PR run 3" in result["stops"]


def _write(tmp_path: Path, name: str, doc: Any) -> Path:
    import json

    path = tmp_path / name
    path.write_text(json.dumps(doc))
    return path


def test_issue_443_pass_two_rerun_repeats_then_stops(tmp_path: Path) -> None:
    baseline = _write(tmp_path, "b0.json", stable_doc([run_row(1)]))
    passes = 0
    rerun = [run_row(1, run_attempt=3, status="queued", conclusion=None)]

    def read(endpoint: str) -> Any:
        nonlocal passes
        if endpoint.endswith(str(inv.WORKFLOW_ID)):
            passes += 1
        return fixture_read([run_row(1)] if passes == 1 else rerun)(endpoint)

    result = inv.run_comparison(baseline, "pre-merge", read=read)
    assert [a["unstable"] for a in result["inventory_attempts"]] == [True, False]
    assert result["complete"] and any("identity" in s for s in result["stops"])
    assert inv.exit_code(result) == 1 and result["activation_allowed"] is False


def test_three_unstable_attempts_are_incomplete_and_never_a_stop(
    tmp_path: Path,
) -> None:
    baseline = _write(tmp_path, "b0.json", stable_doc([run_row(1)]))
    passes = 0

    def read(endpoint: str) -> Any:
        nonlocal passes
        if endpoint.endswith(str(inv.WORKFLOW_ID)):
            passes += 1
        status = "queued" if passes % 2 else "in_progress"
        return fixture_read(
            [
                run_row(1),
                run_row(2, event="pull_request", status=status, conclusion=None),
            ]
        )(endpoint)

    result = inv.run_comparison(baseline, "pre-merge", read=read)
    assert len(result["inventory_attempts"]) == 3
    assert result["complete"] is False and result["stops"] == []
    assert "unstable after 3" in result["error"] and inv.exit_code(result) == 1


def test_a_baseline_with_blockers_is_refused_in_drained_mode(tmp_path: Path) -> None:
    b0 = stable_doc([run_row(1, status="in_progress", conclusion=None)])
    assert inv.baseline_refusals(b0, "pre-merge") == []
    path = _write(tmp_path, "b0.json", b0)

    def unreachable(endpoint: str) -> Any:
        raise AssertionError("a refused baseline must not start an inventory")

    result = inv.run_comparison(path, "drained", read=unreachable)
    assert result["complete"] is False and result["stops"] == []
    assert result["baseline_refusals"]


@pytest.mark.parametrize(
    "mutation",
    ["incomplete", "unstable", "schema", "repository", "workflow", "path", "shape"],
)
def test_baseline_acceptance_is_mandatory(mutation: str) -> None:
    b0 = stable_doc([run_row(1)])
    if mutation == "incomplete":
        b0["complete"] = False
    elif mutation == "unstable":
        b0["stability_verified"] = False
    elif mutation == "schema":
        b0["schema"] = True
    elif mutation == "repository":
        b0["repository_id"] = 1
    elif mutation == "workflow":
        b0["workflow_id"] = 1
    elif mutation == "path":
        b0["workflow_path"] = ".github/workflows/image.yml"
    else:
        b0["runs"][0].pop("head_branch")
    assert inv.baseline_refusals(b0, "pre-merge")


def test_a_collector_mismatch_is_reported_not_refused(tmp_path: Path) -> None:
    b0 = stable_doc([run_row(1)])
    b0["collector_sha256"] = "0" * 64
    result = inv.run_comparison(
        _write(tmp_path, "b0.json", b0), "drained", read=fixture_read([run_row(1)])
    )
    assert result["collector_sha256_matches"] is False and result["stops"] == []
    assert inv.exit_code(result) == 0


# --- T3 retention horizon (president RE-1b) ---------------------------------

DAY = 86400


def retention(**overrides: Any) -> dict[str, Any]:
    doc = {
        "schema": 1,
        "source": "repos/Consiliency/pmcp/actions/permissions/artifact-and-log-retention",
        "read_at": "2026-11-01T00:00:00Z",
        "days": 90,
        "maximum_allowed_days": 90,
    }
    doc.update(overrides)
    return doc


def created(seconds_before_start: int) -> str:
    import datetime

    start = inv.parse_time(STARTED)
    return (start - datetime.timedelta(seconds=seconds_before_start)).isoformat()


def retention_run(
    tmp_path: Path,
    baseline_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    *,
    retention_doc: Any = None,
    flag: bool = True,
) -> Any:
    baseline = _write(tmp_path, "b1.json", stable_doc(baseline_rows))
    current = stable_doc(current_rows)
    monkeypatch.setattr(inv, "collect", lambda read=None: dict(current))
    path = (
        _write(tmp_path, "retention.json", retention_doc or retention())
        if flag
        else None
    )
    return inv.run_comparison(baseline, "drained", retention_path=path)


def test_a_row_exactly_at_the_horizon_is_expired_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = run_row(1, created_at=created(83 * DAY))
    result = retention_run(tmp_path, [old, run_row(2)], [run_row(2)], monkeypatch)
    assert result["expired_ids"] == [1] and result["missing_ids"] == [1]
    assert result["stops"] == [] and inv.exit_code(result) == 0


def test_a_row_one_second_younger_than_the_horizon_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    young = run_row(1, created_at=created(83 * DAY - 1))
    result = retention_run(tmp_path, [young, run_row(2)], [run_row(2)], monkeypatch)
    assert result["expired_ids"] == [] and result["stops"] == ["missing run 1"]
    assert inv.exit_code(result) == 1


def test_without_the_flag_every_missing_row_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = run_row(1, created_at=created(83 * DAY))
    result = retention_run(
        tmp_path, [old, run_row(2)], [run_row(2)], monkeypatch, flag=False
    )
    assert result["expired_ids"] == [] and result["stops"] == ["missing run 1"]


def test_a_young_missing_row_stops_while_older_rows_expire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        run_row(1, created_at=created(200 * DAY)),
        run_row(2, created_at=created(90 * DAY), event="pull_request"),
        run_row(3, created_at=created(10 * DAY)),
        run_row(4),
    ]
    result = retention_run(tmp_path, rows, [run_row(4)], monkeypatch)
    assert result["expired_ids"] == [1, 2]
    assert result["stops"] == ["missing run 3"]


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "repos/Consiliency/pmcp/actions/permissions"},
        {"days": 7},
        {"days": 91},
        {"days": 400, "maximum_allowed_days": 401},
        {"schema": 2},
        {"days": "90"},
        {"read_at": "yesterday"},
        {"extra": 1},
    ],
)
def test_an_invalid_retention_file_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: dict[str, Any]
) -> None:
    result = retention_run(
        tmp_path,
        [run_row(1)],
        [run_row(1)],
        monkeypatch,
        retention_doc=retention(**bad),
    )
    assert result["complete"] is False and result["stops"] == []
    assert inv.exit_code(result) == 1


def test_cli_compares_against_a_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    baseline = _write(tmp_path, "b1.json", stable_doc([run_row(1), run_row(2)]))
    current = stable_doc([run_row(1)])
    monkeypatch.setattr(inv, "collect", lambda read=None: dict(current))
    output = tmp_path / "out.json"
    code = inv.main(
        ["--output", str(output), "--baseline", str(baseline), "--mode", "drained"]
    )
    report = json.loads(output.read_text())
    assert code == 1 and report["stops"] == ["missing run 2"]
    assert report["activation_allowed"] is False
