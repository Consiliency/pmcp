"""Nonpublishing exact-wheel AND exact-sdist verification and manifest creation."""

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
import tomllib


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archives(dist: Path, version: str) -> list[Path]:
    expected = {f"pmcp-{version}-py3-none-any.whl", f"pmcp-{version}.tar.gz"}
    paths = list(dist.iterdir())
    if {p.name for p in paths} != expected or any(
        not p.is_file() or p.is_symlink() for p in paths
    ):
        raise ValueError("unexpected distribution filename set")
    if any(not 0 < p.stat().st_size <= 100 * 1024 * 1024 for p in paths):
        raise ValueError("invalid distribution size")
    return sorted(paths)


def run(args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True, timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearsal", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"][
        "version"
    ]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?", version):
        raise ValueError("invalid version")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=source, text=True
    ).strip()
    tag = "v" + version
    if not args.rehearsal:
        assert (
            os.environ["GITHUB_REF_TYPE"] == "tag"
            and os.environ["GITHUB_REF_NAME"] == tag
        )
        assert os.environ["GITHUB_SHA"] == sha
        actual = subprocess.check_output(
            ["git", "rev-parse", tag + "^{commit}"], cwd=source, text=True
        ).strip()
        assert actual == sha
    bundle = args.bundle.resolve()
    if bundle.exists():
        raise ValueError("bundle directory must be fresh")
    dist = bundle / "dist"
    dist.mkdir(parents=True)
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
    constraints = source / "scripts/release-build-constraints.txt"
    run(
        [
            "uv",
            "build",
            "--python",
            "3.12",
            "--build-constraints",
            str(constraints),
            "--out-dir",
            str(dist),
        ],
        cwd=source,
        env=env,
    )
    # uv creates this known housekeeping file in a fresh build output. It is
    # never publishable; every other unexpected output still fails closed.
    housekeeping = dist / ".gitignore"
    if housekeeping.exists():
        if housekeeping.is_symlink() or housekeeping.read_bytes() not in {b"*", b"*\n"}:
            raise ValueError("unexpected build housekeeping")
        housekeeping.unlink()
    paths = archives(dist, version)
    before = {p.name: {"sha256": digest(p), "bytes": p.stat().st_size} for p in paths}
    # Each install starts in an independent environment and cwd with no src/.
    resolved = {}
    with tempfile.TemporaryDirectory(prefix="pmcp-archive-") as scratch:
        root = Path(scratch)
        for index, path in enumerate(paths):
            venv, work = root / f"env{index}", root / f"work{index}"
            work.mkdir()
            shutil.copy2(
                source / ".github/probe/release_installed.py",
                work / "release_installed.py",
            )
            shutil.copy2(
                source / ".github/probe/p1_probe_server.py", work / "p1_probe_server.py"
            )
            run(["uv", "venv", "--python", "3.12", str(venv)], cwd=work, env=env)
            python = venv / "bin/python"
            run(
                [
                    "uv",
                    "--no-cache",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--build-constraints",
                    str(constraints),
                    str(path) + "[http]",
                ],
                cwd=work,
                env=env,
            )
            resolved[path.name] = json.loads(
                subprocess.check_output(
                    ["uv", "pip", "list", "--python", str(python), "--format", "json"],
                    cwd=work,
                    env=env,
                    text=True,
                    timeout=30,
                )
            )
            run([str(venv / "bin/pmcp"), "--version"], cwd=work, env=env)
            run([str(venv / "bin/pmcp"), "--help"], cwd=work, env=env)
            run(
                [str(python), "-I", "-B", str(work / "release_installed.py"), version],
                cwd=work,
                env=env,
            )
    assert before == {
        p.name: {"sha256": digest(p), "bytes": p.stat().st_size}
        for p in archives(dist, version)
    }
    manifest = {
        "schema": 1,
        "purpose": "rehearsal" if args.rehearsal else "release",
        "sha": sha,
        "tree": tree,
        "tag": tag,
        "version": version,
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        "lock_sha256": digest(source / "uv.lock"),
        "build_constraints_sha256": digest(constraints),
        "archives": before,
        "resolved_dependencies": resolved,
        "uv_version": subprocess.check_output(
            ["uv", "--version"], text=True, timeout=10
        ).strip(),
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
