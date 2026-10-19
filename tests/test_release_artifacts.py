"""The publishable filename set is exact; extra or substituted files fail."""

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_artifacts", ROOT / "scripts/release_artifacts.py"
)
assert spec and spec.loader
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)
verify_spec = importlib.util.spec_from_file_location(
    "verify_release_bundle", ROOT / "scripts/verify_release_bundle.py"
)
assert verify_spec and verify_spec.loader
verifier = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verifier)


@pytest.mark.parametrize(
    "mutation",
    ["extra-wheel", "missing-sdist", "symlink", "directory", "manifest-in-dist"],
)
def test_unexpected_archive_sets_fail(tmp_path: Path, mutation: str) -> None:
    wheel = tmp_path / "pmcp-3.0.0-py3-none-any.whl"
    sdist = tmp_path / "pmcp-3.0.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    assert len(artifacts.archives(tmp_path, "3.0.0")) == 2
    if mutation == "extra-wheel":
        (tmp_path / "extra.whl").write_bytes(b"extra")
    elif mutation == "missing-sdist":
        sdist.unlink()
    elif mutation == "symlink":
        wheel.unlink()
        wheel.symlink_to(sdist)
    elif mutation == "directory":
        wheel.unlink()
        wheel.mkdir()
    else:
        (tmp_path / "manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        artifacts.archives(tmp_path, "3.0.0")


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "altered-hash",
        "extra-wheel",
        "wrong-run",
        "wrong-attempt",
        "rehearsal",
        "wrong-sha",
        "missing-sdist",
    ],
)
def test_identity_and_archive_substitutions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in ("pmcp-3.0.0-py3-none-any.whl", "pmcp-3.0.0.tar.gz"):
        (dist / name).write_bytes(b"exact-tested-bytes")
    manifest = {
        "schema": 1,
        "purpose": "release",
        "version": "3.0.0",
        "tag": "v3.0.0",
        "sha": "a" * 40,
        "run_id": "123",
        "attempt": "1",
        "archives": {
            p.name: {
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in dist.iterdir()
        },
    }
    for key, value in {
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REF_NAME": "v3.0.0",
    }.items():
        monkeypatch.setenv(key, value)
    if mutation == "altered-hash":
        (dist / "pmcp-3.0.0.tar.gz").write_bytes(b"different-bytes")
    elif mutation == "extra-wheel":
        (dist / "extra.whl").write_bytes(b"extra")
    elif mutation == "wrong-run":
        manifest["run_id"] = "124"
    elif mutation == "wrong-attempt":
        manifest["attempt"] = "2"
    elif mutation == "rehearsal":
        manifest["purpose"] = "rehearsal"
    elif mutation == "wrong-sha":
        manifest["sha"] = "b" * 40
    elif mutation == "missing-sdist":
        (dist / "pmcp-3.0.0.tar.gz").unlink()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    if mutation == "none":
        assert verifier.verify(tmp_path)["sha"] == "a" * 40
    else:
        with pytest.raises(ValueError):
            verifier.verify(tmp_path)
