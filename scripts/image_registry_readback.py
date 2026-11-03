"""Read-only ghcr.io readback of consiliency/pmcp; it never authorizes activation.

GET only, against exactly two hosts: ghcr.io (registry and token) and
api.github.com (only ``repos/Consiliency/pmcp/compare/<sha>...main``). Every
tag is listed through ``Link`` pagination and every manifest digest is checked
against the body. Any failure is ``complete: false``, which halts every owner
procedure step that uses the readback. With ``--baseline`` and ``--mode`` the
fresh readback is compared under one of three contracts (drain, pre-enable,
watch); a stop is a detection event for the owner's incident path (D3).

``watch`` is the image-watch.yml driver: it runs the drained inventory diff
and the watch contract against the committed baselines and writes exactly one
outcome marker (COMPLETE, DISCREPANCY, INCOMPLETE or NO_BASELINE) to the job
summary.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, NamedTuple

REGISTRY_HOST = "ghcr.io"
API_HOST = "api.github.com"
IMAGE = "consiliency/pmcp"
REPOSITORY = "Consiliency/pmcp"
TOKEN_URL = f"https://{REGISTRY_HOST}/token?scope=repository:{IMAGE}:pull"
TAGS_PATH = f"/v2/{IMAGE}/tags/list"
MANIFEST_PREFIX = f"/v2/{IMAGE}/manifests/"
COMPARE_PATH = re.compile(r"/repos/Consiliency/pmcp/compare/([0-9a-f]{40})\.\.\.main")
ACCEPT = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
TAG_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
VERSION_TAG = re.compile(r"^\d+\.\d+(\.\d+)?([-+.].*)?$")
SHORT_SHA_TAG = re.compile(r"[0-9a-f]{7}")
FULL_SHA_TAG = re.compile(r"sha-([0-9a-f]{40})")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
PROTECTED_TAGS = frozenset({"3.0.0", "3.0", "latest"})
ON_MAIN = frozenset({"ahead", "identical"})
MAX_MANIFEST = 4 * 1024 * 1024
MAX_PAGE = 1024 * 1024
MAX_PAGES = 1000
TIMEOUT = 30
MODES = ("drain", "pre-enable", "watch")

BASELINE_DIR = ".consiliency/evidence/image-baselines"
BASELINE_FILES = ("inventory-B1.json", "registry-R2.json", "retention.json")
OUTCOMES = ("COMPLETE", "DISCREPANCY", "INCOMPLETE", "NO_BASELINE")
MARKER = "IMAGE_WATCH_OUTCOME"
INCOMPLETE_TEXT = (
    "registry readback incomplete; all new registry writes must remain disabled"
)


class ReadbackError(Exception):
    """A refusal; its text is fixed and never carries a body or credential."""


class Response(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


Transport = Callable[[str, str, dict[str, str]], Response]


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_time(value: Any) -> datetime.datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("invalid timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp without offset")
    return parsed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def urllib_transport(method: str, url: str, headers: dict[str, str]) -> Response:
    """The real transport: no redirects, bounded body, bounded wait."""
    if method != "GET":
        raise ReadbackError("non-GET request refused")
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_MANIFEST + 1)
            status = response.status
            items = response.headers.items()
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_MANIFEST + 1) if exc.fp is not None else b""
        status = exc.code
        items = exc.headers.items() if exc.headers is not None else []
    return Response(status, {k.lower(): v for k, v in items}, body)


class Reader:
    """GET-only reader restricted to the two allowed hosts and paths."""

    def __init__(self, transport: Transport, github_token: str | None = None):
        self._transport = transport
        self._github_token = github_token or None
        self._registry_token: str | None = None

    def request(self, method: str, url: str, headers: dict[str, str]) -> Response:
        if method != "GET":
            raise ReadbackError("non-GET request refused")
        parts = urllib.parse.urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port is not None
            or parts.fragment
        ):
            raise ReadbackError("request URL refused")
        if parts.hostname == REGISTRY_HOST:
            allowed = (
                parts.path == "/token"
                or parts.path == TAGS_PATH
                or (
                    parts.path.startswith(MANIFEST_PREFIX)
                    and TAG_NAME.fullmatch(parts.path[len(MANIFEST_PREFIX) :])
                    is not None
                )
            )
        elif parts.hostname == API_HOST:
            allowed = COMPARE_PATH.fullmatch(parts.path) is not None and not parts.query
        else:
            allowed = False
        if not allowed:
            raise ReadbackError("host or path outside the allowlist")
        try:
            response = self._transport("GET", url, headers)
        except ReadbackError:
            raise
        except (OSError, ValueError) as exc:
            raise ReadbackError("transport failure or timeout") from exc
        if len(response.body) > MAX_MANIFEST:
            raise ReadbackError("oversize response")
        return response

    def _registry_headers(self) -> dict[str, str]:
        if self._registry_token is None:
            headers = {}
            if self._github_token:
                basic = base64.b64encode(
                    f"token:{self._github_token}".encode()
                ).decode()
                headers["Authorization"] = f"Basic {basic}"
            response = self.request("GET", TOKEN_URL, headers)
            if response.status != 200:
                raise ReadbackError("registry token refused")
            token = json.loads(response.body).get("token")
            if not isinstance(token, str) or not token:
                raise ReadbackError("registry token missing")
            self._registry_token = token
        return {"Authorization": f"Bearer {self._registry_token}"}

    def tags(self) -> list[str]:
        url: str | None = f"https://{REGISTRY_HOST}{TAGS_PATH}?n=100"
        seen: list[str] = []
        pages = 0
        while url is not None:
            pages += 1
            if pages > MAX_PAGES:
                raise ReadbackError("tag pagination limit exceeded")
            response = self.request("GET", url, self._registry_headers())
            if not 200 <= response.status < 300 or len(response.body) > MAX_PAGE:
                raise ReadbackError("tag list refused")
            data = json.loads(response.body)
            names = data.get("tags") if isinstance(data, dict) else None
            if not isinstance(names, list) or any(
                not isinstance(n, str) or TAG_NAME.fullmatch(n) is None for n in names
            ):
                raise ReadbackError("invalid tag list")
            for name in names:
                if name in seen:
                    raise ReadbackError("duplicate tag in listing")
                seen.append(name)
            url = _next_link(response.headers.get("link"))
        return seen

    def manifest(self, tag: str) -> dict[str, str]:
        headers = dict(self._registry_headers(), Accept=", ".join(ACCEPT))
        response = self.request(
            "GET", f"https://{REGISTRY_HOST}{MANIFEST_PREFIX}{tag}", headers
        )
        if not 200 <= response.status < 300:
            raise ReadbackError("manifest read refused")
        digest = response.headers.get("docker-content-digest")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise ReadbackError("missing Docker-Content-Digest")
        if "sha256:" + hashlib.sha256(response.body).hexdigest() != digest:
            raise ReadbackError("manifest body does not match its digest")
        media_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        if media_type not in ACCEPT:
            raise ReadbackError("manifest media type outside the Accept set")
        return {"digest": digest, "media_type": media_type}

    def compare_status(self, sha: str) -> str:
        """``ahead``/``identical``/``behind``/``diverged`` or ``missing`` (404)."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"
        response = self.request(
            "GET",
            f"https://{API_HOST}/repos/{REPOSITORY}/compare/{sha}...main",
            headers,
        )
        if response.status == 404:
            return "missing"
        if response.status != 200:
            raise ReadbackError("compare read refused")
        status = json.loads(response.body).get("status")
        if status not in {"ahead", "identical", "behind", "diverged"}:
            raise ReadbackError("unknown compare status")
        return str(status)


def _next_link(header: str | None) -> str | None:
    """The ``rel="next"`` target, refused unless it stays on the tag list path."""
    if not header:
        return None
    for part in header.split(","):
        match = re.fullmatch(r'\s*<([^>]*)>\s*;\s*rel="?next"?\s*', part)
        if match is None:
            continue
        target = urllib.parse.urlsplit(match[1])
        if target.scheme or target.netloc:
            if target.scheme != "https" or target.netloc != REGISTRY_HOST:
                raise ReadbackError("foreign Link host")
        if target.path != TAGS_PATH or target.fragment:
            raise ReadbackError("foreign Link path")
        query = f"?{target.query}" if target.query else ""
        return f"https://{REGISTRY_HOST}{TAGS_PATH}{query}"
    return None


def readback(reader: Reader) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": 1,
        "standing": "READ_ONLY_REGISTRY_READBACK",
        "registry": REGISTRY_HOST,
        "image": IMAGE,
        "complete": False,
        "legacy_trusted": False,
        "activation_allowed": False,
        "registry_writes": False,
        "started_at": now(),
        "tags": {},
    }
    try:
        for tag in reader.tags():
            doc["tags"][tag] = reader.manifest(tag)
        doc["complete"] = True
    except (ReadbackError, ValueError, TypeError, AttributeError):
        doc["tags"] = {}
        doc["error"] = INCOMPLETE_TEXT
    doc["finished_at"] = now()
    return doc


# --- contracts ----------------------------------------------------------------


def readback_valid(doc: Any) -> bool:
    try:
        parse_time(doc["started_at"])
        parse_time(doc["finished_at"])
        tags = doc["tags"]
        return (
            doc["schema"] == 1
            and doc["complete"] is True
            and doc["legacy_trusted"] is False
            and isinstance(tags, dict)
            and all(
                TAG_NAME.fullmatch(tag) is not None
                and isinstance(entry, dict)
                and DIGEST.fullmatch(str(entry.get("digest"))) is not None
                and entry.get("media_type") in ACCEPT
                for tag, entry in tags.items()
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


def _inventory_valid(doc: Any) -> bool:
    try:
        parse_time(doc["finished_at"])
        return (
            doc["complete"] is True
            and doc["stability_verified"] is True
            and isinstance(doc["runs"], list)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _digests(doc: dict[str, Any]) -> dict[str, str]:
    return {tag: entry["digest"] for tag, entry in doc["tags"].items()}


def _label(tag: str) -> str:
    if tag in PROTECTED_TAGS or VERSION_TAG.match(tag):
        return "protected tag"
    return "tag"


def contract(
    mode: str,
    baseline: Any,
    current: Any,
    *,
    b0: Any = None,
    b1: Any = None,
    on_main: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Compare two readbacks. ``complete: false`` means retake, not D3."""
    result: dict[str, Any] = {"mode": mode, "complete": False, "stops": []}
    if mode not in MODES:
        result["error"] = "unknown contract"
        return result
    if not readback_valid(baseline) or not readback_valid(current):
        result["error"] = "a readback is incomplete or lacks started_at/finished_at"
        return result
    before, after = _digests(baseline), _digests(current)
    stops: list[str] = result["stops"]
    explaining: dict[str, str] = {}
    if mode == "drain":
        if not _inventory_valid(b0) or not _inventory_valid(b1):
            result["error"] = "drain needs stable complete B0 and B1 inventories"
            return result
        if not (
            parse_time(baseline["started_at"]) > parse_time(b0["finished_at"])
            and parse_time(current["started_at"]) > parse_time(b1["finished_at"])
        ):
            result["error"] = "readback order violation; retake the readback"
            return result
        old_runs = {row.get("id"): row for row in b0["runs"] if isinstance(row, dict)}
        for row in b1["runs"]:
            old = old_runs.get(row.get("id")) if isinstance(row, dict) else None
            if (
                old is not None
                and row.get("event") == "push"
                and row.get("head_branch") == "main"
                and old.get("status") != "completed"
                and row.get("status") == "completed"
                and isinstance(row.get("head_sha"), str)
            ):
                explaining[row["head_sha"][:7]] = row["head_sha"]
    status_cache: dict[str, str] = {}

    def passes_on_main(sha: str) -> bool:
        if on_main is None:
            raise ReadbackError("no compare reader")
        if sha not in status_cache:
            status_cache[sha] = on_main(sha)
        return status_cache[sha] in ON_MAIN

    try:
        for tag, digest in sorted(before.items()):
            if tag not in after:
                stops.append(f"{_label(tag)} disappeared: {tag}")
            elif tag != "main" and after[tag] != digest:
                stops.append(f"{_label(tag)} digest changed: {tag}")
        accepted_new: dict[str, str] = {}
        for tag in sorted(set(after) - set(before)):
            if tag == "main":
                continue
            short = SHORT_SHA_TAG.fullmatch(tag)
            full = FULL_SHA_TAG.fullmatch(tag)
            if mode == "drain" and short and tag in explaining:
                accepted_new[tag] = after[tag]
            elif mode == "watch" and full:
                if passes_on_main(full[1]):
                    accepted_new[tag] = after[tag]
                else:
                    stops.append(f"new sha tag is not on main: {tag}")
            else:
                stops.append(f"unexpected new {_label(tag)}: {tag}")
        if "main" in after and after.get("main") != before.get("main"):
            target = after["main"]
            if mode == "drain":
                ok = target in {
                    d for t, d in accepted_new.items() if SHORT_SHA_TAG.fullmatch(t)
                }
            elif mode == "watch":
                ok = any(
                    digest == target
                    and FULL_SHA_TAG.fullmatch(tag) is not None
                    and passes_on_main(tag[4:])
                    for tag, digest in sorted(after.items())
                )
            else:
                ok = False
            if not ok:
                stops.append("main moved to an unexplained digest")
    except (ReadbackError, ValueError, TypeError, AttributeError):
        result["error"] = INCOMPLETE_TEXT
        return result
    result["complete"] = True
    return result


# --- watch driver -------------------------------------------------------------


def _enabled() -> bool:
    return os.environb.get(b"PMCP_IMAGE_PUBLISH") == b"enabled"


def _load_inventory_module() -> Any:
    path = Path(__file__).resolve().with_name("image_cutover_inventory.py")
    spec = importlib.util.spec_from_file_location("image_cutover_inventory", path)
    if spec is None or spec.loader is None:
        raise ReadbackError("inventory collector unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ls_tree(commit: str) -> list[str]:
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ReadbackError("commit must be 40 lowercase hex")
    output = subprocess.run(
        ["git", "ls-tree", "--name-only", commit, "--", BASELINE_DIR + "/"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    return [line for line in output.splitlines() if line]


def _write_fresh(path: Path, doc: Any) -> None:
    with path.open("x") as stream:
        stream.write(json.dumps(doc, sort_keys=True, indent=2) + "\n")


def watch(
    commit: str,
    baseline_dir: Path,
    work_dir: Path,
    *,
    reader: Reader,
    ls_tree: Callable[[str], list[str]] | None = None,
    inventory_module: Any = None,
) -> tuple[str, list[str]]:
    """Return (outcome, detail lines). Both comparisons run when baselines exist."""
    try:
        listed = (ls_tree or _ls_tree)(commit)
    except (OSError, ValueError, ReadbackError, subprocess.SubprocessError):
        return "INCOMPLETE", ["baseline listing failed"]
    if not listed:
        if _enabled():
            return "INCOMPLETE", ["baselines are missing while publishing is enabled"]
        return "NO_BASELINE", ["no committed baselines and publishing is not enabled"]
    expected = sorted(f"{BASELINE_DIR}/{name}" for name in BASELINE_FILES)
    try:
        present = sorted(p.name for p in baseline_dir.iterdir())
    except OSError:
        present = []
    if sorted(listed) != expected or present != sorted(BASELINE_FILES):
        return "INCOMPLETE", ["partial or extra baseline set"]
    inv = inventory_module if inventory_module is not None else _load_inventory_module()
    try:
        b1 = json.loads((baseline_dir / "inventory-B1.json").read_text())
        r2 = json.loads((baseline_dir / "registry-R2.json").read_text())
        inv.load_retention(json.loads((baseline_dir / "retention.json").read_text()))
    except (OSError, ValueError, TypeError):
        return "INCOMPLETE", ["invalid baseline file"]
    if inv.baseline_refusals(b1, "drained") or not readback_valid(r2):
        return "INCOMPLETE", ["invalid baseline file"]

    discrepancies: list[str] = []
    incomplete: list[str] = []
    work_dir.mkdir(parents=True, exist_ok=False)
    inventory = inv.run_comparison(
        baseline_dir / "inventory-B1.json",
        "drained",
        retention_path=baseline_dir / "retention.json",
    )
    _write_fresh(work_dir / "inventory.json", inventory)
    discrepancies += [f"inventory: {stop}" for stop in inventory.get("stops") or []]
    if not inventory.get("complete"):
        incomplete.append("inventory incomplete or unstable")
    current = readback(reader)
    verdict = contract("watch", r2, current, on_main=reader.compare_status)
    current["comparison"] = verdict
    _write_fresh(work_dir / "registry.json", current)
    discrepancies += [f"registry: {stop}" for stop in verdict["stops"]]
    if not verdict["complete"]:
        incomplete.append("registry readback or comparison incomplete")
    if discrepancies:
        return "DISCREPANCY", discrepancies + incomplete
    if incomplete:
        return "INCOMPLETE", incomplete
    return "COMPLETE", ["inventory and registry match the committed baselines"]


def write_marker(summary: Path, outcome: str, details: list[str]) -> None:
    if outcome not in OUTCOMES:
        raise ValueError("unknown outcome")
    lines = ["## image-watch", "", f"{MARKER}={outcome}", ""]
    lines += [f"- {line.replace(MARKER, 'marker')}" for line in details]
    with summary.open("a") as stream:
        stream.write("\n".join(lines) + "\n")


# --- CLI ----------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with path.open("rb") as stream:
        data = stream.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("oversize input")
    return json.loads(data)


def main(argv: list[str] | None = None, transport: Transport = urllib_transport) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    read = sub.add_parser("readback", help="fresh registry readback, optional contract")
    read.add_argument("--output", required=True, type=Path)
    read.add_argument("--baseline", type=Path)
    read.add_argument("--mode", choices=MODES)
    read.add_argument("--inventory-b0", type=Path)
    read.add_argument("--inventory-b1", type=Path)
    run = sub.add_parser("watch", help="image-watch.yml driver")
    run.add_argument("--commit", required=True)
    run.add_argument("--baseline-dir", required=True, type=Path)
    run.add_argument("--work-dir", required=True, type=Path)
    run.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    reader = Reader(transport, os.environ.get("GITHUB_TOKEN"))
    if args.command == "watch":
        try:
            outcome, details = watch(
                args.commit, args.baseline_dir, args.work_dir, reader=reader
            )
        except Exception:  # noqa: BLE001 - any crash is an incomplete observation
            outcome, details = "INCOMPLETE", ["watch driver failed"]
        write_marker(args.summary, outcome, details)
        print(f"{MARKER}={outcome}")
        for line in details:
            print(f"  {line}")
        return 0 if outcome in {"COMPLETE", "NO_BASELINE"} else 1
    if (args.baseline is None) != (args.mode is None):
        parser.error("--baseline and --mode go together")
    if (args.mode == "drain") != (
        args.inventory_b0 is not None and args.inventory_b1 is not None
    ):
        parser.error("--mode drain needs --inventory-b0 and --inventory-b1")
    # A previous report must never masquerade as this invocation after a crash.
    with args.output.open("x") as stream:
        json.dump(
            {
                "complete": False,
                "activation_allowed": False,
                "legacy_trusted": False,
                "standing": "READBACK_STARTED_INCOMPLETE",
                "started_at": now(),
            },
            stream,
        )
    doc = readback(reader)
    code = 0 if doc["complete"] else 1
    if args.baseline is not None:
        try:
            baseline = _load_json(args.baseline)
            b0 = _load_json(args.inventory_b0) if args.inventory_b0 else None
            b1 = _load_json(args.inventory_b1) if args.inventory_b1 else None
        except (OSError, ValueError):
            verdict: dict[str, Any] = {
                "mode": args.mode,
                "complete": False,
                "stops": [],
                "error": "baseline input refused",
            }
        else:
            verdict = contract(
                args.mode, baseline, doc, b0=b0, b1=b1, on_main=reader.compare_status
            )
        doc["comparison"] = verdict
        if not verdict["complete"] or verdict["stops"]:
            code = 1
    args.output.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n")
    summary = {k: doc[k] for k in ("complete", "activation_allowed", "legacy_trusted")}
    summary["tags"] = len(doc["tags"])
    if "comparison" in doc:
        summary["comparison"] = doc["comparison"]
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
