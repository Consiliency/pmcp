"""Read-only legacy Docker run inventory; this report never authorizes activation.

No API event/status/date/search filter: filtered Actions listings can stop at1000.
All statuses other than literal completed remain blockers for non-PR runs.
With --baseline, the stable inventory is compared with a recorded one; any stop
is a detection event for the owner's incident path (D3). Instability after
bounded whole-inventory retries is incomplete (retake), never a stop.
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

RECORD_KEYS = (
    "id",
    "run_attempt",
    "head_sha",
    "event",
    "status",
    "conclusion",
    "created_at",
    "run_started_at",
    "head_branch",
)
# Compared per non-PR row; a change of any identity field always stops. The
# progress fields may advance only in pre-merge mode, on rows nonterminal in B0.
IDENTITY_KEYS = ("run_attempt", "head_sha", "event", "head_branch", "created_at")
PROGRESS_KEYS = ("status", "conclusion", "run_started_at")
PRE_START = frozenset({"requested", "pending", "waiting", "queued"})
RANK = {**{status: 0 for status in PRE_START}, "in_progress": 1, "completed": 2}
INVENTORY_ATTEMPTS = 3
RETENTION_SOURCE = (
    "repos/Consiliency/pmcp/actions/permissions/artifact-and-log-retention"
)
RETENTION_KEYS = frozenset(
    {"schema", "source", "read_at", "days", "maximum_allowed_days"}
)
# GitHub documents neither the age anchor nor the deletion schedule.
RETENTION_MARGIN_DAYS = 7


def parse_time(value: Any) -> datetime.datetime:
    """An aware UTC-offset ISO-8601 timestamp; anything else is refused."""
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("invalid timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp without offset")
    return parsed


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
                report["unstable"] = True
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
                if "run_started_at" not in row or "head_branch" not in row:
                    raise ValueError("missing run timing or branch")
                parse_time(row.get("created_at"))
                if row["run_started_at"] is not None:
                    parse_time(row["run_started_at"])
                branch = row["head_branch"]
                if branch is not None and (
                    not isinstance(branch, str) or not branch or len(branch) > 255
                ):
                    raise ValueError("invalid head branch")
                record = {key: row.get(key) for key in RECORD_KEYS}
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
        result["unstable"] = bool(first.get("unstable"))
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
        result["unstable"] = bool(second.get("unstable"))
        return result
    if sorted(first["runs"], key=lambda row: row["id"]) != sorted(
        second["runs"], key=lambda row: row["id"]
    ):
        result["unstable"] = True
        result["complete"] = False
        result["error"] = (
            "legacy run identities, attempts or results changed between observations; repeat the whole inventory"
        )
        return result
    result["stability_verified"] = True
    return result


def collect(
    read: Callable[[str], Any] | None = None, attempts: int = INVENTORY_ATTEMPTS
) -> dict[str, Any]:
    """Repeat the whole two-pass inventory while it is unstable, at most ``attempts``."""
    history: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        result = inventory() if read is None else inventory(read)
        history.append(
            {
                "attempt": attempt,
                "complete": result.get("complete"),
                "stability_verified": result.get("stability_verified"),
                "unstable": bool(result.get("unstable")),
            }
        )
        if not result.get("unstable"):
            break
    else:
        result["complete"] = False
        result["error"] = (
            f"inventory still unstable after {attempts} whole-inventory attempts; "
            "retake it (not a detection event)"
        )
    result["inventory_attempts"] = history
    return result


def load_retention(doc: Any) -> int:
    """Return the retention ``days`` from an exact owner-recorded settings read."""
    if not isinstance(doc, dict) or set(doc) != RETENTION_KEYS:
        raise ValueError("retention file fields are not exact")
    if type(doc["schema"]) is not int or doc["schema"] != 1:
        raise ValueError("retention file schema")
    if doc["source"] != RETENTION_SOURCE:
        raise ValueError("retention file source")
    parse_time(doc["read_at"])
    days, maximum = doc["days"], doc["maximum_allowed_days"]
    if type(days) is not int or type(maximum) is not int:
        raise ValueError("retention days must be integers")
    if not 8 <= days <= maximum <= 400:
        raise ValueError("retention days out of range")
    return days


def baseline_refusals(doc: Any, mode: str) -> list[str]:
    """Reasons a recorded inventory cannot serve as a baseline in ``mode``."""
    if not isinstance(doc, dict):
        return ["baseline is not an inventory document"]
    reasons = []
    if doc.get("complete") is not True:
        reasons.append("baseline is not complete")
    if doc.get("stability_verified") is not True:
        reasons.append("baseline stability was not verified")
    if type(doc.get("schema")) is not int or doc.get("schema") != 1:
        reasons.append("baseline schema is not 1")
    if doc.get("repository_id") != REPOSITORY_ID:
        reasons.append("baseline repository_id mismatch")
    if doc.get("workflow_id") != WORKFLOW_ID:
        reasons.append("baseline workflow_id mismatch")
    if doc.get("workflow_path") != WORKFLOW_PATH:
        reasons.append("baseline workflow_path mismatch")
    runs = doc.get("runs")
    if not isinstance(runs, list) or any(
        not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(RECORD_KEYS))
        for row in runs
    ):
        return reasons + ["baseline run records are not the current record shape"]
    ids = [row["id"] for row in runs]
    if any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
        reasons.append("baseline run IDs are invalid or duplicated")
    try:
        for row in runs:
            parse_time(row["created_at"])
    except ValueError:
        reasons.append("baseline created_at is invalid")
    if mode == "drained":
        blockers = [
            row
            for row in runs
            if row["event"] != "pull_request" and row["status"] != "completed"
        ]
        if doc.get("non_pr_not_completed") != [] or blockers:
            reasons.append("drained mode needs a baseline without non-PR blockers")
    return reasons


def _progress_allowed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Same-attempt progress of a row that was nonterminal in B0 (pre-merge only)."""
    old, new = before["status"], after["status"]
    if old == "completed" or old not in RANK or new not in RANK:
        return False
    if RANK[new] < RANK[old]:
        return False
    if new == "completed":
        if before["conclusion"] is not None or not isinstance(after["conclusion"], str):
            return False
    elif after["conclusion"] != before["conclusion"]:
        return False
    if after["run_started_at"] != before["run_started_at"] and old not in PRE_START:
        return False
    return True


def compare(
    baseline: dict[str, Any],
    current: dict[str, Any],
    mode: str,
    *,
    merge_time: datetime.datetime | None = None,
    retention_days: int | None = None,
) -> dict[str, Any]:
    """Diff a stable complete inventory against an accepted baseline."""
    before = {row["id"]: row for row in baseline["runs"]}
    after = {row["id"]: row for row in current["runs"]}
    started = parse_time(current["started_at"])
    stops: list[str] = []
    new_ids = sorted(set(after) - set(before))
    missing_ids = sorted(set(before) - set(after))
    expired_ids: list[int] = []
    changed: list[dict[str, Any]] = []
    for run_id in new_ids:
        row = after[run_id]
        if row["event"] != "pull_request":
            stops.append(f"new non-PR run {run_id}")
            if row["head_branch"] != "main":
                stops.append(f"new non-PR run {run_id} is not on main")
    horizon = None
    if retention_days is not None:
        horizon = datetime.timedelta(
            seconds=(retention_days - RETENTION_MARGIN_DAYS) * 86400
        )
    for run_id in missing_ids:
        age = started - parse_time(before[run_id]["created_at"])
        if horizon is not None and age >= horizon:
            expired_ids.append(run_id)
        else:
            stops.append(f"missing run {run_id}")
    for run_id in sorted(set(before) & set(after)):
        old, new = before[run_id], after[run_id]
        if old["event"] == "pull_request" and new["event"] == "pull_request":
            continue
        fields = [k for k in IDENTITY_KEYS + PROGRESS_KEYS if old[k] != new[k]]
        if not fields:
            continue
        entry = {
            "id": run_id,
            "fields": fields,
            "before": {k: old[k] for k in fields},
            "after": {k: new[k] for k in fields},
        }
        if any(k in IDENTITY_KEYS for k in fields):
            entry["stop"] = "identity changed"
        elif mode != "pre-merge":
            entry["stop"] = "drained mode allows no change"
        elif not _progress_allowed(old, new):
            entry["stop"] = "not an allowed same-attempt progress"
        if new["head_branch"] != "main":
            entry["stop"] = entry.get("stop", "changed run is not on main")
        if "stop" in entry:
            stops.append(f"run {run_id}: {entry['stop']}")
        changed.append(entry)
    if merge_time is not None:
        for run_id, row in sorted(after.items()):
            if row["event"] != "pull_request" and (
                parse_time(row["created_at"]) >= merge_time
            ):
                stops.append(f"non-PR run {run_id} created at or after the merge")
    return {
        "mode": mode,
        "new_ids": new_ids,
        "missing_ids": missing_ids,
        "expired_ids": expired_ids,
        "changed": changed,
        "stops": stops,
    }


def _load_json(path: Path) -> Any:
    with path.open("rb") as stream:
        data = stream.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("oversize input")
    return json.loads(data)


def run_comparison(
    baseline_path: Path,
    mode: str,
    *,
    merge_time: str | None = None,
    retention_path: Path | None = None,
    read: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Validate the inputs, collect a stable inventory and compare it."""
    refusal: dict[str, Any] = {
        "complete": False,
        "activation_allowed": False,
        "registry_writes": False,
        "non_pr_not_completed": [],
        "stops": [],
    }
    try:
        baseline = _load_json(baseline_path)
        retention_days = (
            load_retention(_load_json(retention_path))
            if retention_path is not None
            else None
        )
        merge = parse_time(merge_time) if merge_time is not None else None
    except (OSError, ValueError, TypeError):
        refusal["error"] = "baseline, retention or merge-time input refused"
        return refusal
    reasons = baseline_refusals(baseline, mode)
    if reasons:
        refusal["error"] = "baseline refused"
        refusal["baseline_refusals"] = reasons
        return refusal
    result = collect(read)
    result["baseline_collector_sha256"] = baseline.get("collector_sha256")
    result["collector_sha256_matches"] = baseline.get("collector_sha256") == result.get(
        "collector_sha256"
    )
    result["stops"] = []
    if result.get("complete") and result.get("stability_verified"):
        result.update(
            compare(
                baseline,
                result,
                mode,
                merge_time=merge,
                retention_days=retention_days,
            )
        )
        result["retention_days"] = retention_days
    result["activation_allowed"] = False
    return result


def exit_code(result: dict[str, Any]) -> int:
    return (
        0
        if result.get("complete")
        and not result.get("non_pr_not_completed")
        and not result.get("stops")
        else 1
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--mode", choices=("pre-merge", "drained"))
    parser.add_argument("--merge-time")
    parser.add_argument("--retention-file", type=Path)
    args = parser.parse_args(argv)
    if (args.baseline is None) != (args.mode is None):
        parser.error("--baseline and --mode go together")
    if args.baseline is None and (args.merge_time or args.retention_file):
        parser.error("--merge-time and --retention-file need --baseline")
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
    if args.baseline is None:
        result = collect()
    else:
        result = run_comparison(
            args.baseline,
            args.mode,
            merge_time=args.merge_time,
            retention_path=args.retention_file,
        )
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    summary = ["complete", "activation_allowed", "non_pr_not_completed"]
    if args.baseline is not None:
        summary += ["new_ids", "missing_ids", "expired_ids", "stops"]
    print(json.dumps({key: result.get(key) for key in summary}))
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
