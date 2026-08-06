"""Guard `pmcp.__version__` against the installed distribution's metadata.

The version lives in two places — `pyproject.toml`'s `[project] version` and
`src/pmcp/__init__.py`'s `__version__` — and a release that bumps one without
the other makes everything reading `pmcp.__version__` report the wrong release:
the `/health` payload and the `pmcp/{version}` User-Agent both come from it, so
the drift is invisible locally and misleading in production.
"""

import importlib.metadata

import pytest

import pmcp


def test_dunder_version_matches_distribution_metadata() -> None:
    try:
        installed = importlib.metadata.version("pmcp")
    except importlib.metadata.PackageNotFoundError:
        # Not drift — the distribution simply is not installed, which is an
        # environment fault. Fail loudly rather than skipping: a silent skip
        # here would let real drift ship unnoticed on any machine where the
        # metadata happened to be missing.
        pytest.fail(
            "the pmcp distribution is not installed, so __version__ cannot be "
            "checked against its metadata; run `uv sync --all-extras`"
        )

    assert pmcp.__version__ == installed, (
        f"pmcp.__version__ is {pmcp.__version__!r} but the installed "
        f"distribution reports {installed!r}; bump both pyproject.toml and "
        f"src/pmcp/__init__.py"
    )
