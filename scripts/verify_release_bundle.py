"""Check exact archive bytes and same-run release identity before any upload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def verify(bundle: Path, *, released_dir: Path | None = None) -> dict[str, object]:
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 65536:
        raise ValueError("invalid manifest")
    manifest = json.loads(manifest_path.read_text())
    if manifest["schema"] != 1 or manifest["purpose"] != "release":
        raise ValueError("not a verified release bundle")
    for key, expected in [
        ("sha", os.environ["GITHUB_SHA"]),
        ("run_id", os.environ["GITHUB_RUN_ID"]),
        ("attempt", os.environ["GITHUB_RUN_ATTEMPT"]),
        ("tag", os.environ["GITHUB_REF_NAME"]),
    ]:
        if manifest[key] != expected:
            raise ValueError("release identity mismatch")
    version = manifest["version"]
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?", version)
        or manifest["tag"] != "v" + version
    ):
        raise ValueError("invalid release version")
    expected_names = {f"pmcp-{version}-py3-none-any.whl", f"pmcp-{version}.tar.gz"}
    if set(manifest["archives"]) != expected_names:
        raise ValueError("invalid manifest archive set")
    for directory in [
        bundle / "dist",
        *([released_dir] if released_dir is not None else []),
    ]:
        paths = list(directory.iterdir())
        if {p.name for p in paths} != expected_names:
            raise ValueError("unexpected archive set")
        for path in paths:
            expected = manifest["archives"][path.name]
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != expected["bytes"]
            ):
                raise ValueError("archive size/type mismatch")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
                raise ValueError("archive hash mismatch")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--released-dir", type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        raise ValueError("only tag releases can publish")
    manifest = verify(args.bundle, released_dir=args.released_dir)
    tag = str(manifest["tag"])
    refs = subprocess.check_output(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "origin",
            f"refs/tags/{tag}",
            f"refs/tags/{tag}^{{}}",
        ],
        text=True,
        timeout=30,
    )
    objects = dict(line.split()[::-1] for line in refs.splitlines())
    current = objects.get(f"refs/tags/{tag}^{{}}", objects.get(f"refs/tags/{tag}"))
    if current != manifest["sha"]:
        raise ValueError("remote release tag moved or disappeared")
    print("Exact release archive hashes, run identity and current tag verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
