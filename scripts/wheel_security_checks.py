"""Run the focused SDK rejection checks using the installed wheel."""

import importlib.metadata as metadata
import pathlib
import sys
import pmcp
import pytest

assert (
    pathlib.Path(pmcp.__file__)
    .resolve()
    .is_relative_to(pathlib.Path(sys.prefix).resolve())
)
print("installed pmcp:", pmcp.__file__)
print({name: metadata.version(name) for name in ("mcp", "httpx2", "pytest")})
raise SystemExit(
    pytest.main(
        [
            "-o",
            "pythonpath=",
            "tests/test_http_transport.py",
            "tests/test_parse_error_echo.py",
            "-q",
            "-k",
            "sdk or plumbing or received_error",
        ]
    )
)
