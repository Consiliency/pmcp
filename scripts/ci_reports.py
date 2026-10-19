"""Build public diagnostic artifacts from an explicit, bounded allowlist.

Never copy text, names, properties, paths or messages from a test report. Raw
reports deliberately remain local: security assertions can contain credentials.
This evidence describes a run; it is never authorization to skip validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_BYTES = 32 * 1024 * 1024
MAX_CASES = 100_000


def bounded_read(path: Path) -> bytes:
    """Refuse oversized inputs before parsing them."""
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("report exceeds byte limit")
    return data


def seconds(value: object) -> float:
    """Accept finite, nonnegative durations with a generous job-scale cap."""
    if isinstance(value, bool):
        raise ValueError("invalid duration")
    number = float(str(value))
    if not math.isfinite(number) or not 0 <= number <= 86400:
        raise ValueError("invalid duration")
    return round(number, 6)


def identity() -> dict[str, str]:
    """Read only the named GitHub identity fields; never dump the environment."""
    rules = {
        "GITHUB_SHA": r"[0-9a-f]{40}",
        "GITHUB_RUN_ID": r"[0-9]{1,20}",
        "GITHUB_RUN_ATTEMPT": r"[1-9][0-9]{0,8}",
        "GITHUB_EVENT_NAME": r"(?:pull_request|push|schedule|workflow_dispatch)",
    }
    result = {}
    for key, pattern in rules.items():
        value = os.environ.get(key, "")
        if not re.fullmatch(pattern, value):
            raise ValueError("missing or invalid run identity")
        result[key.removeprefix("GITHUB_").lower()] = value
    return result


def sanitize_junit(data: bytes) -> tuple[bytes, int]:
    """Reconstruct minimal JUnit; even unknown elements/attributes are discarded."""
    text = data.decode("utf-8")
    if "<!" in text or "\0" in text:
        raise ValueError("XML declarations/entities are not accepted")
    root = ET.fromstring(text)
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("unexpected report root")
    suite = ET.Element("testsuite", name="ci", tests="0")
    cases = list(root.iter("testcase"))
    if not 0 < len(cases) <= MAX_CASES:
        raise ValueError("missing or excessive test cases")
    for case in cases:
        # Hash ALL identifiers, including parametrization and local path text.
        key = case.get("classname", "") + "\0" + case.get("name", "")
        name = "case-" + hashlib.sha256(key.encode()).hexdigest()
        safe = ET.SubElement(suite, "testcase", name=name)
        if "time" in case.attrib:
            safe.set("time", str(seconds(case.attrib["time"])))
        if any("}" in child.tag for child in case):
            raise ValueError("namespaced outcomes are not accepted")
        outcomes = [
            child.tag for child in case if child.tag in {"failure", "error", "skipped"}
        ]
        # Pytest emits multiple outcomes for setup/call plus teardown failures.
        # Preserve the strongest result without copying any diagnostic text.
        for outcome in ("error", "failure", "skipped"):
            if outcome in outcomes:
                ET.SubElement(safe, outcome)
                break
    suite.set("tests", str(len(cases)))
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True), len(cases)


def sanitize_timings(raw: Path) -> dict[str, object]:
    """Reconstruct one bounded controller inventory, including crashed workers."""
    source = json.loads(bounded_read(raw / "timings.json"))
    if source["schema"] != 2 or not isinstance(source["workers"], list):
        raise ValueError("invalid timing schema")
    expected, unattributed = source["expected_workers"], source["unattributed_reports"]
    if (
        type(expected) is not int
        or not 1 <= expected <= 128
        or type(unattributed) is not int
        or not 0 <= unattributed <= MAX_CASES * 3
        or not 0 < len(source["workers"]) <= 128
    ):
        raise ValueError("worker evidence missing or excessive")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    collected: set[int] = set()
    complete = len(source["workers"]) == expected and unattributed == 0
    total_records = 0
    for data in source["workers"]:
        worker = data["worker"]
        if not isinstance(worker, str) or not re.fullmatch(
            r"(?:gw[0-9]{1,3}|master)", worker
        ):
            raise ValueError("invalid worker")
        if worker in seen:
            raise ValueError("duplicate worker")
        seen.add(worker)
        records: list[dict[str, object]] = []
        for record in data["phases"]:
            total_records += 1
            if total_records > MAX_CASES * 3:
                raise ValueError("too many phase records")
            if not re.fullmatch(r"[0-9a-f]{64}", record["id"]):
                raise ValueError("invalid case identity")
            if record["phase"] not in {"setup", "call", "teardown"} or record[
                "outcome"
            ] not in {"passed", "failed", "skipped"}:
                raise ValueError("invalid phase result")
            records.append(
                {
                    "id": hashlib.sha256(record["id"].encode()).hexdigest(),
                    "phase": record["phase"],
                    "outcome": record["outcome"],
                    "seconds": seconds(record["seconds"]),
                }
            )
        count = data["collected_total"]
        if count is not None and (
            type(count) is not int or not 0 <= count <= MAX_CASES
        ):
            raise ValueError("invalid collection count")
        if count is not None:
            collected.add(count)
        finished, crashed = data["finished"], data["crashed"]
        if type(finished) is not bool or type(crashed) is not bool:
            raise ValueError("invalid worker state")
        complete = complete and finished and not crashed and count is not None
        selected = len({r["id"] for r in records if r["phase"] == "setup"})
        completed = len({r["id"] for r in records if r["phase"] == "teardown"})
        result.append(
            {
                "worker": worker,
                "collected_total": count,
                "selected": selected,
                "completed": completed,
                "finished": finished,
                "crashed": crashed,
                "phases": records,
            }
        )
    complete = complete and len(collected) == 1
    return {
        "complete": complete,
        "expected_workers": expected,
        "unattributed_reports": unattributed,
        "workers": result,
    }


def sanitize_environment(data: bytes) -> dict[str, object]:
    """Revalidate fixed keys; raw metadata is never copied to an upload."""
    source = json.loads(data)
    keys = {
        "tools": ("python", "uv", "node", "npm"),
        "dependencies": (
            "pytest",
            "pytest-xdist",
            "pytest-cov",
            "coverage",
            "mcp",
            "anyio",
            "uvicorn",
            "sse-starlette",
        ),
        "runner": ("ImageOS", "ImageVersion"),
    }
    result: dict[str, object] = {"schema": 1}
    for group, names in keys.items():
        values = {}
        for name in names:
            value = source[group][name]
            pattern = (
                r"[A-Za-z0-9_.-]{1,64}"
                if group == "runner"
                else r"[0-9][0-9a-z.+!-]{0,63}"
            )
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError("invalid environment version")
            values[name] = value
        result[group] = values
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("safe", type=Path)
    args = parser.parse_args()
    # An existing destination is never trusted, nor reused after a failed pass.
    if args.safe.exists():
        raise SystemExit("safe report directory already exists; upload refused")
    staging: Path | None = None
    try:
        binding = identity()
        junit, count = sanitize_junit(bounded_read(args.raw / "junit.xml"))
        timings = sanitize_timings(args.raw)
        environment = sanitize_environment(bounded_read(args.raw / "environment.json"))
        args.safe.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="safe-report-", dir=args.safe.parent))
        suffix = f"{binding['run_id']}-{binding['run_attempt']}"
        (staging / f"junit-{suffix}-controller.xml").write_bytes(junit)
        (staging / f"environment-{suffix}-controller.json").write_text(
            json.dumps({"run": binding, **environment}, sort_keys=True) + "\n"
        )
        summaries: list[dict[str, object]] = []
        workers = timings["workers"]
        if not isinstance(workers, list):
            raise TypeError("invalid reconstructed worker inventory")
        for worker in workers:
            name = f"timings-{suffix}-{worker['worker']}.json"
            (staging / name).write_text(
                json.dumps({"schema": 2, "run": binding, **worker}, sort_keys=True)
                + "\n"
            )
            summaries.append(
                {key: value for key, value in worker.items() if key != "phases"}
                | {"report": name}
            )
        (staging / f"timings-{suffix}-controller.json").write_text(
            json.dumps(
                {
                    "schema": 2,
                    "run": binding,
                    "cases": count,
                    **timings,
                    "workers": summaries,
                },
                sort_keys=True,
            )
            + "\n"
        )
        staging.rename(args.safe)
        if not timings["complete"]:
            print(
                "::warning::Timing evidence incomplete; observed phases retained; not success evidence"
            )
    except (OSError, ValueError, KeyError, TypeError, RecursionError, ET.ParseError):
        # Do not print the exception: parser diagnostics can echo input bytes.
        print("::error::Diagnostic evidence missing or unsafe; upload refused")
        return 1
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
