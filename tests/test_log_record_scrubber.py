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
