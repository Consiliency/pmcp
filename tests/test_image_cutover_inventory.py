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
