"""Every log record is scrubbed at creation, whoever logs it
(Consiliency/pmcp#297, rev 4).

`install_log_scrubber` wraps the `logging` record factory, so it covers
loggers pmcp does not own and cannot enumerate -- the MCP SDK's
`ClientSession` logs on `"client"`, outside `mcp.*` (the rev 3 board's B1)
-- and each branch of `scrub_record` is pinned here: `exc_info`, `%`-args
(including exceptions nested in containers and a `%(name)s` mapping), a
`msg` that is itself an exception, and `stack_info`.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from pmcp.types import McpTaskInfo
from tests.test_argument_error_echo import _FAMILIES, _forbidden, _record_text

_LOGGERS = (
    "client",
    "server",
    "mcp.client.session",
    "asyncio",
    "uvicorn.error",
    "httpx",
    "anyio",
    "third.party",
)


def _validation_error(s: str) -> ValidationError:
    try:
        McpTaskInfo.model_validate({"task_id": "t", "ttl": {"v": s}})
    except ValidationError as error:
        return error
    raise AssertionError("no validation error")


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> Iterator[_Capture]:
    """A handler on every logger under test, with propagation off, so the
    record the scrubber made is the one inspected."""
    from pmcp.argument_errors import install_log_scrubber

    install_log_scrubber()
    handler = _Capture()
    saved = []
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        saved.append((logger, logger.level, logger.propagate))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    yield handler
    for logger, level, propagate in saved:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


def _clean(record: logging.LogRecord, s: str) -> bool:
    text = _record_text(record)
    return not any(form in text for form in _forbidden(s))


@pytest.mark.parametrize("name", _LOGGERS)
@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_exc_info_is_scrubbed_on_any_logger(
    capture: _Capture, name: str, family: str
) -> None:
    s = _FAMILIES[family][1]
    try:
        raise _validation_error(s)
    except ValidationError:
        logging.getLogger(name).warning(
            "Failed to validate notification: %s", "m", exc_info=True
        )
    (record,) = capture.records
    assert record.exc_info is None
    assert "validation error for McpTaskInfo: $.ttl" in record.getMessage()
    assert _clean(record, s)


@pytest.mark.parametrize(
    "shape",
    ["tuple", "list", "dict", "set-free-nested", "mapping", "repr"],
)
def test_percent_args_are_scrubbed_however_nested(
    capture: _Capture, shape: str
) -> None:
    s = _FAMILIES["alpha"][1]
    error = _validation_error(s)
    logger = logging.getLogger("third.party")
    if shape == "tuple":
        logger.error("failed: %s", error)
    elif shape == "list":
        logger.error("failed: %s", [1, error])
    elif shape == "dict":
        logger.error("failed: %s", {"k": error})
    elif shape == "set-free-nested":
        logger.error("failed: %s", ([{"k": (error,)}],))
    elif shape == "mapping":
        logger.error("failed: %(e)s", {"e": error})
    else:
        logger.error("failed: %r", error)
    (record,) = capture.records
    assert _clean(record, s), record.getMessage()
    assert "validation error for McpTaskInfo" in record.getMessage()


def test_msg_that_is_an_exception_is_scrubbed(capture: _Capture) -> None:
    s = _FAMILIES["hex"][1]
    logging.getLogger("client").error(_validation_error(s))
    (record,) = capture.records
    assert _clean(record, s)
    assert "validation error for McpTaskInfo" in record.getMessage()


def test_stack_info_carries_no_exception_text(capture: _Capture) -> None:
    """`stack_info` renders frames and source lines, never an exception's
    text, so the scrubber leaves it; this pins that it stays clean inside an
    `except` block holding a validation error."""
    s = _FAMILIES["digits"][1]
    try:
        raise _validation_error(s)
    except ValidationError:
        logging.getLogger("client").warning("inside", stack_info=True)
    (record,) = capture.records
    assert record.stack_info
    assert _clean(record, s)


def test_other_exceptions_and_values_pass_unchanged(capture: _Capture) -> None:
    logger = logging.getLogger("third.party")
    plain = RuntimeError("plain failure")
    try:
        raise plain
    except RuntimeError:
        logger.error("x %r %s", plain, {"k": 1}, exc_info=True)
    (record,) = capture.records
    assert record.args == (plain, {"k": 1})
    assert record.exc_info is not None and record.exc_info[1] is plain
    assert "RuntimeError('plain failure')" in record.getMessage()
    assert "plain failure" in "".join(traceback.format_exception(*record.exc_info))


def test_install_is_idempotent_and_wraps_the_previous_factory() -> None:
    from pmcp.argument_errors import install_log_scrubber

    original = logging.getLogRecordFactory()
    marked: list[Any] = []

    def custom(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original(*args, **kwargs)
        marked.append(record)
        return record

    logging.setLogRecordFactory(custom)
    try:
        install_log_scrubber()
        installed = logging.getLogRecordFactory()
        install_log_scrubber()
        assert logging.getLogRecordFactory() is installed
        assert getattr(installed, "previous", None) is custom
        logging.getLogger("third.party").warning("hello")
        assert marked, "the previous factory was not called"
    finally:
        logging.setLogRecordFactory(original)
        install_log_scrubber()


def test_the_gateway_installs_the_scrubber(tmp_path: Any) -> None:
    from pmcp.server import GatewayServer

    original = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    try:
        policy = tmp_path / "policy.json"
        policy.write_text("{}")
        GatewayServer(policy_path=policy, cache_dir=tmp_path / "cache")
        assert getattr(logging.getLogRecordFactory(), "pmcp_validation_scrubber", False)
    finally:
        logging.setLogRecordFactory(original)


# --- every entry point installs it (rev 5, rev 4 board B1) --------------------

_MARKER = "import logging; print(getattr(logging.getLogRecordFactory(), 'pmcp_validation_scrubber', False))"


def _console_scripts() -> list[str]:
    """The console scripts pmcp's distribution declares (pyproject's
    `[project.scripts]`), as `module:attr`."""
    from importlib.metadata import distribution

    return sorted(
        ep.value
        for ep in distribution("pmcp").entry_points
        if ep.group == "console_scripts"
    )


def _entry_points() -> dict[str, str]:
    """A fresh interpreter per entry point: the snippet enters pmcp that way
    and then prints whether the record factory is the scrubber."""
    snippets = {
        f"import {module}": f"import {module}\n{_MARKER}"
        for module in (
            "pmcp",
            "pmcp.cli",
            "pmcp.manifest.refresher",
            "pmcp.transport.http",
        )
    }
    snippets["python -m pmcp --version"] = (
        "import runpy, sys\nsys.argv = ['pmcp', '--version']\n"
        "try:\n    runpy.run_module('pmcp', run_name='__main__')\n"
        "except SystemExit:\n    pass\n" + _MARKER
    )
    for value in _console_scripts():
        module, _, attr = value.partition(":")
        snippets[f"console script {value}"] = (
            f"import importlib\ngetattr(importlib.import_module({module!r}), {attr!r})\n{_MARKER}"
        )
    return snippets


@pytest.mark.parametrize("entry", sorted(_entry_points()))
def test_every_entry_point_installs_the_scrubber(entry: str) -> None:
    import subprocess
    import sys

    assert _console_scripts(), "pmcp declares no console script?"
    result = subprocess.run(
        [sys.executable, "-c", _entry_points()[entry]],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True", (entry, result.stdout)


_CLI_REFRESH = """
import json, sys
from pathlib import Path
import pmcp.cli as cli
import pmcp.manifest.refresher as refresher
from pmcp.manifest.loader import ServerConfig

tmp, script = Path(sys.argv[1]), sys.argv[2]
config = ServerConfig(name="d", description="d", keywords=[], install={},
                      command=sys.executable, args=[script, str(tmp / "case.json")])

class _Manifest:
    servers = {"d": config}
    def get_server(self, name):
        return self.servers.get(name)

# Only the manifest lookup is replaced: the downstream is the test's.
refresher.load_manifest = lambda *a, **k: _Manifest()
sys.argv = ["pmcp", "refresh", "--server", "d", "--force",
            "--cache-dir", str(tmp / "cache"), "-l", "info"]
try:
    cli.main()
except SystemExit:
    pass
"""


@pytest.mark.parametrize(
    "kind",
    [
        "notifications/message",
        "notifications/progress",
        "notifications/resources/updated",
    ],
)
def test_pmcp_refresh_logs_no_downstream_value(tmp_path: Any, kind: str) -> None:
    """The operator's `pmcp refresh` (the real `pmcp.cli.main`, in a fresh
    interpreter) against a downstream whose malformed notification the
    SDK's `ClientSession` rejects: neither stderr nor `.pmcp/logs/gateway.log`
    carries the value."""
    import json
    import os
    import subprocess
    import sys

    from tests.test_downstream_frame_echo import _SESSION_MESSAGES, _SESSION_SCRIPT

    script = tmp_path / "session_downstream.py"
    script.write_text(_SESSION_SCRIPT)
    driver = tmp_path / "cli_refresh.py"
    driver.write_text(_CLI_REFRESH)
    for family in ("hex", "alpha", "unicode"):
        s = _FAMILIES[family][1]
        (tmp_path / "case.json").write_text(
            json.dumps([kind, _SESSION_MESSAGES[kind](s)])
        )
        (tmp_path / "case.json.in").unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(driver), str(tmp_path), str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp_path,
            env={**os.environ, "HOME": str(tmp_path)},
        )
        log_file = tmp_path / ".pmcp" / "logs" / "gateway.log"
        log = log_file.read_text(errors="replace") if log_file.exists() else ""
        for channel, text in (
            ("stdout", result.stdout),
            ("stderr", result.stderr),
            ("log file", log),
        ):
            assert not any(form in text for form in _forbidden(s)), (
                kind,
                family,
                channel,
                text,
            )
        # No vacuous pass: the refresh ran, and the SDK rejected the message.
        assert "Refreshing server: d" in result.stdout, result.stdout + result.stderr
        assert "Failed to validate notification" in result.stderr + log, (
            kind,
            result.stderr,
        )
