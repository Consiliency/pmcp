"""SL-1.1 — pin the declared dependency bounds and prove they resolve.

Written before SL-1.2's `pyproject.toml`/`uv.lock` edit (test-before-impl per
the phase plan's SL-1 task table), so this module is expected to fail until
that edit lands. It is the single piece of evidence for IF-0-P2-3: the four
direct declarations (`mcp`, `httpx`, `httpx2`, `jsonschema`) are two-sided,
the P1 floor-parsing regex still matches and yields `2.0.0`, the installed
`mcp` is genuinely 2.x, and `httpx`/`httpx2` are distinct distributions so a
future resolver change cannot silently collapse one into the other.
"""

from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path

import pytest

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Same regex `.github/workflows/test.yml`'s `min-version-smoke` job uses to
# parse the declared `mcp` floor. Duplicated here (rather than imported) on
# purpose: it is a text-file contract between this repo and that workflow
# step, not a Python symbol either side exports.
FLOOR_REGEX = r'"mcp>=([0-9.]+),<'

DIRECT_DEPENDENCIES = ("mcp", "httpx", "httpx2", "jsonschema")

# Deliberately not parsed with `tomllib`: that's stdlib-only on Python
# 3.11+, and this repo's CI matrix includes 3.10 (`.github/workflows/test.yml`
# `python-version: ["3.10", "3.11", "3.12"]`). Reaching for `tomli` as a
# fallback would just re-create the undeclared-transitive-dependency problem
# this lane exists to close. A plain regex over the quoted specifier strings
# in the `dependencies = [...]` block is the same text-file contract
# `min-version-smoke`'s own floor regex already relies on.
DEPENDENCY_SPECIFIER_RE = re.compile(r'"([A-Za-z0-9_.\[\]-]+(?:[<>=!~][^"]*)?)"')


def _dependencies() -> list[str]:
    text = PYPROJECT_PATH.read_text()
    match = re.search(r"dependencies\s*=\s*\[(.*?)\n\]", text, re.DOTALL)
    assert match, f"could not find a dependencies = [...] block in {PYPROJECT_PATH}"
    return DEPENDENCY_SPECIFIER_RE.findall(match.group(1))


def _specifier_for(name: str, dependencies: list[str]) -> str:
    for dep in dependencies:
        # Match the package name at the start of the specifier so "httpx"
        # doesn't accidentally match the "httpx2" entry.
        if re.match(rf"^{re.escape(name)}(?=[<>=!\s\[]|$)", dep):
            return dep
    pytest.fail(f"no dependency entry found for {name!r} in {PYPROJECT_PATH}")


@pytest.mark.parametrize("name", DIRECT_DEPENDENCIES)
def test_declared_bounds_are_two_sided(name: str) -> None:
    specifier = _specifier_for(name, _dependencies())
    assert ">=" in specifier, f"{name} has no floor: {specifier!r}"
    assert "<" in specifier, f"{name} has no ceiling: {specifier!r}"


def test_mcp_floor_matches_declared_ceiling_style() -> None:
    # mcp specifically must be declared as >=2.0.0,<3.0.0 per IF-0-P2-3.
    specifier = _specifier_for("mcp", _dependencies())
    assert "mcp>=2.0.0,<3.0.0" in specifier.replace(" ", ""), specifier


def test_installed_mcp_is_2x() -> None:
    version = importlib.metadata.version("mcp")
    assert version.startswith("2."), (
        f"installed mcp is {version!r}, expected a 2.x release "
        "(run `uv sync --all-extras` after the pyproject.toml bump)"
    )


def test_p1_floor_regex_still_matches_and_yields_2_0_0() -> None:
    text = PYPROJECT_PATH.read_text()
    match = re.search(FLOOR_REGEX, text)
    assert match, (
        f"the min-version-smoke floor regex {FLOOR_REGEX!r} no longer "
        "matches pyproject.toml's mcp specifier"
    )
    assert match.group(1) == "2.0.0"


def test_httpx_httpx2_jsonschema_all_importable() -> None:
    import httpx  # noqa: F401
    import httpx2  # noqa: F401
    import jsonschema  # noqa: F401


def test_httpx_and_httpx2_are_distinct_modules() -> None:
    import httpx
    import httpx2

    assert httpx is not httpx2
    assert httpx.__name__ != httpx2.__name__
    assert sys.modules["httpx"] is not sys.modules["httpx2"]
    # Distinct distributions, so their __version__ attributes are resolved
    # independently — a resolver bug that collapsed one into the other would
    # make these identical (or make one attribute lookup fail).
    assert httpx.__version__ != httpx2.__version__
