"""Observe a current PyPI wheel separately from the newest main-source probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request


def current_wheel() -> dict[str, str]:
    with urllib.request.urlopen(
        "https://pypi.org/pypi/pmcp/json", timeout=20
    ) as response:
        data = response.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("index response exceeds limit")
    index = json.loads(data)
    version = index["info"]["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?", version):
        raise ValueError("unsupported release version")
    filename = f"pmcp-{version}-py3-none-any.whl"
    wheels = [
        x
        for x in index["urls"]
        if x["filename"] == filename
        and x["packagetype"] == "bdist_wheel"
        and not x.get("yanked")
    ]
    if len(wheels) != 1:
        raise ValueError("ambiguous published wheel")
    wheel = wheels[0]
    parsed = urllib.parse.urlparse(wheel["url"])
    if (
        parsed.scheme != "https"
        or parsed.hostname != "files.pythonhosted.org"
        or parsed.username
        or parsed.password
    ):
        raise ValueError("unexpected archive origin")
    sha = wheel["digests"]["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("invalid archive digest")
    return {
        "version": version,
        "filename": filename,
        "sha256": sha,
        "url": wheel["url"],
        "index": "https://pypi.org/simple",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    wheel = current_wheel()
    report: dict[str, object] = {k: v for k, v in wheel.items() if k != "url"}
    report.update(
        schema=1,
        status="failed",
        source_sha=os.environ["GITHUB_SHA"],
        run_id=os.environ["GITHUB_RUN_ID"],
        attempt=os.environ["GITHUB_RUN_ATTEMPT"],
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True) + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("report_ready=true\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "npm_config_cache",
            "npm_config_store_dir",
            "pnpm_config_store_dir",
        }
    }
    with tempfile.TemporaryDirectory(prefix="pmcp-published-") as scratch:
        root = Path(scratch)
        archive = root / wheel["filename"]
        with urllib.request.urlopen(wheel["url"], timeout=30) as response:
            if (
                urllib.parse.urlparse(response.geturl()).hostname
                != "files.pythonhosted.org"
            ):
                raise ValueError("unexpected redirected archive origin")
            data = response.read(100 * 1024 * 1024 + 1)
        if (
            len(data) > 100 * 1024 * 1024
            or hashlib.sha256(data).hexdigest() != wheel["sha256"]
        ):
            raise ValueError("published wheel identity mismatch")
        archive.write_bytes(data)
        work, venv = root / "work", root / "venv"
        work.mkdir()
        for script in ("release_installed.py", "p1_probe_server.py"):
            shutil.copy2(source / ".github/probe" / script, work / script)

        def run(command: list[str]) -> None:
            subprocess.run(command, cwd=work, env=env, check=True, timeout=300)

        run(["uv", "venv", "--python", "3.12", str(venv)])
        python = venv / "bin/python"
        run(
            [
                "uv",
                "--no-cache",
                "pip",
                "install",
                "--python",
                str(python),
                str(archive) + "[http]",
            ]
        )
        report["dependencies"] = json.loads(
            subprocess.check_output(
                ["uv", "pip", "list", "--python", str(python), "--format", "json"],
                cwd=work,
                env=env,
                text=True,
                timeout=30,
            )
        )
        args.report.write_text(json.dumps(report, sort_keys=True) + "\n")
        run([str(venv / "bin/pmcp"), "--version"])
        run([str(venv / "bin/pmcp"), "--help"])
        run(
            [
                str(python),
                "-I",
                "-B",
                str(work / "release_installed.py"),
                wheel["version"],
            ]
        )
    report["status"] = "success"
    args.report.write_text(json.dumps(report, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
