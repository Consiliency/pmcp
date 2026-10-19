"""Read-only legacy Docker run inventory; this report never authorizes activation.

No API event/status/date/search filter: filtered Actions listings can stop at1000.
All statuses other than literal completed remain blockers for non-PR runs.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

REPOSITORY = "Consiliency/pmcp"
REPOSITORY_ID = 1123923171
WORKFLOW_ID = 219424499
WORKFLOW_PATH = ".github/workflows/docker.yml"


def read_api(endpoint: str) -> Any:
    output = subprocess.check_output(
        ["gh", "api", endpoint], timeout=60, stderr=subprocess.PIPE
    )
    if len(output) > 16 * 1024 * 1024:
        raise ValueError("oversize GitHub response")
    return json.loads(output)


def _inventory_pass(read: Callable[[str], Any] = read_api) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": 1,
        "standing": "READ_ONLY_INVENTORY_ONLY",
        "activation_allowed": False,
        "registry_writes": False,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "workflow_id": WORKFLOW_ID,
        "workflow_path": WORKFLOW_PATH,
        "complete": False,
        "pages": [],
        "runs": [],
        "non_pr_not_completed": [],
    }
    try:
        root = f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW_ID}"
        identity = read(root)
        if (
            not isinstance(identity, dict)
            or identity.get("id") != WORKFLOW_ID
            or identity.get("path") != WORKFLOW_PATH
        ):
            raise ValueError("workflow identity mismatch")
        repository = read(f"repos/{REPOSITORY}")
        if not isinstance(repository, dict) or repository.get("id") != REPOSITORY_ID:
            raise ValueError("repository identity mismatch")
        seen: set[int] = set()
        total = None
        for page in range(1, 10001):
            endpoint = f"{root}/runs?per_page=100&page={page}"
            data = read(endpoint)
            if not isinstance(data, dict):
                raise ValueError("invalid run page")
            count = data.get("total_count")
            rows = data.get("workflow_runs")
            if (
                type(count) is not int
                or count < 0
                or not isinstance(rows, list)
                or len(rows) > 100
            ):
                raise ValueError("invalid run page")
            if total is None:
                total = count
            if count != total:
                raise ValueError(
                    "listing changed during enumeration; repeat the whole inventory"
                )
            report["pages"].append(
                {"endpoint": endpoint, "total_count": count, "rows": len(rows)}
            )
            for row in rows:
                if not isinstance(row, dict) or not isinstance(
                    row.get("repository"), dict
                ):
                    raise ValueError("invalid run entry")
                run_id = row.get("id")
                if type(run_id) is not int or run_id <= 0 or run_id in seen:
                    raise ValueError("duplicate or invalid run identity")
                seen.add(run_id)
                if (
                    row.get("workflow_id") != WORKFLOW_ID
                    or row.get("repository", {}).get("id") != REPOSITORY_ID
                ):
                    raise ValueError("run source identity mismatch")
                if (
                    not isinstance(row.get("status"), str)
                    or not row["status"]
                    or not isinstance(row.get("event"), str)
                    or not row["event"]
                ):
                    raise ValueError("missing actual run status or event")
                if type(row.get("run_attempt")) is not int or row["run_attempt"] < 1:
                    raise ValueError("missing run attempt")
                if (
                    not isinstance(row.get("head_sha"), str)
                    or len(row["head_sha"]) != 40
                    or any(c not in "0123456789abcdef" for c in row["head_sha"])
                ):
                    raise ValueError("invalid source SHA")
                if "conclusion" not in row:
                    raise ValueError("missing actual conclusion")
                conclusion = row["conclusion"]
                if conclusion is not None and (
                    not isinstance(conclusion, str)
                    or not conclusion.strip()
                    or len(conclusion) > 128
                ):
                    raise ValueError("invalid actual conclusion")
                if row["status"] == "completed" and conclusion is None:
                    raise ValueError("missing terminal conclusion")
                record = {
                    key: row.get(key)
                    for key in (
                        "id",
                        "run_attempt",
                        "head_sha",
                        "event",
                        "status",
                        "conclusion",
                    )
                }
                report["runs"].append(record)
                if row["event"] != "pull_request" and row["status"] != "completed":
                    report["non_pr_not_completed"].append(record)
            if len(rows) < 100:
                if len(seen) != total:
                    raise ValueError("incomplete run enumeration")
                report["complete"] = True
                break
        else:
            raise ValueError("run pagination limit exceeded")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        # Fixed refusal; no raw API body, command output, token or exception text.
        report["error"] = (
            "inventory incomplete; all new registry writes must remain disabled"
        )
    report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return report


def inventory(read: Callable[[str], Any] = read_api) -> dict[str, Any]:
    """Require two back-to-back identical full observations, never an atomic snapshot."""
    first = _inventory_pass(read)
    result = dict(first)
    result["verification_passes"] = [first]
    result["stability_verified"] = False
    result["collector_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if not first["complete"]:
        return result
    second = _inventory_pass(read)
    result = dict(second)
    result.update(
        verification_passes=[first, second],
        stability_verified=False,
        collector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        started_at=first["started_at"],
    )
    if not second["complete"]:
        return result
    if sorted(first["runs"], key=lambda row: row["id"]) != sorted(
        second["runs"], key=lambda row: row["id"]
    ):
        result["complete"] = False
        result["error"] = (
            "legacy run identities, attempts or results changed between observations; repeat the whole inventory"
        )
        return result
    result["stability_verified"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # A previous report must never masquerade as this invocation after a crash.
    with args.output.open("x") as stream:
        json.dump(
            {
                "complete": False,
                "activation_allowed": False,
                "registry_writes": False,
                "standing": "INVENTORY_STARTED_INCOMPLETE",
                "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
            stream,
        )
    result = inventory()
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("complete", "activation_allowed", "non_pr_not_completed")
            }
        )
    )
    return 0 if result["complete"] and not result["non_pr_not_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
