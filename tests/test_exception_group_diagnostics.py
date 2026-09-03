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

    def test_no_logged_exception_in_manager_is_interpolated_bare(self) -> None:
        """Structural guard: a future edit must not reintroduce `{e}`.

        Walks the AST for f-strings inside `except ... as <name>` handlers that
        interpolate the bound name directly into a `logger.*` call. `str()` of
        an exception group is the defect; `describe_exception(...)` is the fix,
        so the bound name may only appear through a call.
        """
        source = pathlib.Path(inspect.getsourcefile(manager_module) or "").read_text()
        tree = ast.parse(source)
        offenders: list[str] = []

        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or not handler.name:
                continue
            bound = handler.name
            for node in ast.walk(handler):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "logger"
                ):
                    continue
                for fstring in ast.walk(node):
                    if not isinstance(fstring, ast.FormattedValue):
                        continue
                    if (
                        isinstance(fstring.value, ast.Name)
                        and fstring.value.id == bound
                    ):
                        offenders.append(
                            f"line {fstring.lineno}: logs `{{{bound}}}` directly"
                        )

        assert not offenders, (
            "these logger calls interpolate a caught exception directly, so an "
            "ExceptionGroup renders as 'unhandled errors in a TaskGroup' and "
            "names nothing (Consiliency/pmcp#224). Use describe_exception():\n  "
            + "\n  ".join(offenders)
        )
