"""The publishable filename set is exact; extra or substituted files fail."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_artifacts", ROOT / "scripts/release_artifacts.py"
)
assert spec and spec.loader
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


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
