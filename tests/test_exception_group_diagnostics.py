"""`describe_exception` must name the cause inside an exception group.

Consiliency/pmcp#224. Every remote-transport path in `ClientManager` runs
inside an anyio task group, and `str(ExceptionGroup)` is
``"unhandled errors in a TaskGroup (1 sub-exception)"`` -- it names neither the
type nor the message of what actually failed. A week of CI hangs
(Consiliency/pmcp#200) produced exactly that string from six call sites and
nothing else, which is why the root cause is still unknown.

The interpreter matters here and is the reason for duck-typing: on 3.11+ anyio
raises the builtin `BaseExceptionGroup`; on 3.10 it raises
`exceptiongroup.ExceptionGroup` from the backport. The tests below build the
group through a real `anyio` task group rather than constructing one directly,
so they exercise whichever type this interpreter actually produces.
"""

from __future__ import annotations

import ast
import inspect
import logging
import pathlib
import traceback
from unittest.mock import MagicMock

import anyio
import pytest

from pmcp.client import manager as manager_module
from pmcp.client.manager import (
    _MAX_DESCRIBED_LEAVES,
    ClientManager,
    ManagedClient,
    _iter_leaf_exceptions,
    describe_exception,
)
from pmcp.types import ServerStatus, ServerStatusEnum


async def _raise(exc: BaseException) -> None:
    raise exc


async def _group_from_anyio(*excs: BaseException) -> BaseException:
    """The group this interpreter's anyio actually raises, not a hand-built one."""
    try:
        async with anyio.create_task_group() as tg:
            for exc in excs:
                tg.start_soon(_raise, exc)
    except BaseException as caught:  # noqa: BLE001 - the point of the helper
        return caught
    raise AssertionError("the task group did not raise")


class TestTheDefect:
    async def test_a_real_task_group_stringifies_to_nothing_useful(self) -> None:
        # Pin the defect itself: if this ever stops being true, the whole
        # helper is unnecessary and should go.
        group = await _group_from_anyio(ValueError("the real cause"))
        assert "the real cause" not in str(group)
        assert "sub-exception" in str(group)

    async def test_describe_exception_names_the_cause(self) -> None:
        group = await _group_from_anyio(ValueError("the real cause"))
        described = describe_exception(group)
        assert "ValueError" in described
        assert "the real cause" in described


class TestLeafIteration:
    async def test_a_plain_exception_is_its_own_leaf(self) -> None:
        exc = RuntimeError("plain")
        assert list(_iter_leaf_exceptions(exc)) == [exc]

    async def test_every_leaf_of_a_multi_error_group_is_named(self) -> None:
        group = await _group_from_anyio(
            ValueError("first"), KeyError("second"), OSError("third")
        )
        described = describe_exception(group)
        for fragment in (
            "ValueError",
            "first",
            "KeyError",
            "second",
            "OSError",
            "third",
        ):
            assert fragment in described, described

    async def test_nested_groups_are_flattened(self) -> None:
        inner = await _group_from_anyio(ValueError("buried"))
        outer = await _group_from_anyio(inner)
        leaves = list(_iter_leaf_exceptions(outer))
        assert [type(leaf) for leaf in leaves] == [ValueError]
        assert "buried" in describe_exception(outer)

    def test_an_object_with_an_exceptions_attribute_is_not_a_group(self) -> None:
        # Duck-typing has to be narrow: `.exceptions` holding non-exceptions is
        # not an exception group, and treating it as one would drop the real
        # error entirely.
        class NotAGroup(Exception):
            exceptions = ["not", "exceptions"]

        exc = NotAGroup("the actual message")
        assert list(_iter_leaf_exceptions(exc)) == [exc]
        assert "the actual message" in describe_exception(exc)

    def test_a_self_referential_group_terminates(self) -> None:
        # Depth-capped rather than cycle-detected: a diagnostic helper must not
        # be the thing that hangs the process it is diagnosing.
        class Recursive(Exception):
            @property
            def exceptions(self) -> tuple[BaseException, ...]:
                return (self,)

        exc = Recursive("loops")
        leaves = list(_iter_leaf_exceptions(exc))
        assert leaves == [exc]

    async def test_many_leaves_are_capped_and_the_remainder_counted(self) -> None:
        count = _MAX_DESCRIBED_LEAVES + 3
        group = await _group_from_anyio(
            *[ValueError(f"cause-{i}") for i in range(count)]
        )
        described = describe_exception(group)
        assert f"{count} sub-exceptions" in described
        assert "and 3 more" in described


class TestRedaction:
    """Flattening increases how much of an exception reaches the log, and these
    come from an HTTP transport. Every path must stay redacted."""

    @pytest.mark.parametrize(
        ("secret", "message"),
        [
            ("sk-topsecretvalue", "Authorization: Bearer sk-topsecretvalue"),
            (
                "tok-abcdef123456",
                "failed for https://api.example.com/x?token=tok-abcdef123456",
            ),
        ],
    )
    async def test_a_secret_inside_a_group_leaf_is_redacted(
        self, secret: str, message: str
    ) -> None:
        group = await _group_from_anyio(ConnectionError(message))
        described = describe_exception(group)
        assert secret not in described, described
        assert "REDACTED" in described

    async def test_a_secret_in_a_plain_exception_is_redacted_too(self) -> None:
        # The single-exception path returns early; it must not skip redaction.
        described = describe_exception(
            ConnectionError("Authorization: Bearer sk-anothersecret")
        )
        assert "sk-anothersecret" not in described
        assert "REDACTED" in described


class TestTheCallSitesAreWired:
    """The helper existing is not the fix; the call sites using it is.

    Every test above would still pass if a call site were reverted to
    `f"...: {e}"`, which is exactly how this defect survived: the sites looked
    fine in isolation and only lied when handed a group.
    """

    async def test_a_failing_remote_disconnect_logs_the_leaf_not_the_group(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = ClientManager()
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        managed = ManagedClient(
            config=MagicMock(),
            process=None,
            is_remote=True,
            write_stream=MagicMock(),
            status=status,
        )
        manager._clients["remote"] = managed
        manager._servers["remote"] = status

        group = await _group_from_anyio(ConnectionResetError("peer went away"))

        async def _raise_group(*args: object, **kwargs: object) -> None:
            raise group

        manager._close_remote_transport = _raise_group  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING):
            ok, _cancelled, error = await manager.disconnect_server(
                "remote", force=True
            )

        assert ok is False
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "ConnectionResetError" in logged, logged
        assert "peer went away" in logged, logged
        # The returned error reaches the caller of the public API, so it must
        # not be the useless group string either.
        assert "ConnectionResetError" in error, error
        assert error != str(group)

    def test_no_caught_exception_in_manager_is_stringified_bare(self) -> None:
        """Structural guard: inside `except ... as e`, `e` may not become a
        string except through a call.

        **The first version of this guard only looked inside `logger.*` calls,
        and that was a false green.** It passed while
        `read_failure_reason = f"stdout read error: {e}"` (logged twice *and*
        stored as `last_error`), four `last_error = str(e)` assignments, and the
        error list `connect_server` returns to its caller all still rendered a
        group as "unhandled errors in a TaskGroup". Those reach `pmcp status`,
        `pmcp doctor`, health output and provision messages — further than any
        log line.

        So the rule is the broad one: within a handler that binds a name,
        neither `f"...{name}..."` nor `str(name)` may appear anywhere.
        `describe_exception(name)` is a `Call`, so the fix passes.
        """
        source = pathlib.Path(inspect.getsourcefile(manager_module) or "").read_text()
        tree = ast.parse(source)
        offenders: list[str] = []

        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or not handler.name:
                continue
            bound = handler.name
            for node in ast.walk(handler):
                if (
                    isinstance(node, ast.FormattedValue)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == bound
                ):
                    offenders.append(
                        f"line {node.lineno}: f-string interpolates `{{{bound}}}`"
                    )
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "str"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == bound
                ):
                    offenders.append(f"line {node.lineno}: calls `str({bound})`")

        assert not offenders, (
            "these render a caught exception directly, so an ExceptionGroup "
            "becomes 'unhandled errors in a TaskGroup' and names nothing "
            "(Consiliency/pmcp#224). Use describe_exception():\n  "
            + "\n  ".join(sorted(set(offenders)))
        )

    def test_the_guard_catches_an_indirect_interpolation(self) -> None:
        """The guard must fail on the shape that defeated its first version.

        A criterion that only passes is not a criterion: this feeds the guard's
        own logic a handler that assigns `f"{e}"` to a local and only logs it
        later, and requires a finding.
        """
        snippet = (
            "try:\n"
            "    pass\n"
            "except Exception as e:\n"
            "    reason = f'boom: {e}'\n"
            "    logger.warning(reason)\n"
        )
        tree = ast.parse(snippet)
        found = [
            node
            for handler in ast.walk(tree)
            if isinstance(handler, ast.ExceptHandler) and handler.name
            for node in ast.walk(handler)
            if isinstance(node, ast.FormattedValue)
            and isinstance(node.value, ast.Name)
            and node.value.id == handler.name
        ]
        assert found, "the guard would not catch an indirect interpolation"

    async def test_last_error_names_the_cause_not_the_group(self) -> None:
        """`last_error` reaches `pmcp status`, `pmcp doctor` and health output.

        It was `str(e)` at four sites, so a user asking why a server is offline
        was told "unhandled errors in a TaskGroup (1 sub-exception)".
        """
        manager = ClientManager()
        group = await _group_from_anyio(ConnectionResetError("peer went away"))

        async def _raise_group(*args: object, **kwargs: object) -> None:
            raise group

        manager._connect_singleflight = _raise_group  # type: ignore[method-assign]
        config = MagicMock()
        config.name = "remote"
        manager._servers["remote"] = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )

        errors = await manager.connect_server(config)

        assert errors and "ConnectionResetError" in errors[0], errors
        assert "peer went away" in errors[0], errors
        last_error = manager._servers["remote"].last_error or ""
        assert "ConnectionResetError" in last_error, last_error
        assert last_error != str(group)

    def test_no_raw_exception_reaches_the_log_via_exc_info(self) -> None:
        """`exc_info=` hands the RAW exception to the logging machinery.

        Python then appends the unredacted exception tree *after* the sanitised
        message, so a bearer token in a transport error reached the log in full
        while the message above it looked clean. `record.getMessage()` cannot
        see that, which is why the first version of these tests missed it.
        Formatting the traceback ourselves and sanitising it keeps the frames
        and closes the hole.
        """
        source = pathlib.Path(inspect.getsourcefile(manager_module) or "").read_text()
        tree = ast.parse(source)
        offenders = [
            f"line {kw.value.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
            for kw in node.keywords
            if kw.arg == "exc_info"
        ]
        assert not offenders, (
            "logger calls passing exc_info attach an unsanitised traceback; "
            "format it and pass it through sanitize_auth_diagnostic instead "
            "(Consiliency/pmcp#224):\n  " + "\n  ".join(offenders)
        )

    def test_a_formatted_traceback_is_sanitised(self) -> None:
        from pmcp.auth import sanitize_auth_diagnostic

        try:
            raise ConnectionError("Authorization: Bearer sk-tracebacksecret")
        except ConnectionError as exc:
            text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        assert "sk-tracebacksecret" in text, "the fixture must contain the secret"
        cleaned = sanitize_auth_diagnostic(text, max_length=None)
        assert "sk-tracebacksecret" not in cleaned, cleaned
        # The frames must survive redaction, or this trade was not worth making.
        assert "Traceback (most recent call last)" in cleaned
        assert "test_a_formatted_traceback_is_sanitised" in cleaned
