"""MCP Client Manager - Manages connections to downstream MCP servers."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, contextmanager
import contextvars
import json
import logging
import os
from pathlib import Path
import random
import re
import signal
import traceback
import string
import sys
import time
import weakref
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Iterator, TypeVar

import httpx2
import mcp.types as mcp_types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

from pmcp.auth import sanitize_auth_diagnostic
from pmcp.config.loader import make_tool_id
from pmcp.env_store import sanitized_subprocess_env
from pmcp.manifest.installer import _operator_safe, _render_install_argv
from pmcp.remote_auth import (
    MissingRemoteHeaderAuthError,
    resolve_remote_headers_for_tenant,
)
from pmcp.subscriptions import CatalogEventSink
from pmcp.validation import normalized_executable_name
from pmcp.types import (
    LocalMcpServerConfig,
    UNUSABLE_TASK_VALUE,
    task_hint_is_usable,
    McpTaskInfo,
    McpTaskRecord,
    PromptArgumentInfo,
    PromptInfo,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    RequestState,
    ResourceInfo,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    TaskMetadataInput,
    TaskSupportMode,
    ToolInfo,
    TraceContextInfo,
)
from pmcp.waits import bounded_wait

resource_module: ModuleType | None

try:
    import resource as resource_module

    HAS_RESOURCE = True
except ImportError:
    resource_module = None
    HAS_RESOURCE = False

logger = logging.getLogger(__name__)

#: Executables that fetch and run the package they are given at spawn time,
#: by NORMALIZED name (`normalized_executable_name`), so ``UVX.EXE``,
#: ``pnpx.cmd`` and ``C:\\tools\\npx.cmd`` are runners without being listed.
_PACKAGE_RUNNER_EXECUTABLES = frozenset({"npx", "uvx", "pnpx", "bunx"})

# --- exception-group flattening ------------------------------------------------

# How many leaf exceptions to name before summarising the rest. A TaskGroup
# failure carries one cause in practice; the cap exists so a pathological group
# cannot push the useful part past `sanitize_auth_diagnostic`'s truncation.
_MAX_DESCRIBED_LEAVES = 5
_MAX_GROUP_DEPTH = 10


def _iter_leaf_exceptions(
    exc: BaseException, _depth: int = 0
) -> Iterator[BaseException]:
    """Yield the concrete exceptions inside an exception group, recursively.

    Detected by duck-typing `.exceptions` rather than `isinstance`, because the
    type differs by interpreter: 3.11+ raises the builtin `BaseExceptionGroup`,
    while on 3.10 anyio raises `exceptiongroup.ExceptionGroup` from the
    backport (verified: both expose `.exceptions`, and neither name is
    importable on the other interpreter). Every element is checked to be a
    `BaseException`, so an unrelated class that merely has an `exceptions`
    attribute is not mistaken for a group.
    """
    nested = getattr(exc, "exceptions", None)
    if (
        _depth < _MAX_GROUP_DEPTH
        and isinstance(nested, (tuple, list))
        and nested
        and all(isinstance(item, BaseException) for item in nested)
    ):
        for item in nested:
            yield from _iter_leaf_exceptions(item, _depth + 1)
    else:
        yield exc


def describe_exception(exc: BaseException) -> str:
    """Render an exception for a log line, naming the cause inside a group.

    `str(ExceptionGroup)` is `"unhandled errors in a TaskGroup (1 sub-exception)"`
    -- it names neither the type nor the message of the thing that actually
    failed. Every remote-transport path in this class runs inside an anyio task
    group, so that string is what these call sites logged for *any* failure, and
    a week of CI hangs (Consiliency/pmcp#200) produced exactly it and nothing
    else. See #224.

    The result goes through `sanitize_auth_diagnostic`, which redacts URLs,
    bearer tokens and API keys. That is not optional: flattening *increases* how
    much of an exception's text reaches the log, and these exceptions come from
    an HTTP transport whose messages can carry a URL or an Authorization header.
    Most of these call sites logged the raw exception before this change; all of
    them are redacted now.
    """
    leaves = list(_iter_leaf_exceptions(exc))
    if len(leaves) == 1 and leaves[0] is exc:
        return sanitize_auth_diagnostic(exc)

    shown = leaves[:_MAX_DESCRIBED_LEAVES]
    rendered = "; ".join(
        f"{type(leaf).__name__}: {leaf}" if str(leaf) else type(leaf).__name__
        for leaf in shown
    )
    if len(leaves) > len(shown):
        rendered += f"; ... and {len(leaves) - len(shown)} more"
    plural = "" if len(leaves) == 1 else "s"
    return sanitize_auth_diagnostic(
        f"{type(exc).__name__}({len(leaves)} sub-exception{plural}): {rendered}"
    )


_TaskT = TypeVar("_TaskT", bound=asyncio.Task[Any])


# --- Cancellation during teardown (Consiliency/pmcp#324) -----------------------
#
# The rule every teardown below follows:
#
# * Waits for a child task the teardown cancelled go through `asyncio.wait`,
#   which never raises the child's outcome. So a `CancelledError` out of any
#   teardown await is the CALLER's -- never ambiguous with the child's -- and
#   nothing in a teardown catches a `CancelledError` in order to absorb it.
# * When the caller is cancelled mid-teardown, the teardown switches to a
#   synchronous path (`ClientManager._abandon_client_io`) that does only what
#   cannot be interrupted: SIGKILL the process tree, cancel the client's tasks
#   without awaiting them, drop it from the registries. Then it re-raises.
#   That path has no `await`, so nothing in it can be cancelled before it
#   runs, hang, or hold a lock across a suspension.
# * The non-cancelled path keeps the graceful behaviour (SIGTERM grace,
#   graceful remote close).


def _retrieve_outcome(task: asyncio.Future[Any]) -> None:
    """Done-callback for a task that was cancelled and is not awaited: mark
    its outcome retrieved (no "exception was never retrieved"), and log a
    genuine failure by type only."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug(f"abandoned task ended with {type(exc).__name__}")


def _cancel_without_waiting(task: asyncio.Future[Any] | None) -> None:
    if task is not None and not task.done():
        task.cancel()
        task.add_done_callback(_retrieve_outcome)


# How often an abandoned transport owner is re-cancelled until it ends.
_OWNER_RECANCEL_S = 0.5


def _abandon_owner(
    task: asyncio.Future[Any] | None,
    shutdown: asyncio.Event | None,
    on_done: Callable[[asyncio.Future[Any]], None] = _retrieve_outcome,
) -> None:
    """Abandon a remote transport owner without awaiting it.

    Signal its shutdown and cancel it now (an owner still entering its
    transport ends at once), then cancel it again from loop callbacks -- on
    the next iteration, after the owner has woken from `shutdown.wait()` and
    entered its transport's `__aexit__`, then every `_OWNER_RECANCEL_S` until
    it ends. The immediate cancel alone is consumed while the owner is parked
    on `shutdown.wait()`, and a dead peer's `__aexit__` would then block
    uncancelled (measured). Callbacks only, no task: nothing here
    can be cancelled before it runs or hold its caller (Consiliency/pmcp#324).
    """
    if task is None or task.done():
        return
    if shutdown is not None:
        shutdown.set()
    task.add_done_callback(on_done)
    loop = asyncio.get_running_loop()

    def kick() -> None:
        if not task.done():
            task.cancel()
            loop.call_later(_OWNER_RECANCEL_S, kick)

    task.cancel()  # ends an owner still entering its transport at once
    loop.call_soon(kick)


async def _reap_child(
    task: asyncio.Future[Any] | None, *, timeout: float | None = None
) -> None:
    """Cancel a child task this teardown owns and wait (bounded by
    ``timeout``) for it to end.

    `asyncio.wait` never raises the child's outcome, so the child's own
    ``CancelledError`` is not seen here at all, and a ``CancelledError`` out
    of this await can only be the caller's -- it propagates. This is what
    lets the teardowns below tell the two apart without a private task.
    """
    if task is None:
        return
    if not isinstance(task, asyncio.Future):
        # A test double, not a task: cancel it unless done and move on, as
        # the old `shield` + `except Exception` path effectively did.
        # `asyncio.wait` on such an object would wait forever on 3.11+.
        if not task.done():
            task.cancel()
        return
    if not task.done():
        task.cancel()
        await asyncio.wait({task}, timeout=timeout)
    if task.done():
        _retrieve_outcome(task)
    else:
        task.add_done_callback(_retrieve_outcome)


def _log_abandoned_owner_failure(name: str, task: asyncio.Future[Any]) -> None:
    """Done-callback for a transport owner a cancelled caller escalated and
    did not wait for: log a genuine unwind failure (sanitised traceback, as
    `_close_remote_transport` always has) rather than drop it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    # Formatted and sanitised rather than passed as `exc_info=`: `exc_info`
    # appends the unredacted exception tree after the sanitised message.
    traceback_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    logger.warning(
        f"[{name}] remote transport failed to unwind after our caller's "
        f"cancellation: {describe_exception(exc)}\n"
        f"{sanitize_auth_diagnostic(traceback_text, max_length=None)}"
    )


def _handshake_error(exc: BaseException) -> str:
    """`last_error` for a failed handshake. A cancellation renders as an
    empty string through `describe_exception`, so name it."""
    if isinstance(exc, asyncio.CancelledError):
        return "Connection cancelled"
    return describe_exception(exc)


# The three catalog kinds, in the order reconciliation fetches and applies them.
# Iterating this rather than three hand-written branches is what keeps
# "each kind is handled independently" true as kinds are added.
CATALOG_KINDS: tuple[str, str, str] = ("tools", "resources", "prompts")

# IF-0-FANOUT-1: the downstream `notifications/*` methods this gateway acts on,
# mapped to the catalog kind whose entries decide whether reconciliation
# publishes. Every method absent from this mapping is a no-op by construction --
# see `ClientManager._handle_downstream_notification`.
DOWNSTREAM_LIST_CHANGED_METHODS: dict[str, str] = {
    "notifications/tools/list_changed": "tools",
    "notifications/resources/list_changed": "resources",
    "notifications/prompts/list_changed": "prompts",
}

# Minimum gap between consecutive reconciles of the same server. The first
# reconcile after a notification is never delayed; only a *re-run* waits. This
# bounds the one pathological case coalescing alone does not: a downstream that
# emits `list_changed` in response to the `tools/list` that reconciliation
# itself issues, which would otherwise drive the re-run loop forever at full
# speed. With the debounce that server costs one reconcile per interval instead
# of a hot spin, and notifications arriving during the wait still collapse into
# the single pending re-run.
_RECONCILE_RERUN_DEBOUNCE_S = 0.25

# Ceiling on `nextCursor` follows for one catalog kind (Consiliency/pmcp#173).
# A downstream that always returns a cursor would otherwise spin the fetch
# forever, and reconciliation runs on every downstream notification, so the
# loop is reachable by a misbehaving peer rather than only at connect.
#
# Exceeding it makes the kind UNREADABLE, not partial -- indexing a truncated
# view is what #173 was about, so the bound must not resolve to "keep what we
# got".
#
# That cuts both ways, which is why this is 500 and not the 50 first written
# here (ah board review). Failing closed on an HONEST server is its own defect:
# page size is the server's choice and 1 is legal, so a catalog of 100 tools --
# `max_tools_per_server`'s own default -- can legitimately need 100 round trips,
# and a 50-page cap would report that server as having no tools at all. The cap
# is a runaway guard, not a catalog-size limit; 500 sequential round trips for
# one kind is already pathological by any honest reading, while leaving real
# small-page servers well inside it.
_MAX_LISTING_PAGES = 500


class OutboundFrameNotJson(ValueError):
    """An outbound frame is not strict JSON (Consiliency/pmcp#298)."""


_OUTBOUND_NOT_JSON = "outbound frame is not strict JSON"


def _encode_outbound_frame(payload: dict[str, Any]) -> str:
    """One JSON-RPC frame as strict JSON, or a value-free refusal.

    NaN and +-Infinity are not JSON: stdio would write them as bare literals a
    strict peer cannot parse, and the SDK's HTTP and SSE clients would
    silently send them as ``null``. A value ``json`` cannot encode at all
    (``TypeError``), a cycle (``ValueError``) or runaway nesting
    (``RecursionError``) is refused the same way, on every transport, with a
    message that carries nothing from the frame (Consiliency/pmcp#298).
    """
    try:
        return json.dumps(payload, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        pass
    # Raised OUTSIDE the handler: `from None` would still leave the json
    # error attached as `__context__`, whose text can carry the value
    # (`...: nan` on 3.12+, a dict key on 3.13).
    raise OutboundFrameNotJson(_OUTBOUND_NOT_JSON)


class DownstreamError(Exception):
    """A JSON-RPC `error` object returned by a downstream MCP server.

    Preserves the `code` and `data` members alongside `message`, which the two
    dispatch paths previously discarded.

    Scoped to the `ClientManager` boundary by design. `gateway.invoke` maps every
    exception to `E302` through `str(e)`, so `str()` here is exactly the
    downstream `message` and nothing about the `gateway.*` error contract
    changes. Surfacing `code`/`data` to MCP clients is a separate contract change
    tracked as a follow-up (EC-FANOUT-7).
    """

    def __init__(
        self, message: str, *, code: int | None = None, data: Any = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data


def _downstream_error(error: Any) -> DownstreamError:
    """Build a `DownstreamError` from a JSON-RPC `error` member."""
    if not isinstance(error, dict):
        return DownstreamError(str(error))
    return DownstreamError(
        str(error.get("message", "Unknown error")),
        code=error.get("code"),
        data=error.get("data"),
    )


class _ConnectRefused(Exception):
    """A connect refused by `ClientManager._admit_connect` -- the one check
    every connect path and `adopt_process` pass before they spawn, join a
    connect in flight, or register a client (Consiliency/pmcp#324). Not a
    connection failure: `_connect_with_retry` re-raises it instead of
    retrying, and the reconnect loop stops on it."""


class _ManagerAbandoned(_ConnectRefused):
    """A connect or adoption refused because `ClientManager.abandon_all_now()`
    ran: the gateway is shutting down, and no connect path and no
    `adopt_process` spawns or registers a client after it, whenever it was
    requested (Consiliency/pmcp#324, codex rounds 1 and 2 on the
    implementation)."""


class _ConnectSuperseded(_ConnectRefused):
    """A connect refused because it was requested before a
    `disconnect_server(name)` that was cancelled before it took the lifecycle
    lock. Uncancelled, that disconnect would have run after this connect and
    torn down what it produced; cancelled, it never runs, so the connect is
    refused instead (Consiliency/pmcp#324, round 2 on the implementation)."""


# Every ticket is below this, so `abandon_all_now()` setting the global
# supersession point to it makes every connect stale, for every name, forever.
_ABANDONED_AT = sys.maxsize


@dataclass(frozen=True)
class _ConnectTicket:
    """The lifecycle generation a connect captured when it was REQUESTED,
    before it waited for the lifecycle lock. Carried from the entry point to
    `_admit_connect` in `_CONNECT_TICKET`; `asyncio.create_task` copies the
    context, so the per-name tasks a request starts carry it too."""

    owner: ClientManager
    seq: int


_CONNECT_TICKET: contextvars.ContextVar[_ConnectTicket | None] = contextvars.ContextVar(
    "pmcp_connect_ticket", default=None
)


class _NullCatalogEventSink:
    """No-op `CatalogEventSink` used when `ClientManager` is constructed with
    no `catalog_events` (IF-0-P3B-2's default). Keeps every pre-P3B
    construction site — `server.py`, `cli.py`, and ~20 test modules — working
    unchanged: nothing publishes, but nothing raises either."""

    def note_tools_changed(self) -> None:
        pass

    def note_resources_changed(self) -> None:
        pass

    def note_prompts_changed(self) -> None:
        pass

    async def flush(self) -> None:
        pass


def _process_groups_supported() -> bool:
    """Whether this platform has POSIX process groups to signal. Windows has
    neither `os.killpg` nor `os.getpgid`: there every group path is skipped
    and teardown uses the single-process `terminate()`/`kill()` fallback, as
    on main (codex round 7: a retained id reached `os.killpg` there and the
    `AttributeError` failed `disconnect_server`). Read at call time, so a
    test that removes the APIs sees it."""
    return hasattr(os, "killpg") and hasattr(os, "getpgid")


def _spawned_group_pgid(process: asyncio.subprocess.Process) -> int | None:
    """The group id of a process pmcp spawned with `start_new_session=True`:
    its pid (the spawn contract), or `None` where groups do not exist."""
    return process.pid if _process_groups_supported() else None


def _own_group_pgid(process: asyncio.subprocess.Process | None) -> int | None:
    """The process group an *adopted* downstream leads, read while it is
    still ours (spawned servers take it from the spawn contract instead).

    stdio servers are spawned with ``start_new_session=True``, so the child
    calls ``setsid()`` before ``exec`` and its pgid equals its pid; an adopted
    process may or may not lead a group. Read once, when the client is
    created -- the leader is then alive or an unreaped zombie, and
    ``os.getpgid`` answers for both -- so the group can still be killed
    after asyncio has reaped the leader (codex round 5: a grandchild that
    outlived its leader survived every teardown). ``None`` when the process
    does not lead its own group, so the gateway's own group is never kept.
    """
    if process is None or process.returncode is not None:
        return None
    pid = process.pid
    if not isinstance(pid, int) or not _process_groups_supported():
        return None
    try:
        return pid if os.getpgid(pid) == pid else None
    except (ProcessLookupError, PermissionError, OSError):
        return None


def _kill_retained_group(group_pgid: int | None) -> None:
    """SIGKILL a group whose leader asyncio has already reaped, synchronously.

    Only while (1) the group still has a member (``killpg(pgid, 0)``) and (2)
    no process holds the leader's pid (``kill(pgid, 0)`` fails with
    ``ProcessLookupError``). While any member of our group lives, the kernel
    keeps the id in use, so it cannot have been handed to a new process; a
    process holding that pid therefore means the id was reused after our
    group emptied, and the group is left alone. Residual: the id is reused
    by a new session leader that then exits while its own children live, all
    between our reap and this call -- the pid space must cycle in that window.
    """
    if group_pgid is None or not _process_groups_supported():
        return
    try:
        os.killpg(group_pgid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return  # no member left (or not ours to signal)
    try:
        os.kill(group_pgid, 0)
        return  # a live process holds the leader's pid: the id was reused
    except ProcessLookupError:
        pass
    except (PermissionError, OSError):
        return
    try:
        os.killpg(group_pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _kill_process_tree_now(
    process: asyncio.subprocess.Process | None,
    *,
    group_pgid: int | None = None,
) -> None:
    """SIGKILL a downstream process and its group, synchronously.

    The uninterruptible step of a cancelled teardown (Consiliency/pmcp#324):
    no await, so nothing can cancel or skip it. The leader is signalled only
    while ``process.returncode is None`` -- once asyncio has reaped it, its
    pid may be reused. Its group is signalled when the leader leads it (taken
    from ``os.getpgid`` while the leader is unreaped) or when ``group_pgid``
    was cached earlier by `_terminate_process_tree` and the group is still
    alive.
    """
    if not _process_groups_supported():
        group_pgid = None
    if process is None:
        _kill_retained_group(group_pgid)
        return
    pid = process.pid
    if process.returncode is not None:
        # The leader is reaped: never signal its pid; the group only through
        # the retained, checked id (codex round 5).
        _kill_retained_group(group_pgid)
        return
    if isinstance(pid, int):
        if group_pgid is None and hasattr(os, "getpgid"):
            try:
                if os.getpgid(pid) == pid:
                    group_pgid = pid
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
    if group_pgid is not None and hasattr(os, "killpg"):
        try:
            os.killpg(group_pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


async def _terminate_process_tree(
    process: asyncio.subprocess.Process | None,
    name: str,
    *,
    group_pgid: int | None = None,
) -> None:
    """Terminate a downstream process and its whole process group.

    stdio servers are spawned with ``start_new_session=True``, so the child is a
    process-group leader; signalling the group also reaps grandchildren (e.g. the
    Chrome that ``@playwright/mcp`` launches) that would otherwise orphan to init
    and keep the browser profile's SingletonLock, breaking the next launch.

    Falls back to single-process signals when the process is not a group leader
    (e.g. an adopted install-time process) or the group is already gone, so this
    never accidentally signals an unrelated group such as the gateway's own.
    """
    if process is None or process.returncode is not None:
        # Already reaped: a grandchild may still hold the group (codex round
        # 5). Kill it through the id retained at spawn, never the pid.
        _kill_retained_group(group_pgid)
        return
    retained_pgid = group_pgid if _process_groups_supported() else None

    def _signal(kill: bool) -> None:
        # Process-group signalling is POSIX-only. On Windows os.getpgid/os.killpg
        # (and signal.SIGKILL) don't exist, so fall back to the cross-platform
        # process.terminate()/kill() — preserving the pre-process-group behavior.
        pid = process.pid
        pgid: int | None = None
        if isinstance(pid, int) and hasattr(os, "getpgid"):
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError, OSError):
                pgid = None
        # Only signal the group if this process leads it; otherwise signalling
        # its group could hit unrelated processes (including the gateway, for an
        # adopted process that was not given its own session).
        if pgid is not None and pgid == pid and hasattr(os, "killpg"):
            try:
                os.killpg(pgid, signal.SIGKILL if kill else signal.SIGTERM)
                return
            except (ProcessLookupError, PermissionError):
                pass
        try:
            if kill:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    # Cache the process group up front: once the leader exits, os.getpgid(pid)
    # fails, so we could no longer find the group to escalate against. Only set
    # when this process leads its own group (POSIX, start_new_session=True).
    group_pgid = retained_pgid
    pid = process.pid
    if (
        group_pgid is None
        and isinstance(pid, int)
        and hasattr(os, "getpgid")
        and hasattr(os, "killpg")
    ):
        try:
            if os.getpgid(pid) == pid:
                group_pgid = pid
        except (ProcessLookupError, PermissionError, OSError):
            group_pgid = None

    def _group_alive() -> bool:
        if group_pgid is None:
            return False
        try:
            os.killpg(group_pgid, 0)  # signal 0 == liveness probe
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    _signal(kill=False)
    try:
        try:
            await bounded_wait(process.wait(), timeout=5.0)
            leader_exited = True
        except asyncio.TimeoutError:
            leader_exited = False

        # If the leader is still alive, SIGKILL it (and, when it leads a group, the
        # whole group). The leader exiting is NOT sufficient: a grandchild (e.g. a
        # SIGTERM-ignoring browser) can outlive the leader inside the group and keep
        # the profile SingletonLock — so we still escalate to a group SIGKILL below.
        if not leader_exited:
            _signal(kill=True)
            try:
                await bounded_wait(process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{name}] Process PID={process.pid} did not exit after SIGKILL "
                    "(possible D-state / uninterruptible I/O wait)"
                )

        if _group_alive():
            try:
                os.killpg(group_pgid, signal.SIGKILL)  # type: ignore[arg-type]
            except (ProcessLookupError, PermissionError, OSError):
                pass
            for _ in range(30):  # up to ~3s for the OS to reap the group
                if not _group_alive():
                    break
                await asyncio.sleep(0.1)
            else:
                logger.warning(
                    f"[{name}] process group {group_pgid} survived SIGKILL "
                    "(possible orphaned grandchild / D-state)"
                )
    except asyncio.CancelledError:
        # Whoever cancelled this wait -- a caller, or loop shutdown cancelling
        # every task -- the tree is SIGKILLed synchronously before the
        # cancellation propagates (Consiliency/pmcp#324).
        _kill_process_tree_now(process, group_pgid=group_pgid)
        raise


# Heartbeat thresholds for health monitoring
HEARTBEAT_WARN_THRESHOLD = 60.0  # Warn if no activity for 60s
HEARTBEAT_STALL_THRESHOLD = 120.0  # Mark as stalled after 120s
HEALTH_CHECK_INTERVAL = 30.0  # Background health check every 30s
# Longest real wait between two idle/ceiling checks in _await_with_idle_timeout.
# The checks themselves read the request clock (ClientManager._clock); this only
# bounds how often they run, so tests can shrink it without changing an outcome.
IDLE_POLL_SLICE_S = 1.0

# Connection retry settings
MAX_CONNECTION_RETRIES = 3
RETRY_DELAYS = [1.0, 2.0, 4.0]  # Exponential backoff delays in seconds
# The auto-reconnect loop's delay before each attempt, in seconds.
RECONNECT_DELAYS = (5.0, 15.0, 30.0)
PREFERRED_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (
    PREFERRED_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Memory monitoring
MEMORY_LOG_INTERVAL = 60.0  # Log memory every 60s
MEMORY_WARN_THRESHOLD_MB = 1024  # Warn if process uses > 1GB

# Stdio read limit for downstream MCP server stdout (bytes per JSON-RPC line).
# asyncio's StreamReader default is 64 KiB, which truncates real-world tool
# responses (page scrapes, screenshots, large file reads) into an opaque
# "disconnected unexpectedly". 10 MiB covers realistic responses; override via
# PMCP_STDIO_READ_LIMIT for hosts that need larger or smaller caps.
DEFAULT_STDIO_READ_LIMIT = 10 * 1024 * 1024

# Chunk size for draining downstream stdout. We read in chunks and split on
# newlines ourselves (rather than StreamReader.readline) so a single oversized
# line can be dropped — failing only its request — instead of raising and tearing
# down the whole server connection (issue #79/1b).
_STDIO_CHUNK_SIZE = 64 * 1024

# Bound on the per-server outbound queue for fire-and-forget frames we originate
# (server->client request replies and notifications/cancelled). One writer task
# per ManagedClient drains it, so a downstream that stalls its own sink cannot
# make us allocate an unbounded number of writer tasks or buffer unbounded
# frames: at most this many queued plus one in-flight, then overflow is dropped.
_OUTBOUND_QUEUE_MAXSIZE = 256


def _stdio_read_limit() -> int:
    raw = os.environ.get("PMCP_STDIO_READ_LIMIT")
    if not raw:
        return DEFAULT_STDIO_READ_LIMIT
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "PMCP_STDIO_READ_LIMIT=%r is not an integer; using default %d",
            raw,
            DEFAULT_STDIO_READ_LIMIT,
        )
        return DEFAULT_STDIO_READ_LIMIT
    if value <= 0:
        logger.warning(
            "PMCP_STDIO_READ_LIMIT=%d must be positive; using default %d",
            value,
            DEFAULT_STDIO_READ_LIMIT,
        )
        return DEFAULT_STDIO_READ_LIMIT
    return value


# Absolute backstop for a single downstream request. ``timeout_ms`` is now an
# inactivity (idle) timeout: a call survives as long as the downstream keeps
# producing output. This ceiling caps the total wall-clock time so a chatty but
# never-completing call cannot hang forever. Override via PMCP_REQUEST_CEILING_MS.
DEFAULT_REQUEST_CEILING_MS = 600000  # 10 minutes


def _request_ceiling_ms() -> int:
    raw = os.environ.get("PMCP_REQUEST_CEILING_MS")
    if not raw:
        return DEFAULT_REQUEST_CEILING_MS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "PMCP_REQUEST_CEILING_MS=%r is not an integer; using default %d",
            raw,
            DEFAULT_REQUEST_CEILING_MS,
        )
        return DEFAULT_REQUEST_CEILING_MS
    if value <= 0:
        logger.warning(
            "PMCP_REQUEST_CEILING_MS=%d must be positive; using default %d",
            value,
            DEFAULT_REQUEST_CEILING_MS,
        )
        return DEFAULT_REQUEST_CEILING_MS
    return value


def _get_memory_usage_mb() -> float:
    """Get current process memory usage in MB."""
    try:
        if HAS_RESOURCE and resource_module is not None:
            # ru_maxrss is in KB on Linux, bytes on macOS
            usage = resource_module.getrusage(resource_module.RUSAGE_SELF)
            import sys

            if sys.platform == "darwin":
                return usage.ru_maxrss / 1024 / 1024
            return usage.ru_maxrss / 1024
        # Fallback: read from /proc
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception as e:
        logger.debug(f"memory usage parse error: {describe_exception(e)}")
    return 0.0


def _get_system_memory_pct() -> int:
    """Get system memory usage percentage."""
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            total = meminfo.get("MemTotal", 1)
            available = meminfo.get("MemAvailable", total)
            used_pct = int((total - available) * 100 / total)
            return used_pct
    except Exception as e:
        logger.debug(f"system memory check error: {describe_exception(e)}")
        return 0


def _generate_revision_id() -> str:
    """Generate a revision ID for cache invalidation."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"rev-{int(time.time() * 1000)}-{suffix}"


_LOW_RISK_WORDS = ("read", "get", "list", "search", "query", "fetch", "describe")
_HIGH_RISK_WORDS = (
    "delete",
    "remove",
    "drop",
    "execute",
    "run",
    "write",
    "create",
    "update",
    "modify",
    "send",
    "post",
    "put",
)

# Word-boundary, not substring. Plain `in` matching against a whole description
# is why `resolve-library-id` -- a read-only docs lookup -- was classified HIGH
# and told users it "may modify data": its description says "Source Reputation",
# and "reputation" contains "put". Long descriptions make that near-certain for
# almost any tool.
_LOW_RISK_RE = re.compile(rf"\b({'|'.join(_LOW_RISK_WORDS)})\b")
_HIGH_RISK_RE = re.compile(rf"\b({'|'.join(_HIGH_RISK_WORDS)})\b")


def _infer_risk_hint(
    tool_name: str,
    description: str,
    annotations: dict[str, Any] | None = None,
) -> RiskHint:
    """Infer risk level, preferring the server's own declaration.

    MCP defines `ToolAnnotations` (`readOnlyHint`, `destructiveHint`) precisely
    so a server can state this authoritatively. Those win over any guess we
    make from the tool's prose: the server knows what its tool does and we are
    pattern-matching English. The keyword heuristic below is only a fallback
    for servers that declare nothing.
    """
    if annotations:
        # destructive wins over read-only if a server sets both, since the
        # unsafe reading is the safe default.
        if annotations.get("destructiveHint") is True:
            return RiskHint.HIGH
        if annotations.get("readOnlyHint") is True:
            return RiskHint.LOW

    combined = f"{tool_name} {description}".lower()
    if _HIGH_RISK_RE.search(combined):
        return RiskHint.HIGH
    if _LOW_RISK_RE.search(combined):
        return RiskHint.LOW
    return RiskHint.MEDIUM


def _extract_tags(server_name: str, tool_name: str, description: str) -> list[str]:
    """Extract tags from tool name/description."""
    tags: set[str] = {server_name}

    categories: dict[str, list[str]] = {
        "database": ["db", "sql", "query", "table", "database"],
        "file": ["file", "directory", "folder", "path"],
        "git": ["git", "commit", "branch", "repository", "repo"],
        "http": ["http", "api", "request", "fetch", "url"],
        "search": ["search", "find", "grep", "filter"],
        "code": ["code", "function", "class", "symbol"],
    }

    combined = f"{tool_name} {description}".lower()

    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in combined:
                tags.add(category)
                break

    return list(tags)


def _truncate_description(description: str, max_length: int = 100) -> str:
    """Truncate description for catalog display."""
    if not description:
        return ""
    if len(description) <= max_length:
        return description
    return description[: max_length - 3] + "..."


def _raw_metadata(
    payload: dict[str, Any], known_fields: set[str]
) -> dict[str, Any] | None:
    metadata = {key: value for key, value in payload.items() if key not in known_fields}
    return metadata or None


def _entry_label(entry: Any, key: str = "name") -> str:
    """Best-effort identifier for a catalog entry we failed to parse.

    Deliberately total: it is only ever called from an `except` branch, so a
    label that itself raised would turn a skipped entry back into the lost
    catalog the guard exists to prevent. `entry` is typed `Any` because the
    whole point is that it did not have the shape we expected.
    """
    if isinstance(entry, dict):
        identifier = entry.get(key)
        if isinstance(identifier, str) and identifier:
            return repr(identifier)
    return repr(entry)[:120]


def _required_identity(entry: Any, key: str) -> str:
    """The identity a catalog entry cannot be indexed without.

    Raises rather than defaulting, and that is the whole point. `entry.get(key,
    "")` turns an entry with no identity into one whose identity is the empty
    string, so `{}` indexes as `srv::` -- a wholly synthetic entry that replaces
    the real one and gets published as a change. The MCP models reject those
    payloads outright; defaulting here invents an identity the protocol never
    offered.

    Every caller runs inside its parser's per-entry `try`, so raising routes the
    entry into the existing skip, and a listing in which *no* entry parses
    routes into `_reconcile_once`'s offered-but-none-parseable rule, which keeps
    the prior entries. Both are the behaviour we want for "this entry told us
    nothing": not "it is gone".

    `entry` is typed `Any` because a non-object entry (`["a string"]`) must land
    here too, rather than raising `AttributeError` somewhere less legible.
    """
    if not isinstance(entry, dict):
        raise TypeError(f"catalog entry is {type(entry).__name__}, not an object")
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"catalog entry has no usable `{key}`")
    return value


def _required_object(entry: Any, key: str) -> dict[str, Any]:
    """The object-valued field a catalog entry cannot be indexed without.

    `_required_identity`'s rule, for a field whose value is an object rather
    than a string. It exists as a sibling and not as a flag on that helper
    because that one requires `isinstance(value, str) and value`: called for
    `inputSchema` it would reject every valid tool in the catalog.

    `tool.get("inputSchema", {})` manufactured an accept-anything schema for a
    tool that declared none -- and `{}` is not "we do not know", it is "any
    arguments at all are valid", which is published to every caller and to
    every model reading the catalog. MCP requires `inputSchema` on a tool, so a
    tool without one is a tool we could not read, and #172 already settled what
    to do with those: skip it and say so, rather than invent the missing value.

    Raised inside the parser's per-entry `try`, so the entry lands in the
    existing skip-and-log path and a listing of nothing but such entries lands
    in `_reconcile_once`'s offered-but-none-parseable rule -- prior entries
    kept, nothing published.

    An explicitly empty `{}` is accepted: the server said "any arguments", and
    that is an answer. `null`, a string, a list and an absent field are not.
    """
    if not isinstance(entry, dict):
        raise TypeError(f"catalog entry is {type(entry).__name__}, not an object")
    value = entry.get(key)
    if not isinstance(value, dict):
        raise ValueError(
            f"catalog entry has no usable `{key}`: expected an object, got "
            f"{type(value).__name__}"
        )
    return value


def _listing_entries(name: str, kind: str, result: Any) -> list[dict[str, Any]] | None:
    """The entries a `*/list` reply offered, or `None` if it offered none readably.

    `result.get(kind, [])` conflates two different answers. A reply of
    `{"tools": []}` says the server has no tools; a reply of `result: {}` is
    missing a field the protocol requires, so it says nothing at all -- and the
    default turns the second into the first, clearing the catalog and publishing
    a removal built out of a malformed response. `list()` on a non-list widens
    the same hole: `{"tools": {}}` coerces to `[]` and reads as an explicit
    empty answer, while `{"tools": null}` raises `TypeError` out of
    `_fetch_server_listings` and costs the *other* two kinds their reconcile.

    So: absent field, non-object reply, or non-list value all return `None`,
    which callers already treat as a failed listing -- prior entries kept,
    nothing published. Only a genuine list is an answer, and an empty one still
    clears. "We could not read the answer" is not "they are gone", the same
    principle `_reconcile_once` applies to a request that failed and to a
    listing whose every entry was unparseable.
    """
    if not isinstance(result, dict):
        logger.warning(
            f"[{name}] {kind}/list answered with "
            f"{type(result).__name__}, not an object; treating as unreadable"
        )
        return None
    if kind not in result:
        logger.warning(
            f"[{name}] {kind}/list answered without its required `{kind}` field; "
            f"treating as unreadable rather than as an empty {kind} list"
        )
        return None
    offered = result[kind]
    if not isinstance(offered, list):
        logger.warning(
            f"[{name}] {kind}/list answered with `{kind}` as "
            f"{type(offered).__name__}, not a list; treating as unreadable"
        )
        return None
    return list(offered)


def _schema_dialect(*schemas: dict[str, Any] | None) -> str:
    for schema in schemas:
        if schema and isinstance(schema.get("$schema"), str):
            return schema["$schema"]
    return DEFAULT_SCHEMA_DIALECT


def _distinct_indexed(name: str, kind: str, entries: list[tuple[str, Any]]) -> int:
    """How many catalog keys `entries` actually occupies, logging collisions.

    `len(entries)` is the wrong number and `_index_resources` documents the
    right one: "the count returned is what was actually indexed, not what was
    offered, so a caller reporting it is not overstating the catalog." Two
    entries that share an identity are two list items but one dict key -- the
    second overwrites the first -- so the list length overstates the catalog by
    exactly the collisions, and a promise contradicted by the code is worse
    than no promise, because a reader trusts it. Counting distinct identities
    makes the sentence true. (#175 item 5.)

    The collision is logged rather than silently absorbed. Last-write-wins is
    the downstream's bug, not ours, and the honest count makes it *visible* --
    the log is what makes it *diagnosable*. DEBUG because a server can only
    reach here by offering a duplicate, and the operator who cares is already
    looking.

    This counts and logs; it deliberately does **not** write. An earlier draft
    took the catalog dict as a parameter and did both, which moved every write
    out of the three `_index_*` methods and straight past
    `tests/runtime/test_publisher_coverage.py` -- an AST guard that attributes
    each `self._tools`/`self._resources`/`self._prompts` write to its enclosing
    method and fails if one appears outside the five publishing mutators. A
    helper taking the dict as an argument is invisible to it, so the refactor
    would have silently disarmed the guard rather than tripped it. The write
    stays where the guard can see it.
    """
    seen: set[str] = set()
    for entry_id, _entry in entries:
        if entry_id in seen:
            logger.debug(
                f"[{name}] Duplicate {kind} id {entry_id!r} in one listing; "
                "the later entry wins and the count reflects one entry, not two"
            )
        seen.add(entry_id)
    return len(seen)


def _parse_tool_entries(
    name: str, tools: list[dict[str, Any]], limit: int
) -> list[tuple[str, ToolInfo]]:
    """Parse one server's `tools/list` payload into indexable entries.

    Pure: it reads the listing, logs the entries it had to skip, and returns
    what survived. It touches no catalog dict, which is what lets
    `_reconcile_once` run it *before* removing anything and decide from the
    result whether the listing is usable at all -- see its "offered but none
    parseable" rule. `_index_tools` does the writing.
    """
    entries: list[tuple[str, ToolInfo]] = []
    known_fields = {
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "icons",
        "annotations",
        "execution",
    }
    for tool in tools:
        if len(entries) >= limit:
            if limit < 1:
                # "has more than 0 tools, truncating" is true and useless: it
                # reads as a server that overran a bound, when what happened is
                # that the bound admits nothing. Name the limit instead, so the
                # reader can see the gateway made this decision. (#175 item 3.)
                #
                # Since #207 a policy file cannot produce this: `LimitsPolicy`
                # bounds the field at `ge=1`. The caller here is
                # `ClientManager`'s constructor parameter, which is deliberately
                # unbounded, so this stays reachable programmatically.
                logger.warning(
                    f"Server {name} offered {len(tools)} tools but "
                    f"max_tools_per_server is {limit}, so none were indexed"
                )
            else:
                logger.warning(f"Server {name} has more than {limit} tools, truncating")
            break

        try:
            tool_name = _required_identity(tool, "name")
            tool_id = make_tool_id(name, tool_name)
            description = tool.get("description", "")
            input_schema = _required_object(tool, "inputSchema")
            output_schema = tool.get("outputSchema")

            tool_info = ToolInfo(
                tool_id=tool_id,
                server_name=name,
                tool_name=tool_name,
                title=tool.get("title"),
                description=description,
                short_description=_truncate_description(description),
                input_schema=input_schema,
                icons=tool.get("icons"),
                output_schema=output_schema,
                annotations=tool.get("annotations"),
                execution=tool.get("execution"),
                schema_dialect=_schema_dialect(input_schema, output_schema),
                raw_metadata=_raw_metadata(tool, known_fields),
                tags=_extract_tags(name, tool_name, description),
                risk_hint=_infer_risk_hint(
                    tool_name, description, tool.get("annotations")
                ),
            )
        except Exception as e:
            logger.warning(
                f"[{name}] Skipping unparseable tool {_entry_label(tool)}: {describe_exception(e)}"
            )
            continue

        entries.append((tool_id, tool_info))
    return entries


def _parse_resource_entries(
    name: str, resources: list[dict[str, Any]]
) -> list[tuple[str, ResourceInfo]]:
    """Parse one server's `resources/list` payload. Pure, per `_parse_tool_entries`."""
    entries: list[tuple[str, ResourceInfo]] = []
    known_fields = {
        "uri",
        "name",
        "title",
        "description",
        "mimeType",
        "icons",
        "annotations",
    }
    for resource in resources:
        try:
            uri = _required_identity(resource, "uri")
            resource_id = f"{name}::{uri}"
            resource_info = ResourceInfo(
                resource_id=resource_id,
                server_name=name,
                uri=uri,
                name=resource.get("name"),
                title=resource.get("title"),
                description=resource.get("description"),
                mime_type=resource.get("mimeType"),
                icons=resource.get("icons"),
                annotations=resource.get("annotations"),
                raw_metadata=_raw_metadata(resource, known_fields),
            )
        except Exception as e:
            logger.warning(
                f"[{name}] Skipping unparseable resource "
                f"{_entry_label(resource, key='uri')}: {describe_exception(e)}"
            )
            continue

        entries.append((resource_id, resource_info))
    return entries


def _parse_prompt_entries(
    name: str, prompts: list[dict[str, Any]]
) -> list[tuple[str, PromptInfo]]:
    """Parse one server's `prompts/list` payload. Pure, per `_parse_tool_entries`."""
    entries: list[tuple[str, PromptInfo]] = []
    known_prompt_fields = {
        "name",
        "title",
        "description",
        "arguments",
        "icons",
        "annotations",
    }
    known_arg_fields = {"name", "title", "description", "required"}
    for prompt in prompts:
        try:
            prompt_name = _required_identity(prompt, "name")
            prompt_id = f"{name}::{prompt_name}"
            arguments = None
            if prompt.get("arguments"):
                arguments = [
                    PromptArgumentInfo(
                        name=arg.get("name", ""),
                        title=arg.get("title"),
                        description=arg.get("description"),
                        required=arg.get("required", False),
                        raw_metadata=_raw_metadata(arg, known_arg_fields),
                    )
                    for arg in prompt["arguments"]
                ]
            prompt_info = PromptInfo(
                prompt_id=prompt_id,
                server_name=name,
                name=prompt_name,
                title=prompt.get("title"),
                description=prompt.get("description"),
                arguments=arguments,
                icons=prompt.get("icons"),
                annotations=prompt.get("annotations"),
                raw_metadata=_raw_metadata(prompt, known_prompt_fields),
            )
        except Exception as e:
            logger.warning(
                f"[{name}] Skipping unparseable prompt {_entry_label(prompt)}: {describe_exception(e)}"
            )
            continue

        entries.append((prompt_id, prompt_info))
    return entries


def _is_protocol_version_initialize_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "protocol" in message
        and ("version" in message or PREFERRED_PROTOCOL_VERSION in message)
        and (
            "initialize" in message or "unsupported" in message or "invalid" in message
        )
    )


def _remote_headers(
    server_name: str,
    config: RemoteMcpServerConfig,
    *,
    tenant_id: str | None = None,
    project_root: Path | None = None,
) -> dict[str, str] | None:
    """Return remote transport headers with env-var placeholders expanded."""
    if not config.headers:
        return None
    resolution = resolve_remote_headers_for_tenant(
        config.headers,
        server_name=server_name,
        tenant_id=tenant_id,
        project_root=project_root,
    )
    if resolution.missing_env_vars:
        raise MissingRemoteHeaderAuthError(server_name, resolution.missing_env_vars)
    return resolution.resolved_headers


def _trace_context_payload(
    trace_context: TraceContextInfo | dict[str, Any] | None,
) -> dict[str, str]:
    if trace_context is None:
        return {}
    parsed = (
        trace_context
        if isinstance(trace_context, TraceContextInfo)
        else TraceContextInfo.model_validate(trace_context)
    )
    return parsed.model_dump(exclude_none=True)


@dataclass
class PendingRequest:
    """Metadata for tracking a pending tool invocation."""

    request_id: int
    server_name: str
    tool_id: str  # Empty for non-tool requests (initialize, tools/list)
    # Both stamps come from the owning ClientManager's request clock (`_clock`,
    # wall time by default); inside ClientManager they are compared only with
    # readings of that clock. Epoch seconds: handlers/cli render and subtract them.
    started_at: float  # request clock when request started
    last_heartbeat: float  # request clock of last activity
    timeout_ms: int  # Configured timeout
    future: asyncio.Future[Any]
    task_id: str | None = None
    task_status: str | None = None
    # The JSON-RPC method this request carried. Used to shape a downstream
    # notifications/cancelled (the spec forbids cancelling `initialize`), and
    # left "" for callers that don't set it.
    method: str = ""


@dataclass
class ManagedClient:
    """A managed connection to a downstream MCP server."""

    config: ResolvedServerConfig
    process: asyncio.subprocess.Process | None = None
    is_remote: bool = False
    write_stream: Any | None = None
    # The httpx2.AsyncClient pmcp owns for a streamable-HTTP downstream (mcp
    # 2.0.0's streamable_http_client() takes a caller-supplied client and does
    # not close it — see IF-0-P2-2). None for SSE and stdio downstreams, which
    # don't take one. Entered into the transport owner task's exit stack (see
    # `transport_owner_task`), so it closes with the transport; exposed here
    # so tests (and callers) can assert `.is_closed`.
    remote_http_client: httpx2.AsyncClient | None = None
    # The task that entered this remote client's transport exit stack and
    # will close it, on request via `transport_shutdown`. anyio cancel scopes
    # are bound to the task that created them, so the stack must be entered
    # and unwound in the same task — this task. None for stdio clients, which
    # have no exit stack.
    transport_owner_task: asyncio.Task[None] | None = None
    # Graceful-teardown signal the owner task parks on (`await shutdown.wait()`)
    # after publishing its streams. Set by `_close_remote_transport` to ask the
    # owner to unwind its own stack, in its own task.
    transport_shutdown: asyncio.Event | None = None
    status: ServerStatus = field(
        default_factory=lambda: ServerStatus(
            name="",
            status=ServerStatusEnum.OFFLINE,
            tool_count=0,
        )
    )
    pending_requests: dict[int, PendingRequest] = field(default_factory=dict)
    read_task: asyncio.Task[None] | None = None
    # stderr reader task for stdio servers; tracked so a failed/replaced connect
    # can cancel it directly instead of relying on server-name-scoped cancellation.
    stderr_task: asyncio.Task[None] | None = None
    # Remote auth headers as RESOLVED at connect time (placeholders expanded).
    # gateway.refresh compares these against freshly-resolved headers to detect
    # token rotation in the env store, which leaves the raw config unchanged.
    resolved_remote_headers: dict[str, str] | None = None
    # Health monitoring: rolling window of response times for avg calculation
    response_times: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    # Reconnect storm guard: True while a _reconnect_loop task is in flight
    reconnecting: bool = False
    # Bounded outbound path for fire-and-forget frames pmcp originates toward
    # this downstream (server->client request replies, notifications/cancelled).
    # Lazily created by `_enqueue_outbound`; a single `outbound_writer` task
    # drains the queue, recreated on demand when the previous one is done.
    outbound: asyncio.Queue[dict[str, Any]] | None = None
    outbound_writer: asyncio.Task[None] | None = None
    # The process group the downstream leads, so every kill path can reach a
    # grandchild after asyncio has reaped the leader (codex rounds 5 and 6,
    # Consiliency/pmcp#324). Set by the creator from the spawn contract
    # (`start_new_session=True` makes the pgid the pid), never looked up
    # afterwards: a leader that exits at once can be reaped before
    # `create_subprocess_exec` even returns.
    group_pgid: int | None = None


class ClientManager:
    """Manages connections to downstream MCP servers."""

    def __init__(
        self,
        max_tools_per_server: int = 100,
        max_concurrent_spawns: int = 8,
        project_root: Path | None = None,
        *,
        catalog_events: CatalogEventSink | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._catalog_events: CatalogEventSink = (
            catalog_events or _NullCatalogEventSink()
        )
        # The request clock: every PendingRequest stamp (started_at,
        # last_heartbeat, status.last_activity_at) and every comparison against
        # one (idle/ceiling timeout, health monitor, request state, cancel)
        # reads THIS, so a test can drive the idle timeout with a fake clock.
        # Wall time by default: handlers/cli render started_at as an epoch.
        self._clock: Callable[[], float] = clock if clock is not None else time.time
        self._clients: dict[str, ManagedClient] = {}
        self._tools: dict[str, ToolInfo] = {}
        self._resources: dict[str, ResourceInfo] = {}
        self._prompts: dict[str, PromptInfo] = {}
        self._servers: dict[str, ServerStatus] = {}
        self._lazy_configs: dict[str, ResolvedServerConfig] = {}  # On-demand configs
        self._revision_id: str = _generate_revision_id()
        self._last_refresh_ts: float = time.time()
        self._max_tools_per_server = max_tools_per_server
        self._project_root = project_root
        self._spawn_semaphore = asyncio.Semaphore(max_concurrent_spawns)
        self._lifecycle_lock = asyncio.Lock()
        # Connect generations (Consiliency/pmcp#324): every connect captures
        # `_lifecycle_seq` when it is requested (`_connect_request`), and
        # `_admit_connect` refuses it when it is older than the point at which
        # its name -- or every name -- was superseded. A cancelled
        # `disconnect_server(name)` supersedes `name`
        # (`_supersede_connects`); `abandon_all_now()` supersedes every name,
        # for every ticket, forever (`_ABANDONED_AT`).
        self._lifecycle_seq = 0
        self._superseded_at: dict[str, int] = {}
        self._all_superseded_at = 0
        self._connect_tasks: dict[str, asyncio.Task[None]] = {}
        # The ticket each per-name connect task was started with, so that a
        # request never joins a task that is staler than itself.
        self._connect_task_tickets: weakref.WeakKeyDictionary[
            asyncio.Task[None], int
        ] = weakref.WeakKeyDictionary()
        # The last per-name connect task started for each name (never cleared,
        # only replaced): whose request may write that server's state after
        # an await (`_settle_request`).
        self._latest_connect: dict[str, asyncio.Task[None]] = {}
        # Requests currently awaiting each per-name connect task: a request's
        # own cancel cancels the task only when it is the last of them.
        self._connect_waiters: dict[asyncio.Task[None], int] = {}
        # Per-name connect tasks that take the lifecycle lock themselves (lazy
        # start's): a request that holds the lock must not join one.
        self._lock_taking_tasks: weakref.WeakSet[asyncio.Task[None]] = weakref.WeakSet()
        # The client each per-name connect task registered (see
        # `_abandon_task_client`).
        self._connect_task_clients: weakref.WeakKeyDictionary[
            asyncio.Task[Any], ManagedClient
        ] = weakref.WeakKeyDictionary()
        # The config each scheduled reconnect task would connect with.
        self._reconnect_task_configs: weakref.WeakKeyDictionary[
            asyncio.Task[None], ResolvedServerConfig
        ] = weakref.WeakKeyDictionary()
        # `_connect_all_unlocked`'s per-config request tasks (see
        # `_cancel_background_tasks_now`).
        self._connect_request_tasks: weakref.WeakSet[asyncio.Task[Any]] = (
            weakref.WeakSet()
        )
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_task_servers: dict[asyncio.Task[Any], str | None] = {}
        self._reconnect_tasks: dict[str, asyncio.Task[None]] = {}
        # FANOUT: at most one in-flight catalog reconcile per server name, plus
        # the re-run flags a notification sets when it arrives while one is
        # already running. See `_handle_downstream_notification`.
        self._reconcile_tasks: dict[str, asyncio.Task[None]] = {}
        self._reconcile_reruns: set[str] = set()
        # Server names whose `note_*` publishes are currently suppressed, with a
        # depth so overlapping reconciles of the same name nest correctly. Keyed
        # by server so a reconcile of A never swallows a genuine publish for B.
        self._catalog_suppressed: dict[str, int] = {}
        self._request_counters: dict[str, int] = {}
        self._tasks: dict[tuple[str, str], McpTaskRecord] = {}
        # Cap on retained terminal (completed/failed/cancelled) task records.
        # Active records are never evicted; only terminal ones are pruned oldest
        # first once this many accumulate, so the registry can't grow unbounded
        # between full teardowns.
        self._max_terminal_tasks = 100
        # Increments on every `_record_task`: the terminal-eviction order.
        self._record_order = 0

    @contextmanager
    def _connect_request(self) -> Iterator[None]:
        """Capture the connect generation NOW -- when the connect is
        requested, before it waits for the lifecycle lock -- for every
        `_admit_connect` reached inside this block, including from the tasks
        it starts (`create_task` copies the context). Every connect entry
        point opens one: `connect_all` (startup and the CLI), `connect_server`
        (and `restart_server`), `refresh` and `ensure_connected` (lazy start).
        The reconnect loop sets the same ticket from the generation
        `_schedule_reconnect` captured when it scheduled it."""
        token = _CONNECT_TICKET.set(_ConnectTicket(self, self._lifecycle_seq))
        try:
            yield
        finally:
            _CONNECT_TICKET.reset(token)

    def _current_ticket(self) -> int:
        """The generation the running connect request captured. Outside any
        request (a direct call of a private connect method), now."""
        ticket = _CONNECT_TICKET.get()
        if ticket is not None and ticket.owner is self:
            return ticket.seq
        return self._lifecycle_seq

    def _ticket_is_current(self, name: str, ticket: int) -> bool:
        return ticket >= max(self._all_superseded_at, self._superseded_at.get(name, 0))

    def _supersede_connects(self, name: str) -> None:
        """Refuse every connect of `name` requested before now: synchronous,
        for `disconnect_server`'s cancelled pre-lock path."""
        self._lifecycle_seq += 1
        self._superseded_at[name] = self._lifecycle_seq

    def _admit_connect(self, name: str) -> None:
        """THE check (Consiliency/pmcp#324). Raise `_ManagerAbandoned` once
        `abandon_all_now()` ran, or `_ConnectSuperseded` when the running
        request is older than a cancelled `disconnect_server(name)`.
        Synchronous and write-free; every caller registers, spawns or joins
        nothing before it. What a refused request leaves behind is
        `_settle_request`'s, under the lifecycle lock."""
        ticket = self._current_ticket()
        if ticket < self._all_superseded_at:
            raise _ManagerAbandoned(
                f"Not connecting {name}: the client manager was abandoned"
            )
        if ticket < self._superseded_at.get(name, 0):
            raise _ConnectSuperseded(
                f"Not connecting {name}: superseded by a disconnect_server"
                f"({name}) requested after this connect and cancelled before"
                " it ran"
            )

    def _joinable_connect_task(
        self, name: str, *, holding_lock: bool
    ) -> asyncio.Task[None] | None:
        """The per-name connect task in flight, unless it was started by a
        request that is staler than the admitted caller (that task will be
        refused, and joining it would refuse a request that is current), or
        the caller holds the lifecycle lock and the task needs it (a lazy
        start's: the caller would wait on it forever)."""
        task = self._connect_tasks.get(name)
        if task is None or task.done():
            return None
        if holding_lock and task in self._lock_taking_tasks:
            return None
        started = self._connect_task_tickets.get(task, self._lifecycle_seq)
        return task if self._ticket_is_current(name, started) else None

    def _start_connect_task(
        self, name: str, coro: Any, *, takes_lock: bool = False
    ) -> asyncio.Task[None]:
        """Start, track and register the per-name connect task, recording the
        ticket it carries and that it is now the latest for `name`."""
        task: asyncio.Task[None] = self._track_background_task(
            asyncio.create_task(coro), name
        )
        self._connect_task_tickets[task] = self._current_ticket()
        if takes_lock:
            self._lock_taking_tasks.add(task)
        self._connect_tasks[name] = task
        self._latest_connect[name] = task

        def unregister(done: asyncio.Task[None]) -> None:
            # A finished task is never joined: its entry goes when it ends,
            # whichever of its requests is still around (none need be).
            if self._connect_tasks.get(name) is done:
                self._connect_tasks.pop(name, None)

        task.add_done_callback(unregister)
        return task

    def _abandon_task_client(self, task: asyncio.Task[None], name: str) -> None:
        """Synchronously tear down the client `task` registered for `name`
        and has not finished connecting: kill it, cancel its I/O, drop it."""
        managed = self._connect_task_clients.get(task)
        if managed is not None and self._clients.get(name) is managed:
            self._abandon_client_io(name, managed)
            self._drop_client(name, managed)

    def _note_connect_client(self, managed: ManagedClient) -> None:
        """Record the client the running per-name connect task registered."""
        current = asyncio.current_task()
        if current is not None:
            self._connect_task_clients[current] = managed

    def _settle_request(
        self,
        name: str,
        *,
        task: asyncio.Task[None] | None = None,
        config: ResolvedServerConfig | None = None,
        outcome: BaseException | bool,
    ) -> BaseException | bool:
        """THE place a connect request writes a server's state once it has
        waited (Consiliency/pmcp#324, round 3), for every way the request
        ends -- connected, refused, failed, or cancelled -- and returns how it
        ended after admission. (A per-name task's `_connect_tasks` entry goes
        when the task ends: `_start_connect_task`.) Synchronous; called with
        the lifecycle lock held, except from a cancelled request's handler,
        which cannot wait for it (pinned by
        `tests/test_cancel_supersedes_connect.py`). Each write is made only
        by the request that still owns the server's state; `task` is the
        per-name task the request awaited, if any.

        * True (connected) or the request's own `CancelledError`: admission
          first. A request superseded meanwhile -- its connect finished just
          before a cancelled disconnect took the server down -- becomes the
          refusal (and a cancel stays a cancel), and settles as one.
        * A `_ConnectSuperseded`: with no client registered and no current
          connect of `name` started since, leave what the superseding
          disconnect would have left had it run after this connect
          (`_settle_disconnected`); otherwise a newer request owns the state.
        * True, still current: pop the lazy config of a server that is
          ONLINE, if this request's task is the latest for `name` (or it held
          the lock throughout: `task` None).
        * Another exception (failed): stamp ERROR, on the same ownership.
        * `_ManagerAbandoned`, or a cancel that was not superseded: nothing.
        """
        final = outcome
        if outcome is True or isinstance(outcome, asyncio.CancelledError):
            try:
                self._admit_connect(name)
            except _ConnectRefused as e:
                if outcome is True:
                    final = e
                outcome = e
        latest = self._latest_connect.get(name)
        if isinstance(outcome, _ConnectSuperseded):
            newer = latest is not None and self._ticket_is_current(
                name, self._connect_task_tickets.get(latest, -1)
            )
            if config is not None and name not in self._clients and not newer:
                self._settle_disconnected(name, config)
            return final
        if isinstance(outcome, (_ManagerAbandoned, asyncio.CancelledError)):
            return final
        owner = self._ticket_is_current(name, self._current_ticket()) and (
            task is None or latest is task
        )
        if not owner:
            return final
        if outcome is True:
            if self.is_server_online(name):
                self._lazy_configs.pop(name, None)
        elif isinstance(outcome, BaseException) and name in self._servers:
            self._servers[name].status = ServerStatusEnum.ERROR
            self._servers[name].last_error = describe_exception(outcome)
        return final

    async def connect_all(
        self, configs: list[ResolvedServerConfig], retry: bool = True
    ) -> list[str]:
        """Connect to all configured servers in parallel.

        Args:
            configs: List of server configurations
            retry: Whether to retry failed connections with exponential backoff

        Returns:
            List of error messages for failed connections
        """
        if not configs:
            return []

        with self._connect_request():
            async with self._lifecycle_lock:
                return await self._connect_all_unlocked(configs, retry=retry)

    async def _connect_all_unlocked(
        self, configs: list[ResolvedServerConfig], retry: bool = True
    ) -> list[str]:
        """Connect to all configured servers while caller owns lifecycle lock.

        A refused connect (`_ConnectRefused`) is one error string per server,
        as any other failure, logged at INFO: it is not a fault."""
        if not configs:
            return []

        # Connect to all servers concurrently, sharing work for duplicate names.
        tasks_by_name: dict[str, asyncio.Task[None]] = {}
        tasks: list[asyncio.Task[None]] = []
        for config in configs:
            task = tasks_by_name.get(config.name)
            if task is None:
                task = self._track_background_task(
                    asyncio.create_task(self._connect_singleflight(config, retry)),
                    config.name,
                )
                self._connect_request_tasks.add(task)
                tasks_by_name[config.name] = task
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect errors from failed connections. A connect that finished
        # just before a cancelled disconnect superseded it is refused here
        # (`_settle_request`); a refusal is settled, not stamped ERROR.
        errors: list[str] = []
        for config, gathered in zip(configs, results):
            result: BaseException | bool | None = gathered
            if gathered is None:
                result = self._settle_request(config.name, config=config, outcome=True)
            elif isinstance(gathered, _ConnectRefused):
                self._settle_request(config.name, config=config, outcome=gathered)
            if isinstance(result, Exception):
                error_msg = f"Failed to connect to {config.name}: {result}"
                if isinstance(result, _ConnectRefused):
                    logger.info(error_msg)
                else:
                    logger.error(error_msg)
                errors.append(error_msg)

        self._revision_id = _generate_revision_id()
        self._last_refresh_ts = time.time()

        return errors

    async def _connect_singleflight(
        self, config: ResolvedServerConfig, retry: bool = True
    ) -> None:
        """Share concurrent connection attempts for the same server name.

        A superseded request is refused first -- before it can report an
        ONLINE server as its own success or join a connect in flight -- and
        a current one never joins a task a staler request started
        (Consiliency/pmcp#324)."""
        name = config.name
        self._admit_connect(name)
        status = self._servers.get(name)
        if status is not None and status.status == ServerStatusEnum.ONLINE:
            return

        task = self._joinable_connect_task(name, holding_lock=True)
        if task is None:
            task = self._start_connect_task(
                name,
                self._connect_with_retry(config)
                if retry
                else self._connect_server(config),
            )

        await self._await_connect_task(task, name, config)

    async def _await_connect_task(
        self, task: asyncio.Task[None], name: str, config: ResolvedServerConfig
    ) -> None:
        """Await a per-name connect task on behalf of one request of it.

        Through `asyncio.wait`, which never raises the task's outcome, so a
        `CancelledError` out of the wait is this request's own. It cancels
        the task only if no other request still awaits it (a shared connect
        is not the cancelled caller's alone), then propagates. A task that
        ended cancelled while this request was not was stopped from outside:
        by a cancelled `disconnect_server` superseding it or by abandonment,
        which `_admit_connect` reports as the refusal, or by a sweep, whose
        cancellation is re-raised as before (Consiliency/pmcp#324). On its
        own cancel it settles here; otherwise its caller settles
        (`_settle_request`)."""
        self._connect_waiters[task] = self._connect_waiters.get(task, 0) + 1
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError as e:
            # This request's own cancel. When no other request awaits the
            # connect, it is stopped, and what it registered is torn down
            # synchronously HERE, before the cancel propagates -- not on the
            # task's next step (codex round 3 F001). A connect that already
            # finished survives a late cancel, as before. Then the request
            # settles (grok round 3 F001: superseded, it leaves the server as
            # the disconnect would have) and stays cancelled.
            if self._connect_waiters.get(task, 0) <= 1 and not task.done():
                task.cancel()
                self._abandon_task_client(task, name)
            self._settle_request(name, task=task, config=config, outcome=e)
            raise
        finally:
            left = self._connect_waiters.get(task, 0) - 1
            if left > 0:
                self._connect_waiters[task] = left
            else:
                self._connect_waiters.pop(task, None)
        if task.cancelled():
            self._admit_connect(name)
        task.result()

    def _track_background_task(
        self, task: _TaskT, server_name: str | None = None
    ) -> _TaskT:
        self._background_tasks.add(task)
        self._background_task_servers[task] = server_name
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(lambda t: self._background_task_servers.pop(t, None))
        return task

    async def _cancel_background_tasks(
        self,
        *,
        server_name: str | None = None,
        exclude: set[asyncio.Task[Any]] | None = None,
    ) -> None:
        # Copy so we never mutate a caller's set, and always exclude the running
        # task: a connect/reconnect task scoped to this server name must never
        # cancel a gather() containing itself (that self-cancel recurses until
        # RecursionError and leaves the server stuck in ERROR).
        tasks = self._background_tasks_for(server_name=server_name, exclude=exclude)
        for task in tasks:
            task.cancel()
        if tasks:
            # `asyncio.wait`, not `gather`: a cancelled `gather` still waits for
            # every child to finish, so a cancelled caller would wait out a
            # child that ignores cancellation (Consiliency/pmcp#324).
            await asyncio.wait(tasks)
            for task in tasks:
                _retrieve_outcome(task)
        self._background_tasks.difference_update(task for task in tasks if task.done())
        for task in tasks:
            if task.done():
                self._background_task_servers.pop(task, None)

    def _cancel_background_tasks_now(
        self, *, server_name: str | None = None, spare_requests: bool = False
    ) -> None:
        """`_cancel_background_tasks` without the wait: for a cancelled
        teardown, which must not await (Consiliency/pmcp#324).

        With `spare_requests` (connects are being superseded), the request
        tasks of a `connect_all`/`refresh` are spared: each is
        awaiting a per-name connect task this cancels, or has not started and
        will be refused by `_admit_connect`, so it reports a refusal rather
        than ending cancelled and missing from the result."""
        for task in self._background_tasks_for(server_name=server_name):
            if spare_requests and task in self._connect_request_tasks:
                continue
            _cancel_without_waiting(task)

    def _background_tasks_for(
        self,
        *,
        server_name: str | None = None,
        exclude: set[asyncio.Task[Any]] | None = None,
    ) -> list[asyncio.Task[Any]]:
        exclude = set(exclude) if exclude else set()
        current = asyncio.current_task()
        if current is not None:
            exclude.add(current)
        return [
            task
            for task in self._background_tasks
            if task not in exclude
            and not task.done()
            and (
                server_name is None
                or self._background_task_servers.get(task) == server_name
                or task is self._reconnect_tasks.get(server_name)
                or task is self._connect_tasks.get(server_name)
            )
        ]

    def _next_request_id(self, server_name: str) -> int:
        request_id = self._request_counters.get(server_name, 0) + 1
        self._request_counters[server_name] = request_id
        return request_id

    def register_lazy_configs(self, configs: list[ResolvedServerConfig]) -> None:
        """Register configs for lazy (on-demand) server connections.

        These servers won't connect until first use via ensure_connected().

        Args:
            configs: List of server configurations to register for lazy start
        """
        for config in configs:
            name = config.name
            if name in self._clients:
                logger.debug(
                    f"Server {name} already connected, skipping lazy registration"
                )
                continue

            self._lazy_configs[name] = config
            # Create LAZY status entry so server appears in status listings
            self._servers[name] = ServerStatus(
                name=name,
                status=ServerStatusEnum.LAZY,
                tool_count=0,
            )
            logger.info(f"Registered lazy server: {name}")

    def prune_lazy_configs(self, keep_names: set[str]) -> None:
        """Drop on-demand (lazy) configs whose name is not in ``keep_names``.

        Used by ``gateway.refresh`` to reconcile the lazy set to the freshly
        resolved keep-set so a server that is now removed / policy-denied /
        missing-auth can no longer be lazily started via ``ensure_connected()``.
        Also clears its lingering ``LAZY`` status entry. Connected servers are
        untouched (they are reconciled by the refresh diff, not here).
        """
        for name in list(self._lazy_configs):
            if name in keep_names:
                continue
            self._lazy_configs.pop(name, None)
            status = self._servers.get(name)
            if status is not None and status.status == ServerStatusEnum.LAZY:
                self._servers.pop(name, None)

    async def ensure_connected(self, server_name: str) -> bool:
        """Ensure a server is connected, triggering lazy-start if needed.

        Args:
            server_name: Name of the server to ensure is connected

        Returns:
            True if server is online, False if connection failed

        Raises:
            ValueError: If server is not registered (neither connected nor lazy)
        """
        with self._connect_request():
            return await self._ensure_connected_requested(server_name)

    async def _ensure_connected_requested(self, server_name: str) -> bool:
        """`ensure_connected` inside its connect request.

        Its connect task carries the request's ticket and is admitted (or
        refused) when it takes the lock (`_connect_with_lifecycle_lock`) and
        in `_connect_server`. A superseded lazy start needs no check
        of its own here: the lock is first-come, first-served, so it reaches
        this block before any connect requested after the supersession, and
        so finds neither a server that request brought ONLINE nor its task
        to join. The task it starts here is refused in the funnel, and a
        current request that finds it registered starts its own instead
        (`_joinable_connect_task`) (Consiliency/pmcp#324)."""
        async with self._lifecycle_lock:
            if self.is_server_online(server_name):
                return True

            if server_name not in self._lazy_configs:
                if server_name not in self._servers:
                    raise ValueError(f"Unknown server: {server_name}")
                return False

            config = self._lazy_configs[server_name]
            logger.info(f"Lazy-starting server: {server_name}")
            task = self._joinable_connect_task(server_name, holding_lock=False)
            if task is None:
                task = self._start_connect_task(
                    server_name,
                    self._connect_with_lifecycle_lock(config),
                    takes_lock=True,
                )

        # This request waits OUTSIDE the lock, so by the time it resumes a
        # newer request may own the server: every write it makes goes through
        # `_settle_request`, under the lock. A request cancelled here writes
        # nothing and does not wait for the lock (Consiliency/pmcp#324).
        outcome: BaseException | bool
        try:
            await self._await_connect_task(task, server_name, config)
            outcome = True
        except _ConnectRefused as e:
            # Not a failure of the server: no ERROR status.
            outcome = e
            logger.info(f"Not lazy-starting {server_name}: {describe_exception(e)}")
        except Exception as e:
            outcome = e
            logger.error(f"Failed to lazy-start {server_name}: {describe_exception(e)}")
        async with self._lifecycle_lock:
            outcome = self._settle_request(
                server_name, task=task, config=config, outcome=outcome
            )
        return outcome is True

    async def _connect_with_lifecycle_lock(self, config: ResolvedServerConfig) -> None:
        async with self._lifecycle_lock:
            # A request that held the lock may have connected it meanwhile
            # (it does not join this task: `_joinable_connect_task`). A
            # superseded lazy start is refused at its settle either way.
            if self.is_server_online(config.name):
                return
            await self._connect_with_retry(config)

    async def connect_server(
        self, config: ResolvedServerConfig, retry: bool = True
    ) -> list[str]:
        """Connect one server through same-server single-flight startup."""
        with self._connect_request():
            async with self._lifecycle_lock:
                return await self._connect_server_locked(config, retry)

    async def _connect_server_locked(
        self, config: ResolvedServerConfig, retry: bool
    ) -> list[str]:
        """`connect_server` with the lifecycle lock held."""
        try:
            await self._connect_singleflight(config, retry=retry)
            final = self._settle_request(config.name, config=config, outcome=True)
            if isinstance(final, BaseException):
                return [
                    f"Failed to connect to {config.name}: {describe_exception(final)}"
                ]
            self._revision_id = _generate_revision_id()
            self._last_refresh_ts = time.time()
            return []
        except Exception as e:
            # A refusal is not a failure of the server: no ERROR status
            # (`_settle_request`).
            self._settle_request(config.name, config=config, outcome=e)
            return [f"Failed to connect to {config.name}: {describe_exception(e)}"]

    def cancel_pending_requests(self, server: str) -> int:
        """Cancel pending requests for one server and return newly cancelled count."""
        managed = self._clients.get(server)
        if not managed:
            return 0

        cancelled = 0
        for request_id, pending in list(managed.pending_requests.items()):
            if not pending.future.done():
                pending.future.cancel()
                cancelled += 1
            managed.pending_requests.pop(request_id, None)
        managed.status.pending_request_count = len(managed.pending_requests)
        if cancelled:
            logger.warning(
                f"Force-cancelled {cancelled} pending requests for server {server}"
            )
        return cancelled

    async def disconnect_server(
        self, name: str, force: bool = False
    ) -> tuple[bool, int, str | None]:
        """Disconnect one server, refusing active requests unless forced."""
        disconnected, cancelled, error, _errors = await self._disconnect_server(
            name, force
        )
        return (disconnected, cancelled, error)

    async def _disconnect_server(
        self,
        name: str,
        force: bool,
        reconnect: ResolvedServerConfig | None = None,
    ) -> tuple[bool, int, str | None, list[str]]:
        """`disconnect_server`, and with `reconnect` `restart_server`: the
        connect runs in the same lifecycle-lock hold as the disconnect, so a
        restart is one operation in the lock's first-come, first-served
        order (Consiliency/pmcp#324, codex round 3 F002). The connect's
        ticket is the one `restart_server` captured when it was requested."""
        pending_requests = self.get_pending_requests(name)
        active_tasks = self.get_active_tasks(name)
        if (pending_requests or active_tasks) and not force:
            return (
                False,
                0,
                "Disconnect refused because this server has pending requests or active MCP tasks. "
                "Use gateway.list_pending to inspect them or retry with force=true.",
                [],
            )

        cancelled = self.cancel_pending_requests(name) if pending_requests else 0
        # Every await before the lifecycle lock is held is part of the
        # teardown too (Consiliency/pmcp#324, implementation addition): a
        # forced disconnect sends `tasks/cancel` for each active MCP task and
        # waits for the reply, and then waits for the lock. A caller cancelled
        # at either await still gets the synchronous teardown, then the cancel.
        try:
            if active_tasks:
                for task in active_tasks:
                    ok, _task, message = await self.cancel_task(
                        name, task.task_id, force=True
                    )
                    if not ok:
                        return (False, cancelled, message, [])
            await self._lifecycle_lock.acquire()
        except asyncio.CancelledError:
            # Cancelled before it took the lock, this disconnect never runs
            # after the connects of `name` requested before it, as it would
            # have uncancelled (the lock is FIFO). Refuse them instead: one
            # still queued on the lock, one held up behind a lock holder that
            # has not started its per-name task yet (a `refresh` in its
            # disconnect phase), a lazy start, a reconnect -- each fails
            # `_admit_connect` when it gets there. Not on the uncancelled
            # path: there the disconnect does run after them, and refusing
            # them would change what those callers are told.
            self._supersede_connects(name)
            before_lock = self._clients.get(name)
            if before_lock is not None:
                before_lock.status.status = ServerStatusEnum.OFFLINE
                self._abandon_client_io(name, before_lock)
                self._forget_disconnected(name, before_lock.config)
            # A reconnect it stops has no request awaiting it to settle for
            # it: leave what this disconnect would have left after it. Here,
            # at the supersession, no current request of `name` exists yet.
            pending_reconnect = self._reconnect_tasks.get(name)
            if pending_reconnect is not None and not pending_reconnect.done():
                reconnect_config = self._reconnect_task_configs.get(pending_reconnect)
                if reconnect_config is not None and name not in self._clients:
                    self._settle_disconnected(name, reconnect_config)
            # Outside the `if`: a connect for this server may be in flight
            # with no client registered yet (e.g. in its retry backoff,
            # holding the lock). Cancel it either way, or its next attempt
            # spawns after this disconnect, and drop its `_connect_tasks`
            # entry now, so nothing can join it. (`_await_connect_task`
            # reports the refusal to the request awaiting it.)
            self._cancel_background_tasks_now(server_name=name, spare_requests=True)
            self._connect_tasks.pop(name, None)
            raise
        try:
            disconnected, cancelled, error = await self._disconnect_server_locked(
                name, cancelled
            )
            errors: list[str] = []
            if disconnected and reconnect is not None:
                errors = await self._connect_server_locked(reconnect, True)
            return (disconnected, cancelled, error, errors)
        finally:
            self._lifecycle_lock.release()

    async def _disconnect_server_locked(
        self, name: str, cancelled: int
    ) -> tuple[bool, int, str | None]:
        """`disconnect_server`'s teardown, with the lifecycle lock held."""
        managed = self._clients.get(name)
        # Re-cancel inside the lock: requests may have been queued in the
        # window between the pre-lock inspection above and acquiring the
        # lock, which would otherwise leave orphaned pending futures.
        if managed is not None and managed.pending_requests:
            cancelled += self.cancel_pending_requests(name)
        if not managed:
            status = self._servers.get(name)
            if status is not None:
                status.status = (
                    ServerStatusEnum.LAZY
                    if name in self._lazy_configs
                    else ServerStatusEnum.OFFLINE
                )
                status.tool_count = 0
                status.resource_count = 0
                status.prompt_count = 0
                status.pending_request_count = 0
            return (True, cancelled, None)

        config = managed.config
        managed.status.status = ServerStatusEnum.OFFLINE
        managed.status.pending_request_count = 0

        try:
            await _reap_child(managed.read_task, timeout=1.0)
            # Cancel the outbound writer explicitly (in addition to the
            # server-name sweep below), so teardown of this path does not
            # depend on that sweep also matching it -- and reset
            # `outbound`/`outbound_writer`, the same postcondition
            # `_cleanup_client` holds (Consiliency/pmcp#287).
            await self._teardown_outbound(managed, timeout=1.0)
            closed = await self._close_or_terminate(name, managed)
            if closed is not None:
                return (False, cancelled, closed)
            await self._cancel_background_tasks(server_name=name)
        except asyncio.CancelledError:
            # The caller was cancelled mid-disconnect: finish it
            # synchronously, then re-raise (Consiliency/pmcp#324).
            # `disconnect_server`'s `finally` releases the lifecycle lock
            # without awaiting.
            self._abandon_client_io(name, managed)
            self._cancel_background_tasks_now(server_name=name)
            self._forget_disconnected(name, config)
            raise
        self._forget_disconnected(name, config)
        return (True, cancelled, None)

    async def _close_or_terminate(
        self, name: str, managed: ManagedClient
    ) -> str | None:
        """`disconnect_server`'s close step: None on success, else the
        described failure. A cancellation propagates."""
        try:
            if managed.is_remote:
                # _close_remote_transport never swallows a genuine
                # transport-exit failure -- that's deliberate, so it can
                # still reach the `except Exception` below and return
                # (False, cancelled, str(e)) rather than reporting a
                # broken teardown as a successful disconnect. A timeout
                # that escalates to cancel is logged there and returns
                # normally: the transport is closed either way, and
                # `False` here would make a dead-peer disconnect look
                # like a refusal to the caller.
                await self._close_remote_transport(name, managed)
            else:
                await _terminate_process_tree(
                    managed.process, name, group_pgid=managed.group_pgid
                )
        except Exception as e:
            described = describe_exception(e)
            logger.warning(f"Error disconnecting from {name}: {described}")
            return described
        return None

    def _forget_disconnected(self, name: str, config: Any) -> None:
        """`disconnect_server`'s registry bookkeeping. Synchronous, so the
        cancelled path runs it too (Consiliency/pmcp#324)."""
        self._connect_tasks.pop(name, None)
        self._reconnect_tasks.pop(name, None)
        # A reconcile cancelled before it ever started never runs its own
        # `finally`, so clear its bookkeeping here too. The catalog removal
        # below supersedes whatever it would have published.
        self._reconcile_tasks.pop(name, None)
        self._reconcile_reruns.discard(name)
        self._catalog_suppressed.pop(name, None)
        self._clients.pop(name, None)
        self._remove_server_indexes(name)
        self._settle_disconnected(name, config)

    def _settle_disconnected(self, name: str, config: Any) -> None:
        """The registry state a completed `disconnect_server(name)` leaves:
        lazy re-registration, LAZY/OFFLINE status, a new revision. Also what
        `_admit_connect` leaves for a superseded connect with no client."""
        if config is not None and config.source in {"project", "user", "custom"}:
            self._lazy_configs[name] = config
        self._servers[name] = ServerStatus(
            name=name,
            status=ServerStatusEnum.LAZY
            if name in self._lazy_configs
            else ServerStatusEnum.OFFLINE,
            tool_count=0,
        )
        self._revision_id = _generate_revision_id()
        self._last_refresh_ts = time.time()
        # No flush() here, deliberately -- see _index_capabilities.

    async def restart_server(
        self, config: ResolvedServerConfig, force: bool = False
    ) -> tuple[bool, int, list[str]]:
        """Restart one server by disconnecting then connecting the same config,
        in one lifecycle-lock hold, as one connect request whose ticket is
        captured now (Consiliency/pmcp#324)."""
        with self._connect_request():
            disconnected, cancelled, error, errors = await self._disconnect_server(
                config.name, force, reconnect=config
            )
        if not disconnected:
            return (False, cancelled, [error or "Restart refused."])
        return (len(errors) == 0, cancelled, errors)

    def is_lazy_server(self, name: str) -> bool:
        """Check if server is registered for lazy start but not yet connected."""
        return name in self._lazy_configs

    def get_lazy_server_names(self) -> list[str]:
        """Get list of servers registered for lazy start."""
        return list(self._lazy_configs.keys())

    async def _connect_with_retry(self, config: ResolvedServerConfig) -> None:
        """Connect to a server with exponential backoff retry."""
        last_error: Exception | None = None

        for attempt in range(MAX_CONNECTION_RETRIES):
            try:
                await self._connect_server(config)
                return  # Success
            except _ConnectRefused:
                raise  # Not a failure to retry: no later attempt is admitted.
            except Exception as e:
                last_error = e
                if attempt < MAX_CONNECTION_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        f"Connection to {config.name} failed (attempt {attempt + 1}/"
                        f"{MAX_CONNECTION_RETRIES}), retrying in {delay}s: "
                        f"{describe_exception(e)}"
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        if last_error:
            raise last_error

    async def _connect_server(self, config: ResolvedServerConfig) -> None:
        """Connect to a single MCP server.

        Every connect path -- startup, `connect_server`, `refresh`, lazy
        start, reconnect -- ends here, and every attempt of a retried connect
        passes here again: `_admit_connect` refuses one requested before
        `abandon_all_now()` or before a cancelled `disconnect_server` of this
        name (Consiliency/pmcp#324)."""
        self._admit_connect(config.name)
        if isinstance(config.config, RemoteMcpServerConfig):
            if config.config.type in ("http", "streamable-http"):
                await self._connect_streamable_http(config)
            else:
                await self._connect_sse(config)
            return

        await self._connect_stdio(config)

    def _publish_catalog_change(self, kind: str) -> None:
        """Publish one catalog-kind change to the sink, unconditionally."""
        if kind == "tools":
            self._catalog_events.note_tools_changed()
        elif kind == "resources":
            self._catalog_events.note_resources_changed()
        elif kind == "prompts":
            self._catalog_events.note_prompts_changed()

    def _catalog_publishing_suppressed(self, server_name: str) -> bool:
        """True while `server_name` is mid-reconcile, i.e. while its `note_*`
        publishes are being withheld.

        Suppression is keyed by server name rather than being a global flag on
        purpose: reconciling A must not swallow the connect-time publishes of a
        B being indexed concurrently. The reconcile republishes for itself, once
        per kind that actually differs, once it has finished churning.

        Deliberately a *predicate* rather than a wrapper around the sink: each
        publishing mutator keeps its literal `self._catalog_events.note_*(...)`
        call, which is what `tests/runtime/test_publisher_coverage.py`'s AST
        honesty guard asserts. Hiding those calls behind a helper would make
        that guard vacuous.
        """
        return bool(self._catalog_suppressed.get(server_name))

    def _server_catalog_snapshot(self, name: str) -> dict[str, dict[str, Any]]:
        """What one server currently owns, per catalog kind, by *content*.

        Not identifier sets and emphatically not counts. A count-based diff
        misses a rename, an identifier-set diff misses an edit in place: a tool
        whose `description` or `inputSchema` changed under the same name is a
        real catalog change that a subscriber has to hear about. Comparing the
        model dumps catches adds, removes, renames, and edits with one rule.

        `model_dump()` in python mode, not `mode="json"`: every field here was
        parsed out of JSON already, so the python dump is comparable by `==`
        without paying for -- or risking a serializer warning on -- the
        arbitrary values carried in `raw_metadata`.
        """
        return {
            "tools": {
                k: v.model_dump()
                for k, v in self._tools.items()
                if v.server_name == name
            },
            "resources": {
                k: v.model_dump()
                for k, v in self._resources.items()
                if v.server_name == name
            },
            "prompts": {
                k: v.model_dump()
                for k, v in self._prompts.items()
                if v.server_name == name
            },
        }

    def _remove_server_indexes(
        self,
        name: str,
        *,
        drop_tasks: bool = True,
        kinds: Collection[str] | None = None,
    ) -> None:
        """Remove catalog entries owned by one server.

        `drop_tasks=False` keeps the server's tracked `McpTaskRecord`s. Dropping
        them is right for a disconnect, where the downstream's tasks died with
        the connection, and wrong for a `list_changed` reconcile, where the
        server is still up and its in-flight tasks are still running -- evicting
        them there would silently break `gateway.tasks_list`/`tasks_result`.

        `kinds=None` removes all three, which is what a disconnect wants.
        Reconciliation passes only the kinds whose re-listing actually
        succeeded: a `resources/list` that failed means "we could not ask", and
        turning that into "they are gone" both deletes entries the server still
        has and publishes a false removal.
        """
        selected = CATALOG_KINDS if kinds is None else tuple(kinds)
        tools_removed = False
        if "tools" in selected:
            for tool_id, tool in list(self._tools.items()):
                if tool.server_name == name:
                    self._tools.pop(tool_id, None)
                    tools_removed = True
        resources_removed = False
        if "resources" in selected:
            for resource_id, resource in list(self._resources.items()):
                if resource.server_name == name:
                    self._resources.pop(resource_id, None)
                    resources_removed = True
        prompts_removed = False
        if "prompts" in selected:
            for prompt_id, prompt in list(self._prompts.items()):
                if prompt.server_name == name:
                    self._prompts.pop(prompt_id, None)
                    prompts_removed = True
        if drop_tasks:
            for key in list(self._tasks):
                if key[0] == name:
                    self._tasks.pop(key, None)
        suppressed = self._catalog_publishing_suppressed(name)
        if tools_removed and not suppressed:
            self._catalog_events.note_tools_changed()
        if resources_removed and not suppressed:
            self._catalog_events.note_resources_changed()
        if prompts_removed and not suppressed:
            self._catalog_events.note_prompts_changed()

    def _server_supports_tasks(self, managed: ManagedClient) -> bool:
        capabilities = managed.status.server_capabilities or {}
        return "tasks" in capabilities and capabilities.get("tasks") is not False

    def _tool_task_support(self, tool_info: ToolInfo) -> TaskSupportMode:
        support = (tool_info.execution or {}).get("taskSupport")
        if support in {"optional", "required"}:
            return support  # type: ignore[return-value]
        return "forbidden"

    def _task_wire_metadata(
        self, task: TaskMetadataInput | dict[str, Any] | None
    ) -> dict[str, Any]:
        if task is None:
            return {}
        parsed = (
            task
            if isinstance(task, TaskMetadataInput)
            else TaskMetadataInput.model_validate(task)
        )
        payload: dict[str, Any] = {}
        if parsed.metadata:
            payload["metadata"] = parsed.metadata
        if parsed.ttl is not None:
            payload["ttl"] = parsed.ttl
        if parsed.poll_interval is not None:
            payload["pollInterval"] = parsed.poll_interval
        if parsed.requestor_context:
            payload["requestorContext"] = parsed.requestor_context
        return payload

    def _task_request_params(
        self,
        *,
        task_id: str | None = None,
        cursor: str | None = None,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if task_id is not None:
            payload["taskId"] = task_id
        if cursor:
            payload["cursor"] = cursor
        if requestor_context:
            payload["task"] = {"requestorContext": requestor_context}
        return payload

    def _extract_task_payload(self, result: dict[str, Any]) -> dict[str, Any] | None:
        task = result.get("task")
        if isinstance(task, dict):
            return task
        if isinstance(result.get("taskId"), str):
            return result
        return None

    def _task_info_from_payload(self, payload: dict[str, Any]) -> McpTaskInfo | None:
        task_id = payload.get("taskId") or payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return None
        status_message = payload.get("statusMessage", payload.get("status_message"))

        def pick(name: str, keys: tuple[str, ...], values: tuple[Any, ...]) -> Any:
            # One field, several wire aliases (`values` is `payload.get(key)`
            # for each of `keys`, in precedence order). The first USABLE
            # value wins, so `updatedAt: null` next to a usable
            # `lastUpdatedAt` does not hide it. With none usable, the first
            # value sent is passed on for the model to flag. A JSON `null`
            # the downstream SENT is not an absent value: only `ttl: null`
            # means something in MCP ("unlimited"), so for these hints it is
            # unusable -- it must not read as "not sent", or `_record_task`
            # would substitute the current time (Consiliency/pmcp#298).
            sent = [value for key, value in zip(keys, values) if key in payload]
            for value in sent:
                if task_hint_is_usable(name, value):
                    return value
            for value in sent:
                if value is not None:
                    return value
            return UNUSABLE_TASK_VALUE if sent else None

        status = pick("status", ("status",), (payload.get("status"),))
        created_at = pick(
            "created_at",
            ("createdAt", "created_at"),
            (payload.get("createdAt"), payload.get("created_at")),
        )
        updated_at = pick(
            "updated_at",
            ("updatedAt", "updated_at", "lastUpdatedAt", "last_updated_at"),
            (
                payload.get("updatedAt"),
                payload.get("updated_at"),
                payload.get("lastUpdatedAt"),
                payload.get("last_updated_at"),
            ),
        )
        poll_interval = pick(
            "poll_interval",
            ("pollInterval", "poll_interval"),
            (payload.get("pollInterval"), payload.get("poll_interval")),
        )

        return McpTaskInfo(
            task_id=task_id,
            status=status,
            status_message=status_message if isinstance(status_message, str) else None,
            created_at=created_at,
            updated_at=updated_at,
            ttl=payload.get("ttl"),
            poll_interval=poll_interval,
            raw=payload,
        )

    def _record_task(
        self,
        server_name: str,
        task_info: McpTaskInfo,
        *,
        tool_id: str | None = None,
        requestor_context: dict[str, Any] | None = None,
    ) -> McpTaskRecord:
        existing = self._tasks.get((server_name, task_info.task_id))
        now = time.time()
        # created_at: the first usable value pmcp saw (Consiliency/pmcp#298).
        created_at = (
            existing.created_at
            if existing is not None and existing.created_at is not None
            else task_info.created_at
        )
        unusable = [
            name
            for name in task_info.unusable_fields
            if not (name == "created_at" and created_at is not None)
        ]
        # updated_at: the downstream's usable value; None when it was unusable
        # (named in `unusable_fields`); pmcp's observation time only when the
        # downstream sent none at all -- `is None`, not `or`, so a usable 0.0
        # is kept (Consiliency/pmcp#298).
        updated_at = task_info.updated_at
        if updated_at is None and "updated_at" not in task_info.unusable_fields:
            updated_at = now
        record = McpTaskRecord(
            task_id=task_info.task_id,
            status=task_info.status,
            status_message=task_info.status_message,
            created_at=created_at,
            updated_at=updated_at,
            ttl=task_info.ttl,
            poll_interval=task_info.poll_interval,
            unusable_fields=unusable,
            raw=task_info.raw,
            server_name=server_name,
            tool_id=tool_id or (existing.tool_id if existing else None),
            requestor_context=requestor_context
            or (existing.requestor_context if existing else None),
        )
        self._record_order += 1
        record._recorded_order = self._record_order
        self._tasks[(server_name, task_info.task_id)] = record
        self._evict_terminal_tasks()
        return record

    def _evict_terminal_tasks(self) -> None:
        """Prune oldest terminal task records past the retention cap.

        Only completed/failed/cancelled records are candidates; active tasks are
        left untouched. Eviction targets the records pmcp recorded least
        recently (``_recorded_order``), so recently-seen tasks remain queryable.
        """
        terminal = [
            (key, record)
            for key, record in self._tasks.items()
            if self._terminal_task(record)
        ]
        excess = len(terminal) - self._max_terminal_tasks
        if excess <= 0:
            return
        # Least recently recorded by pmcp first. The downstream's own
        # timestamps play no part: a far-future `lastUpdatedAt` cannot keep one
        # server's records past another's, and an honest server whose clock
        # runs behind cannot lose its records early (Consiliency/pmcp#298).
        terminal.sort(key=lambda item: item[1]._recorded_order)
        for key, _record in terminal[:excess]:
            self._tasks.pop(key, None)

    def _terminal_task(self, task: McpTaskRecord) -> bool:
        return task.status in {"completed", "failed", "cancelled"}

    def get_task_record(self, server_name: str, task_id: str) -> McpTaskRecord | None:
        return self._tasks.get((server_name, task_id))

    def get_tracked_tasks(self, server_name: str | None = None) -> list[McpTaskRecord]:
        return sorted(
            [
                task
                for (server, _), task in self._tasks.items()
                if server_name is None or server == server_name
            ],
            key=lambda task: (task.server_name, task.task_id),
        )

    def get_active_tasks(self, server_name: str | None = None) -> list[McpTaskRecord]:
        return [
            task
            for task in self.get_tracked_tasks(server_name)
            if not self._terminal_task(task)
        ]

    async def cancel_active_tasks(
        self, server_name: str | None = None
    ) -> tuple[int, list[str]]:
        cancelled = 0
        errors: list[str] = []
        for task in list(self.get_active_tasks(server_name)):
            ok, _record, message = await self.cancel_task(
                task.server_name, task.task_id, force=True
            )
            if ok:
                cancelled += 1
            else:
                errors.append(message)
        return cancelled, errors

    def _index_tools(
        self,
        name: str,
        tools: list[dict[str, Any]],
        *,
        parsed: list[tuple[str, ToolInfo]] | None = None,
    ) -> int:
        """Index one server's tools, skipping any entry we cannot parse.

        The guard is per *entry*, not per call, and that placement is the whole
        point. Reconciliation removes this server's entries and then calls this
        method; an exception escaping here would leave the removal done and the
        re-index half-finished, so one malformed tool from a downstream would
        silently cost the server its entire catalog -- permanently, since the
        read loop stays healthy and no reconnect comes along to heal it. A
        single `try` around the loop would be barely better: it would still lose
        every entry after the bad one.

        `_index_resources` / `_index_prompts` guard identically. `connect_server`
        and `refresh` reach the same three methods, so they inherit this too: a
        server with one unparseable tool now connects with the rest of its
        catalog instead of failing outright.

        The parsing itself lives in `_parse_tool_entries`, which writes nothing.
        `parsed` lets a caller that has already run it -- `_reconcile_once`,
        which must know how many entries survive parsing *before* it removes
        anything -- hand the result straight in, so each listing is parsed once
        and each unparseable entry is logged once. Callers with a raw listing
        omit it and this method parses for them.

        The write stays here, in the method the publisher-coverage AST guard
        attributes it to. The *count* comes from `_distinct_indexed`, and is a
        count of entries that actually landed, so duplicate identities in one
        listing -- one catalog key, whatever the list length -- are counted
        once.
        """
        entries = (
            _parse_tool_entries(name, tools, self._max_tools_per_server)
            if parsed is None
            else parsed
        )
        for tool_id, tool_info in entries:
            self._tools[tool_id] = tool_info
        indexed = _distinct_indexed(name, "tool", entries)
        if indexed and not self._catalog_publishing_suppressed(name):
            self._catalog_events.note_tools_changed()
        return indexed

    def _index_resources(
        self,
        name: str,
        resources: list[dict[str, Any]],
        *,
        parsed: list[tuple[str, ResourceInfo]] | None = None,
    ) -> int:
        """Index one server's resources, skipping any entry we cannot parse.

        Per-entry, for the reason spelled out on `_index_tools`, and `parsed`
        for the reason spelled out there too. The count returned is what was
        actually indexed, not what was offered, so a caller reporting it is not
        overstating the catalog -- a promise this docstring made before the
        code kept it, and `_distinct_indexed` is where it is now kept.
        """
        entries = _parse_resource_entries(name, resources) if parsed is None else parsed
        for resource_id, resource_info in entries:
            self._resources[resource_id] = resource_info
        indexed = _distinct_indexed(name, "resource", entries)
        if indexed and not self._catalog_publishing_suppressed(name):
            self._catalog_events.note_resources_changed()
        return indexed

    def _index_prompts(
        self,
        name: str,
        prompts: list[dict[str, Any]],
        *,
        parsed: list[tuple[str, PromptInfo]] | None = None,
    ) -> int:
        """Index one server's prompts, skipping any entry we cannot parse.

        Per-entry, for the reason spelled out on `_index_tools`. The count
        returned is what was actually indexed, not what was offered -- see
        `_distinct_indexed`, which is where that is made true.
        """
        entries = _parse_prompt_entries(name, prompts) if parsed is None else parsed
        for prompt_id, prompt_info in entries:
            self._prompts[prompt_id] = prompt_info
        indexed = _distinct_indexed(name, "prompt", entries)
        if indexed and not self._catalog_publishing_suppressed(name):
            self._catalog_events.note_prompts_changed()
        return indexed

    async def _fetch_server_listings(
        self, managed: ManagedClient
    ) -> dict[str, list[dict[str, Any]] | None]:
        """List one server's three catalog kinds, mutating nothing.

        Every downstream request reconciliation makes lives here, and no catalog
        write does. That separation is the whole point: the caller can do all of
        its awaiting first and then apply the result in one synchronous block,
        so the catalog is never left empty across a network round trip for a
        concurrent `gateway.invoke` to trip over.

        Return shape, per kind:

        - `list[...]` -- the server answered, and the reply carried the
          collection the protocol requires. An **empty list is an answer**: it
          means the kind is genuinely empty and its entries should be cleared.
        - `None` -- we could not read an answer. The caller must leave that kind
          exactly as it was and publish nothing for it.

        `None` covers two cases that used to look different and are not. The
        request failing is the obvious one. The other is a reply we cannot read:
        its required collection absent, or present but not a list. Those are
        malformed, not empty -- see `_listing_entries`, which draws the line.
        Any kind can now come back `None`, `tools` included.

        Only `tools/list` failing raises. A server that does not implement
        resources or prompts is ordinary and answers those two with an error,
        which is why they are gathered with `return_exceptions=True` and mapped
        to `None` rather than propagated.
        """
        name = managed.config.name

        # All three gathered together, tools included. Awaiting tools to
        # completion first would let a failure on its page TWO escape and cost
        # resources and prompts their reconcile entirely -- a healthy resources
        # change would sit unapplied because an unrelated kind paginated badly
        # (ah board review). Only a page-ONE tools failure is a connect-time
        # error, and that is re-raised below; a later page is just this kind
        # being unreadable, like any other.
        listing_results = await asyncio.gather(
            self._fetch_listing_pages(managed, "tools"),
            self._fetch_listing_pages(managed, "resources"),
            self._fetch_listing_pages(managed, "prompts"),
            return_exceptions=True,
        )

        tools_result = listing_results[0]
        if isinstance(tools_result, BaseException):
            # Preserves the contract that a server which cannot list its tools
            # is a connect-time error, while one without resources or prompts
            # is ordinary. `_fetch_listing_pages` only lets page one raise.
            raise tools_result
        listings: dict[str, list[dict[str, Any]] | None] = {"tools": tools_result}
        for kind, result in (
            ("resources", listing_results[1]),
            ("prompts", listing_results[2]),
        ):
            if isinstance(result, BaseException):
                logger.debug(f"Server {name} doesn't support {kind}: {result}")
                listings[kind] = None
            else:
                listings[kind] = result
        return listings

    async def _fetch_listing_pages(
        self, managed: ManagedClient, kind: str
    ) -> list[dict[str, Any]] | None:
        """Follow `nextCursor` for one kind, or `None` if any page is unreadable.

        The listing path used to send `{}` once and keep whatever came back, so
        a downstream with more entries than its page size had everything past
        page one silently missing -- and, once reconciliation started publishing,
        announced as removed (Consiliency/pmcp#173). The truncation predated
        that; what made it urgent is asserting freshness over a partial view.

        A failure on page N makes the WHOLE kind unreadable rather than partial.
        Merging the pages that did arrive is exactly the false-removal shape this
        module has now been corrected for four times: entries the server still
        has would be dropped and the drop published. `None` means "we could not
        read the answer", and the caller already handles that by keeping the
        prior entries and publishing nothing.

        `tools/list` failing on page one still raises, preserving the contract
        that a server which cannot list its tools is a connect-time error while
        a server without resources or prompts is ordinary.
        """
        collected: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None

        for page in range(_MAX_LISTING_PAGES):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            if page == 0:
                # Page one propagates: whether a server can list a kind at all
                # is the caller's decision to make, and for tools it is a
                # connect-time error.
                result = await self._send_request(managed, f"{kind}/list", params)
            else:
                # A later page failing says nothing about whether the kind is
                # supported -- only that we could not finish reading it. Raising
                # here would cost the OTHER two kinds their reconcile, since all
                # three are gathered together.
                try:
                    result = await self._send_request(managed, f"{kind}/list", params)
                except Exception as exc:
                    logger.warning(
                        f"[{managed.config.name}] {kind}/list page {page + 1} "
                        f"failed ({describe_exception(exc)}); discarding {len(collected)} entries "
                        f"already collected rather than publishing a partial "
                        f"listing"
                    )
                    return None

            entries = _listing_entries(managed.config.name, kind, result)
            if entries is None:
                # _listing_entries already logged why. One bad page discards the
                # whole kind on purpose -- see the docstring.
                if page > 0:
                    logger.warning(
                        f"[{managed.config.name}] {kind}/list page {page + 1} was "
                        f"unreadable; discarding {len(collected)} entries already "
                        f"collected rather than publishing a partial listing"
                    )
                return None
            collected.extend(entries)

            if not isinstance(
                result, dict
            ):  # pragma: no cover - _listing_entries guards
                return None
            # Presence by KEY, not truthiness. `nextCursor: 0` and `""` are
            # falsey, so `x or y` / `if not cursor` read them as "no more pages"
            # and hand back page one as the whole catalog -- the exact
            # truncation this method exists to stop -- while also slipping past
            # the non-string rejection below (ah board review).
            if "nextCursor" in result:
                raw_cursor = result["nextCursor"]
            elif "next_cursor" in result:
                raw_cursor = result["next_cursor"]
            else:
                return collected
            if raw_cursor is None:
                # An explicit null is the protocol's way of saying "no more".
                return collected
            if not isinstance(raw_cursor, str) or not raw_cursor:
                logger.warning(
                    f"[{managed.config.name}] {kind}/list returned an unusable "
                    f"cursor ({raw_cursor!r}); treating as unreadable rather "
                    f"than as the end of the listing"
                )
                return None
            if raw_cursor in seen_cursors:
                logger.warning(
                    f"[{managed.config.name}] {kind}/list repeated cursor "
                    f"{raw_cursor!r}; treating as unreadable rather than looping"
                )
                return None
            seen_cursors.add(raw_cursor)
            cursor = raw_cursor

        logger.warning(
            f"[{managed.config.name}] {kind}/list exceeded {_MAX_LISTING_PAGES} "
            f"pages; treating as unreadable rather than indexing a truncated view"
        )
        return None

    async def _index_capabilities(self, managed: ManagedClient) -> tuple[int, int, int]:
        name = managed.config.name
        listings = await self._fetch_server_listings(managed)

        indexed = self._index_tools(name, listings["tools"] or [])

        resources = listings["resources"]
        resource_count = (
            0 if resources is None else self._index_resources(name, resources)
        )

        prompts = listings["prompts"]
        prompt_count = 0 if prompts is None else self._index_prompts(name, prompts)

        # No flush() here, deliberately: IF-0-P3B-1's self-scheduling drain
        # is the correctness mechanism, not this call site, and EC-P3B-4
        # exercises exactly this path -- a flush() here would let that
        # acceptance test pass even if the self-scheduling drain were
        # completely broken.
        return indexed, resource_count, prompt_count

    def _handle_downstream_notification(
        self, name: str, managed: ManagedClient, method: str
    ) -> bool:
        """Act on one JSON-RPC notification received from a downstream server.

        This is IF-0-FANOUT-1, the downstream-event contract. It is called from
        both dispatch paths -- `_handle_stdout_line` (stdio) and `_read_sse`
        (remote) -- for every frame that carries a `method` and no `id`.

        Method mapping. Exactly three methods are recognised, each requesting a
        re-index of the *whole* of that one server's catalog (the gateway's
        catalog is index-backed, not proxied, so a per-kind refetch would still
        need the same round trip):

        - `notifications/tools/list_changed`
        - `notifications/resources/list_changed`
        - `notifications/prompts/list_changed`

        Reconcile-then-publish ordering. The gateway serves `gateway.invoke` and
        `gateway.catalog_search` out of its own index, so a notification
        forwarded straight to the subscription sink would tell a client to
        refetch and then hand it the stale catalog. Reconciliation therefore
        always completes before anything is published.

        Fetch-then-swap. Reconciliation lists the server's three catalog kinds
        first and writes nothing while doing so, then removes and re-indexes in
        a single synchronous block. Nothing can interleave inside that block, so
        a `gateway.invoke` running concurrently with a reconcile sees the old
        catalog or the new one, never an empty one. A kind whose listing failed
        is left exactly as it was -- "we could not ask" is not "they are gone" --
        and so are the three states reached by different routes to the same
        place: a reply missing its required collection or carrying a non-list in
        its place, and a listing that offered entries of which not one could be
        parsed. An answer of zero entries is still an answer and does clear the
        kind.

        Suppress-while-churning, publish-once-if-changed. The swap's removal and
        re-index halves both call `CatalogEventSink.note_*` unconditionally, so
        a chatty downstream would spam subscribers even when nothing moved. Sink
        calls originating from the server under reconciliation are suppressed
        for the duration of the swap; afterwards its entries are compared before
        against after, and exactly one `note_*` is published per catalog kind
        that actually differs. Entries, not identifiers and certainly not
        counts: a rename leaves the count unchanged, and an edited description
        or schema leaves the identifiers unchanged too.

        Unrecognised methods are a no-op. Any other `notifications/*` (or any
        other method name) returns False having published nothing, scheduled
        nothing, and raised nothing. This function never raises: `_read_sse`
        wraps its loop in a blanket `except Exception` that tears the connection
        down and triggers a reconnect, so a raise here would turn an unknown
        notification into a dropped server.

        Never blocks the caller. The reconcile runs in a task spawned with
        `asyncio.create_task`, never awaited inline -- see
        `_reconcile_server_catalog` for why an inline await deadlocks
        immediately.

        Args:
            name: The downstream server's registered name.
            managed: The client that received the notification.
            method: The notification's JSON-RPC `method`.

        Returns:
            True if the method was recognised and a reconcile was requested,
            False if it was ignored.
        """
        if method not in DOWNSTREAM_LIST_CHANGED_METHODS:
            return False

        existing = self._reconcile_tasks.get(name)
        if existing is not None and not existing.done():
            # Coalesce: a notification arriving mid-reconcile sets a re-run flag
            # the running task picks up, rather than spawning a second task. A
            # downstream that emits `list_changed` on every request is therefore
            # bounded to one in-flight re-index plus one queued re-run.
            self._reconcile_reruns.add(name)
            return True

        try:
            task = asyncio.create_task(self._reconcile_server_catalog(name))
        except RuntimeError:
            # No running loop. Both real dispatch paths run inside one, so this
            # is unreachable in production; returning False (rather than
            # raising) keeps the never-raises guarantee absolute.
            logger.debug(f"[{name}] No running loop; dropped {method}")
            return False
        self._reconcile_tasks[name] = task
        self._track_background_task(task, name)
        return True

    async def _reconcile_server_catalog(self, name: str) -> None:
        """Re-index one server's catalog, then publish what actually moved.

        Spawned, never awaited from a dispatch path. `_fetch_server_listings`
        awaits `_send_request`, and the future it waits on is resolved by the
        very read loop that received the notification --
        `pending.future.set_result` in `_handle_stdout_line` (stdio) and in
        `_read_sse` (remote). Awaiting inline would make that loop wait on a
        future only it can resolve: an immediate, total deadlock of the
        connection, not an occasional one.

        (Line references as surveyed pre-FANOUT, at ff2cb95: the await is
        `manager.py:1292`, the two resolutions are `:1791` and `:2010`. Anchored
        by symbol above because this change shifts all three.)

        Re-runs are a loop inside this one task rather than a second task, which
        is what keeps the coalescing bound at one in-flight reconcile per server.
        """
        try:
            while True:
                self._reconcile_reruns.discard(name)
                # Re-resolve the client every iteration: a reconnect swaps the
                # ManagedClient object outright, and a stale reference would
                # send `tools/list` down a transport that is already closed.
                managed = self._clients.get(name)
                if managed is None:
                    return
                await self._reconcile_once(name, managed)
                if name not in self._reconcile_reruns:
                    return
                # A re-run is pending. Wait a beat before honouring it so a
                # server that emits `list_changed` in reply to our own
                # `tools/list` cannot spin this loop at full speed; further
                # notifications during the wait fold into this same re-run.
                await asyncio.sleep(_RECONCILE_RERUN_DEBOUNCE_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            # Never let a reconcile surface as an unhandled task exception.
            logger.warning(
                f"[{name}] Catalog reconciliation task failed: {describe_exception(e)}"
            )
        finally:
            self._reconcile_reruns.discard(name)
            if self._reconcile_tasks.get(name) is asyncio.current_task():
                self._reconcile_tasks.pop(name, None)

    async def _reconcile_once(self, name: str, managed: ManagedClient) -> None:
        """One fetch-then-swap pass, published per kind only if that kind moved.

        Fetch first. Every downstream request happens before any catalog write,
        so a `tools/list` that fails costs nothing: there is nothing to roll
        back because nothing was removed. (The previous order -- remove, then
        re-list -- needed a rollback precisely because it had already destroyed
        the state it was trying to preserve.)

        Then swap, synchronously. The block below contains no `await` by
        construction: `_remove_server_indexes` and the three `_index_*` are all
        plain methods. asyncio cannot interleave another task inside it, so a
        concurrent `gateway.invoke` sees either the whole old catalog or the
        whole new one and never the empty window between them. Adding an await
        in there would silently reintroduce that window.

        Per kind, independently. A kind whose listing failed is absent from
        `refreshed`: it is neither removed nor re-indexed nor published, because
        "we could not ask" is not "they are gone". A kind that answered is
        replaced wholesale and published only if its entries actually differ.

        A listing nobody could parse is a failed listing. If a kind offers
        entries and *not one* of them survives parsing, we are in the same
        epistemic state as a failed request -- we could not read the answer --
        so that kind is dropped from `refreshed` too and left exactly as it was.
        A parse failure degrades our visibility of the server's catalog; it does
        not make the server's tools stop working, and publishing a removal on
        the strength of it would tell every subscriber they are gone. Note the
        boundary: a listing that offers *zero* entries is a real answer -- the
        server emptied that kind -- and still clears and publishes. Only
        offered-but-none-parseable is treated as failure. Mixed listings keep
        the per-entry semantics: something parsed, so the kind answered and is
        replaced by what parsed.

        A listing that never arrived readably never reaches that rule at all.
        `_listing_entries` has already collapsed an absent collection, or one
        that is not a list, to `None` -- so it is `offered is None` here, the
        same branch as a request that failed outright. That distinction is load
        bearing: `result: {}` and `{"tools": []}` are different answers, and
        only the second one means the kind is empty. Likewise an entry with no
        identity now fails to parse (`_required_identity`) instead of indexing
        as `srv::`, so a listing of nothing but such entries lands in the
        offered-but-none-parseable rule above rather than replacing real entries
        with synthetic ones.

        (On connect there is no prior catalog to protect -- `_connect_stdio` and
        friends remove this server's indexes first -- so `_index_capabilities`
        deliberately keeps the plain per-entry behaviour: an all-malformed
        listing there yields an empty catalog for that kind, not a failed
        connect.)

        Parsing happens before the apply block, not inside it. It is pure CPU
        and writes nothing, so doing it early costs nothing and is what lets the
        decision above be made while the old catalog is still intact.
        """
        try:
            listings = await self._fetch_server_listings(managed)
        except Exception as e:
            # The downstream announced a change and then failed to list. The
            # catalog has not been touched, so leaving it alone *is* the
            # rollback, and there is nothing to publish.
            logger.warning(
                f"[{name}] Catalog reconciliation failed: {describe_exception(e)}"
            )
            return

        tools = listings["tools"]
        resources = listings["resources"]
        prompts = listings["prompts"]
        parsed_tools = (
            None
            if tools is None
            else _parse_tool_entries(name, tools, self._max_tools_per_server)
        )
        parsed_resources = (
            None if resources is None else _parse_resource_entries(name, resources)
        )
        parsed_prompts = (
            None if prompts is None else _parse_prompt_entries(name, prompts)
        )

        usable: dict[str, bool] = {}
        for kind, offered, parsed in (
            ("tools", tools, parsed_tools),
            ("resources", resources, parsed_resources),
            ("prompts", prompts, parsed_prompts),
        ):
            if offered is None:
                usable[kind] = False
                continue
            if offered and not parsed:
                if kind == "tools" and self._max_tools_per_server < 1:
                    # #175 item 3. A zero limit empties the parse result before
                    # a single entry is looked at, so the generic message below
                    # would accuse the downstream of sending garbage for a
                    # decision this gateway made itself.
                    #
                    # `LimitsPolicy` now bounds the field at `ge=1` (#207), so a
                    # policy file can no longer reach here -- #202 made such a
                    # file terminate startup. `self._max_tools_per_server` comes
                    # from the constructor parameter, which is deliberately left
                    # unbounded, so this branch still guards a live path.
                    logger.warning(
                        f"[{name}] max_tools_per_server is "
                        f"{self._max_tools_per_server}, so none of the "
                        f"{len(offered)} tools offered could be indexed; "
                        "keeping the previous tools rather than reporting "
                        "them removed"
                    )
                else:
                    logger.warning(
                        f"[{name}] Every {kind} entry in the listing was "
                        f"unparseable ({len(offered)} offered); keeping the "
                        f"previous {kind} rather than reporting them removed"
                    )
                usable[kind] = False
                continue
            usable[kind] = True

        refreshed = tuple(k for k in CATALOG_KINDS if usable[k])
        before = self._server_catalog_snapshot(name)

        # ---- apply: synchronous, no `await` below this line --------------
        self._catalog_suppressed[name] = self._catalog_suppressed.get(name, 0) + 1
        try:
            # `drop_tasks=False`: the server is still connected, so its tracked
            # task records must survive a catalog refresh.
            self._remove_server_indexes(name, drop_tasks=False, kinds=refreshed)
            if usable["tools"] and tools is not None:
                self._index_tools(name, tools, parsed=parsed_tools)
            if usable["resources"] and resources is not None:
                self._index_resources(name, resources, parsed=parsed_resources)
            if usable["prompts"] and prompts is not None:
                self._index_prompts(name, prompts, parsed=parsed_prompts)
        finally:
            depth = self._catalog_suppressed.get(name, 1) - 1
            if depth > 0:
                self._catalog_suppressed[name] = depth
            else:
                self._catalog_suppressed.pop(name, None)
        # ---- end apply ----------------------------------------------------

        after = self._server_catalog_snapshot(name)
        for kind in refreshed:
            if before[kind] != after[kind]:
                self._publish_catalog_change(kind)

    async def _connect_stdio(self, config: ResolvedServerConfig) -> None:
        """Connect to a local stdio MCP server."""
        name = config.name

        # Clean up any existing live connection before spawning a replacement
        if name in self._clients:
            existing = self._clients[name]
            logger.warning(
                f"[{name}] Existing live connection found; cleaning up before reconnect"
            )
            await self._cleanup_client(name, existing)
        else:
            self._remove_server_indexes(name)

        # Initialize status
        status = ServerStatus(
            name=name,
            status=ServerStatusEnum.CONNECTING,
            tool_count=0,
        )
        self._servers[name] = status

        if not isinstance(config.config, LocalMcpServerConfig):
            raise ValueError(f"Server {name} has unsupported local config type")

        local_config = config.config

        if not local_config.command:
            raise ValueError(
                f"Server {name} missing command - only stdio transport supported"
            )

        logger.info(f"Connecting to MCP server: {name}")
        # A package runner fetches and runs the package it names as it spawns,
        # so this spawn is an install spawn (EC-PKGID-4) and logs the rendered,
        # secret-safe argv at WARNING, ahead of the spawn so a spawn that raises
        # still leaves the record. Any other executable is a local binary: it
        # installs nothing, and warning on every start would bury the ones
        # that do.
        if (
            normalized_executable_name(local_config.command)
            in _PACKAGE_RUNNER_EXECUTABLES
        ):
            logger.warning(
                f"Spawning {_operator_safe(name)}: "
                f"{_render_install_argv([local_config.command, *local_config.args])}"
            )

        # Build environment: inherit the gateway env MINUS PMCP-managed secrets
        # (so this server never receives another server's stored credentials),
        # then apply this server's own resolved credentials.
        env = sanitized_subprocess_env(local_config.env, self._project_root)

        # Spawn process (semaphore caps concurrent spawns to avoid FD exhaustion)
        async with self._spawn_semaphore:
            process = await asyncio.create_subprocess_exec(
                local_config.command,
                *local_config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=local_config.cwd,
                env=env,
                limit=_stdio_read_limit(),
                # New session/process group so we can reap the whole tree
                # (e.g. browsers launched by the server) on disconnect.
                start_new_session=True,
            )

        managed = ManagedClient(
            config=config,
            process=process,
            status=status,
            # The spawn contract: `start_new_session=True` above makes the
            # child a session and group leader, so its pgid IS its pid --
            # true even if it has already exited and been reaped.
            group_pgid=_spawned_group_pgid(process),
        )
        self._clients[name] = managed
        self._note_connect_client(managed)

        # Start reading stderr in background
        if process.stderr:
            managed.stderr_task = self._track_background_task(
                asyncio.create_task(self._read_stderr(name, process.stderr)),
                name,
            )

        try:
            # Start reading stdout
            managed.read_task = self._track_background_task(
                asyncio.create_task(self._read_stdout(name, managed)),
                name,
            )

            # Initialize connection
            await self._send_initialize(managed)

            indexed, resource_count, prompt_count = await self._index_capabilities(
                managed
            )

            # Update status
            status.status = ServerStatusEnum.ONLINE
            status.tool_count = indexed
            status.resource_count = resource_count
            status.prompt_count = prompt_count
            status.last_connected_at = time.time()

            logger.info(
                f"Connected to {name}: {indexed} tools, "
                f"{resource_count} resources, {prompt_count} prompts indexed"
            )

        except asyncio.CancelledError as e:
            # Cancelled DURING the handshake: no graceful teardown, the
            # synchronous one (Consiliency/pmcp#324). On main this path ran
            # no teardown at all and leaked the process and the entry.
            status.status = ServerStatusEnum.ERROR
            status.last_error = _handshake_error(e)
            self._abandon_client_io(name, managed)
            self._drop_client(name, managed)
            raise
        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = describe_exception(e)
            try:
                await self._abort_stdio_handshake(name, managed, process)
            except asyncio.CancelledError:
                # Cancelled while tearing down a failed handshake.
                self._abandon_client_io(name, managed)
                raise
            finally:
                # Drop the stale ERROR client so it can't be found as a live
                # connection on the next connect attempt (issue: stale entry +
                # leak) -- also when a teardown step raises.
                self._drop_client(name, managed)
            raise

    async def _abort_stdio_handshake(
        self,
        name: str,
        managed: ManagedClient,
        process: asyncio.subprocess.Process,
    ) -> None:
        """The graceful teardown a failed stdio handshake owes. A caller
        cancellation propagates out of any await here; the handler above
        then finishes synchronously."""
        await _reap_child(managed.read_task)
        await _reap_child(managed.stderr_task)
        # A writer started before the handshake failed (e.g. a `ping`
        # answered during `initialize`) must not survive this pop
        # (Consiliency/pmcp#287).
        await self._teardown_outbound(managed)
        await _terminate_process_tree(process, name, group_pgid=managed.group_pgid)

    def _drop_client(self, name: str, managed: ManagedClient) -> None:
        if self._clients.get(name) is managed:
            self._clients.pop(name, None)

    def abandon_all_now(self) -> None:
        """Abandon every client synchronously, with no await: kill each
        process tree (through its retained group), abandon each remote
        owner, cancel the background tasks without waiting, and drop the
        client and task registries (Consiliency/pmcp#324, codex round 6).

        For a caller whose own teardown work runs in another task that can
        be cancelled before its first instruction -- `GatewayServer.shutdown`
        bounds `disconnect_all()` with `bounded_wait`, and loop shutdown can
        cancel that task before it starts, so neither its workers' handlers
        nor its parent fallback run. The caller's `except CancelledError`
        calls this and re-raises. Repeating it for clients `disconnect_all`
        already abandoned is harmless (see `_disconnect_all_unlocked`).
        Catalogs are left to `disconnect_all` (the publisher-coverage guard
        keeps catalog writes there); the process is exiting.

        It is terminal: every connect ticket is superseded for every name,
        forever, so no connect path -- in flight (a `refresh` holding the
        lock), queued behind one, or requested later -- spawns or joins a
        connect afterwards, and `adopt_process` registers nothing
        (`_admit_connect`)."""
        self._all_superseded_at = _ABANDONED_AT
        for name, managed in list(self._clients.items()):
            self._abandon_client_io(name, managed)
        self._cancel_background_tasks_now(spare_requests=True)
        self._clients.clear()
        self._connect_tasks.clear()
        self._reconnect_tasks.clear()
        self._reconcile_tasks.clear()

    def _abandon_client_io(self, name: str, managed: ManagedClient) -> None:
        """Everything a teardown must still do when its caller has been
        cancelled -- synchronously, with no await (Consiliency/pmcp#324):

        * cancel the client's reader, stderr and outbound-writer tasks
          without awaiting them (each logs a genuine failure by type);
        * stdio: SIGKILL the process tree (`_kill_process_tree_now`); no
          SIGTERM grace for a cancelled caller;
        * remote: signal the transport owner and cancel it from loop
          callbacks (`_abandon_owner`) without awaiting it. The graceful
          close is abandoned, so a streamable-HTTP
          session-ending DELETE may not be sent; the server reaps the
          session on its own timeout.

        Never raises: a failure in one step is logged by type, and the
        caller's cancellation is what propagates.

        First, an ONLINE client is marked OFFLINE, before its reader is
        cancelled: the reader's `finally` schedules an auto-reconnect for a
        client it still sees ONLINE, and on this path nothing would cancel
        that reconnect (claude round 4, F1: a `disconnect_all` cancelled
        before its workers ran respawned every server 5 s later). One spot
        for every synchronous abandon path -- handshakes, `_cleanup_client`,
        `disconnect_server`, `_shutdown_one`, the `disconnect_all` fallback
        and `adopt_process`.
        """
        if managed.status.status == ServerStatusEnum.ONLINE:
            managed.status.status = ServerStatusEnum.OFFLINE
        for task in (managed.read_task, managed.stderr_task, managed.outbound_writer):
            _cancel_without_waiting(task)
        managed.outbound = None
        managed.outbound_writer = None
        try:
            if managed.is_remote:
                _abandon_owner(managed.transport_owner_task, managed.transport_shutdown)
            else:
                _kill_process_tree_now(managed.process, group_pgid=managed.group_pgid)
        except Exception as exc:
            logger.warning(
                f"[{name}] teardown step failed while abandoning a cancelled "
                f"caller's client: {type(exc).__name__}"
            )

    async def _connect_sse(self, config: ResolvedServerConfig) -> None:
        """Connect to a remote SSE MCP server."""
        if not isinstance(config.config, RemoteMcpServerConfig):
            raise ValueError(f"Server {config.name} has unsupported remote config type")

        remote_config = config.config
        headers = _remote_headers(
            config.name, remote_config, project_root=self._project_root
        )
        await self._connect_remote_stream(
            config,
            sse_client(remote_config.url, headers=headers),
            transport_name="SSE",
            resolved_headers=headers,
        )

    async def _connect_streamable_http(self, config: ResolvedServerConfig) -> None:
        """Connect to a remote streamable-HTTP MCP server."""
        if not isinstance(config.config, RemoteMcpServerConfig):
            raise ValueError(f"Server {config.name} has unsupported remote config type")

        remote_config = config.config
        headers = _remote_headers(
            config.name, remote_config, project_root=self._project_root
        )
        # mcp 2.0.0's streamable_http_client() no longer builds its own httpx
        # client from headers/timeout kwargs the way 1.x's streamablehttp_client()
        # did internally via create_mcp_http_client(); it takes a caller-supplied
        # httpx2.AsyncClient and (per IF-0-P2-2) does not close it. Reproduce
        # create_mcp_http_client's behaviour explicitly: follow_redirects=True
        # (httpx2's own default is False) and the same (30s connect, 300s read)
        # timeout. `headers` is omitted entirely when unset, matching 1.x's
        # create_mcp_http_client(headers=None) passthrough.
        remote_timeout = httpx2.Timeout(30.0, read=300.0)
        if headers is not None:
            http_client = httpx2.AsyncClient(
                follow_redirects=True, timeout=remote_timeout, headers=headers
            )
        else:
            http_client = httpx2.AsyncClient(
                follow_redirects=True, timeout=remote_timeout
            )
        await self._connect_remote_stream(
            config,
            streamable_http_client(remote_config.url, http_client=http_client),
            transport_name="streamable HTTP",
            resolved_headers=headers,
            remote_http_client=http_client,
        )

    async def _own_remote_transport(
        self,
        name: str,
        transport_context: Any,
        remote_http_client: httpx2.AsyncClient | None,
        ready: asyncio.Future[tuple[Any, Any]],
        shutdown: asyncio.Event,
    ) -> None:
        """Own a remote client's transport for its whole lifetime: enter its
        exit stack here, park here, and unwind it here.

        anyio cancel scopes are bound to the task that creates them, so the
        task that enters this stack is the only task that may ever close it.
        This task exists so that task is always this one -- never a caller of
        `disconnect_server` / `_disconnect_all_unlocked` / `_cleanup_client`
        running in some other task.
        """
        try:
            async with AsyncExitStack() as stack:
                # LIFO: client entered first so it closes last, preserving
                # the ordering this code documented before this change --
                # transport closes first, the owned httpx2 client last, and
                # a failure entering the transport still closes the client
                # we already own rather than leaking it.
                if remote_http_client is not None:
                    await stack.enter_async_context(remote_http_client)
                transport = await stack.enter_async_context(transport_context)
                ready.set_result(transport[:2])
                await shutdown.wait()
        except BaseException as exc:
            if not ready.done():
                # Pre-handoff failure: the connect caller is the one waiting
                # on `ready`, so hand it the exception rather than raising
                # into a task nobody is awaiting yet. The `async with` above
                # has already unwound whatever it entered.
                ready.set_exception(exc)
                return
            # Post-handoff failure: do NOT swallow. `_close_remote_transport`
            # awaits this task and re-raises what it raises, which is what
            # keeps `disconnect_server`'s (False, cancelled, str(e)) contract
            # reachable.
            raise

    def _on_transport_owner_done(
        self, name: str, shutdown: asyncio.Event, task: asyncio.Task[None]
    ) -> None:
        """Log an owner task that exits before anyone asked it to.

        Without this, a transport that dies while parked at
        `await shutdown.wait()` is invisible until someone disconnects, and
        the loop separately logs "Task exception was never retrieved".
        Reading `task.exception()` here does not consume it: a later
        `_close_remote_transport` awaiting this task still re-raises the
        real failure.
        """
        if task.cancelled() or shutdown.is_set():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                f"[{name}] remote transport owner exited unexpectedly: "
                f"{describe_exception(exc)}"
            )

    async def _close_remote_transport(
        self, name: str, managed: ManagedClient, timeout: float = 5.0
    ) -> None:
        """Signal a remote client's transport owner task to unwind its stack
        and wait for it. The single entry point for all three close sites
        (`disconnect_server`, `_disconnect_all_unlocked._shutdown_one`,
        `_cleanup_client`) -- each decides for itself whether a failure here
        should propagate or be swallowed; this method never swallows on
        their behalf.

        Idempotent: setting the shutdown event twice, awaiting an already-
        finished owner, or being called for a client with no owner (stdio)
        all return without effect.
        """
        task, shutdown = managed.transport_owner_task, managed.transport_shutdown
        if task is None or shutdown is None:
            return
        shutdown.set()
        if task.done():
            # The owner already exited -- possibly with a genuine failure it
            # was carrying (the crash-while-parked case `_on_transport_owner_done`
            # only logs). Retrieve, don't discard: a cancelled owner closed
            # cleanly enough to report as closed, but a real exception must
            # still reach `disconnect_server`'s (False, cancelled, str(e)).
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    raise exc
            return
        try:
            # `asyncio.wait`, never `wait_for(shield(task))`: it does not
            # raise the owner's outcome, so a CancelledError here is our
            # caller's alone (Consiliency/pmcp#324).
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                # The budget bounds this graceful wait only, not the
                # escalation: awaiting the cancelled owner is itself
                # unbounded, and an __aexit__ that ignores cancellation hangs
                # there -- the same hang class as a dead-peer teardown.
                # Timeout-as-success is deliberate (a dead peer must not read
                # as "disconnect refused"), but a genuine failure surfacing
                # while the owner unwinds under our cancel still propagates.
                logger.warning(
                    f"[{name}] remote transport did not close within {timeout}s; "
                    "cancelling"
                )
                task.cancel()
                await asyncio.wait({task})
        except asyncio.CancelledError:
            # Our caller was cancelled, during the graceful wait or the
            # escalation. Escalate to the owner so its stack still unwinds,
            # in the owner, but do not wait for it: an owner whose
            # `__aexit__` blocks must not hold a cancelled caller -- or the
            # lifecycle lock `disconnect_server` holds (Consiliency/pmcp#324).
            # A genuine failure surfacing from that unwind is logged by the
            # callback rather than dropped.
            _abandon_owner(
                task, shutdown, lambda t: _log_abandoned_owner_failure(name, t)
            )
            raise
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                raise exc
        # NOTE: no `except Exception` here, deliberately. A transport exit
        # that genuinely fails must propagate, or disconnect_server's
        # `except Exception -> return (False, cancelled, str(e))` can never
        # fire and a broken teardown would report as a successful
        # disconnect. The swallow belongs at the two call sites that
        # legitimately must not fail -- _shutdown_one and _cleanup_client --
        # not here.

    async def _connect_remote_stream(
        self,
        config: ResolvedServerConfig,
        transport_context: Any,
        *,
        transport_name: str,
        resolved_headers: dict[str, str] | None = None,
        remote_http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        """Connect to a remote MCP server using a read/write stream transport."""
        name = config.name

        if name in self._clients:
            existing = self._clients[name]
            logger.warning(
                f"[{name}] Existing live connection found; cleaning up before reconnect"
            )
            await self._cleanup_client(name, existing)
        else:
            self._remove_server_indexes(name)

        status = ServerStatus(
            name=name,
            status=ServerStatusEnum.CONNECTING,
            tool_count=0,
        )
        self._servers[name] = status

        logger.info(f"Connecting to remote MCP server via {transport_name}: {name}")

        ready: asyncio.Future[tuple[Any, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        shutdown = asyncio.Event()
        owner_task = self._track_background_task(
            asyncio.create_task(
                self._own_remote_transport(
                    name, transport_context, remote_http_client, ready, shutdown
                )
            ),
            name,
        )
        owner_task.add_done_callback(
            lambda t: self._on_transport_owner_done(name, shutdown, t)
        )

        try:
            read_stream, write_stream = await ready
            managed = ManagedClient(
                config=config,
                process=None,
                is_remote=True,
                write_stream=write_stream,
                status=status,
                resolved_remote_headers=resolved_headers,
                remote_http_client=remote_http_client,
                transport_owner_task=owner_task,
                transport_shutdown=shutdown,
            )
            self._clients[name] = managed
            self._note_connect_client(managed)
        except BaseException:
            # BaseException, not Exception: cancellation is the case that
            # bites here, and it is a BaseException. Cancel rather than
            # signal-and-wait -- a cancelled caller must not linger, and the
            # peer reaps its own session on timeout. The owner's `async
            # with` unwinds in the owner, as always -- never touch its stack
            # from this task. Not awaited either (Consiliency/pmcp#324): an
            # owner whose enter ignores cancellation must not hold us.
            _abandon_owner(owner_task, shutdown)
            raise

        try:
            managed.read_task = self._track_background_task(
                asyncio.create_task(self._read_sse(name, managed, read_stream)),
                name,
            )

            await self._send_initialize(managed)

            indexed, resource_count, prompt_count = await self._index_capabilities(
                managed
            )

            status.status = ServerStatusEnum.ONLINE
            status.tool_count = indexed
            status.resource_count = resource_count
            status.prompt_count = prompt_count
            status.last_connected_at = time.time()

            logger.info(
                f"Connected to {name}: {indexed} tools, "
                f"{resource_count} resources, {prompt_count} prompts indexed"
            )

        except asyncio.CancelledError as e:
            # As in `_connect_stdio` (Consiliency/pmcp#324).
            status.status = ServerStatusEnum.ERROR
            status.last_error = _handshake_error(e)
            self._abandon_client_io(name, managed)
            self._drop_client(name, managed)
            raise
        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = describe_exception(e)
            try:
                await self._abort_remote_handshake(name, managed)
            except asyncio.CancelledError:
                self._abandon_client_io(name, managed)
                raise
            finally:
                # Drop the stale ERROR client so it can't be found as a live
                # connection on the next connect attempt.
                self._drop_client(name, managed)
            raise

    async def _abort_remote_handshake(self, name: str, managed: ManagedClient) -> None:
        """The graceful teardown a failed remote handshake owes; see
        `_abort_stdio_handshake`."""
        await _reap_child(managed.read_task)
        # Same as the stdio handshake path (Consiliency/pmcp#287).
        await self._teardown_outbound(managed)
        await self._close_remote_transport(name, managed)

    async def _read_stderr(self, name: str, stderr: asyncio.StreamReader) -> None:
        """Read stderr from a server process."""
        try:
            while True:
                try:
                    line = await bounded_wait(stderr.readline(), timeout=120.0)
                except asyncio.TimeoutError:
                    logger.debug(f"[{name}] stderr readline timed out, continuing")
                    continue
                if not line:
                    break
                logger.debug(f"[{name}] stderr: {line.decode().strip()}")
        except Exception as e:
            logger.debug(f"[{name}] stderr reader error: {describe_exception(e)}")

    def _handle_stdout_line(
        self, name: str, managed: ManagedClient, line: bytes, now: float
    ) -> None:
        """Dispatch one complete JSON-RPC line from a downstream server's stdout."""
        try:
            # `errors="replace"`: a bare `.decode()` raises UnicodeDecodeError on
            # a non-UTF-8 byte, and that is NOT a `json.JSONDecodeError`, so it
            # escaped the handler below, propagated to the stdout read loop's
            # broad `except Exception`, and EXITED the loop -- marking the server
            # unexpectedly disconnected. That is the "the server hangs" report
            # (review finding C-03, Consiliency/pmcp#232). Replacement characters
            # make it a normal parse failure, which is handled.
            text = line.decode("utf-8", "replace")
            if "\ufffd" in text:
                # `errors="replace"` keeps the connection alive, but an
                # undecodable byte INSIDE a JSON string still parses -- it just
                # becomes U+FFFD. Without this the corruption would be accepted
                # silently, which trades one failure mode for a quieter one.
                logger.warning(
                    f"[{name}] downstream sent undecodable bytes on stdout; "
                    "the line was decoded with replacement characters"
                )
            message = json.loads(text)
        except json.JSONDecodeError:
            # Non-JSON output already counted as a heartbeat by the caller.
            logger.debug(
                f"[{name}] Non-JSON output: {line.decode(errors='replace').strip()}"
            )
            return
        except (ValueError, RecursionError) as e:
            # Parses as neither JSON nor a JSONDecodeError: an integer over
            # `sys.get_int_max_str_digits()` raises a plain ValueError, and
            # deeply nested arrays/objects raise RecursionError. Both used to
            # escape to the read loop's broad `except` and end it. Value-free:
            # the line is attacker-sized (Consiliency/pmcp#287).
            logger.debug(f"[{name}] dropped unparseable frame ({type(e).__name__})")
            return
        self._dispatch_downstream_frame(name, managed, message, now)

    def _dispatch_downstream_frame(
        self, name: str, managed: ManagedClient, frame: Any, now: float
    ) -> None:
        """Route one parsed downstream JSON-RPC frame; never raises.

        Shared by the stdio (`_handle_stdout_line`) and remote (`_read_sse`)
        read loops. The frame is untrusted, and both loops end -- dropping the
        connection -- on any exception that reaches their broad `except`, so
        one malformed frame must never be able to raise out of here
        (Consiliency/pmcp#287). `_route_downstream_frame` rejects every shape
        its own accesses depend on; this wrapper is the backstop for anything
        they miss, and logs only the exception type -- never a frame value.
        """
        try:
            self._route_downstream_frame(name, managed, frame, now)
        except Exception as e:
            logger.warning(
                f"[{name}] dropped a downstream frame whose handling raised "
                f"{type(e).__name__}; the connection stays up"
            )

    def _route_downstream_frame(
        self, name: str, managed: ManagedClient, frame: Any, now: float
    ) -> None:
        """Classify one frame and act on it. See `_dispatch_downstream_frame`.

        Each guard below exists because a later access depends on it, and each
        drops the frame with a value-free debug log, as a non-JSON line is
        dropped:

        - `frame.get` / `"method" in frame` / `frame["error"]` need a JSON
          object: `[]`, `42`, `"x"`, `null`, `true` all parse but are not one.
        - `msg_id in managed.pending_requests` needs a hashable id, and must not
          match one of our int ids by numeric equality: `True == 1` and
          `1.0 == 1` both hash equal, so a bool or float id would resolve
          request 1. JSON-RPC ids are strings, integers or null.
        - `set_result` / `set_exception` raise `InvalidStateError` on a future
          that is already settled (a caller cancelled it, and the response
          raced its `finally` pop).
        """
        if not isinstance(frame, dict):
            logger.debug(
                f"[{name}] dropped invalid frame: not a JSON object "
                f"({type(frame).__name__})"
            )
            return
        msg_id = frame.get("id")
        if msg_id is not None and (
            isinstance(msg_id, bool) or not isinstance(msg_id, (str, int))
        ):
            logger.debug(
                f"[{name}] dropped invalid frame: id of type {type(msg_id).__name__}"
            )
            return
        method = frame.get("method")
        # Classify by `method` FIRST (C-01). A frame carrying a `method` can
        # never resolve a pending future, so this both handles server->client
        # requests (method + id) and fixes a latent misrouting: a downstream
        # request whose id happens to collide with one of ours must not be
        # mistaken for that response.
        if isinstance(method, str):
            if msg_id is None:
                # Notification: no id, nothing to resolve.
                self._handle_downstream_notification(name, managed, method)
            else:
                # Server->client request: reply (ping -> {} else -32601).
                self._reply_to_downstream_request(name, managed, msg_id, method)
        elif "method" in frame:
            # A `method` that is present but not a string is not a valid
            # JSON-RPC request -- and it is not a response either, so it must
            # not fall through to the pending lookup, where an id colliding with
            # one of ours would resolve that future.
            logger.debug(
                f"[{name}] dropped invalid frame: non-string method "
                f"({type(method).__name__})"
            )
        elif msg_id is not None and msg_id in managed.pending_requests:
            pending = managed.pending_requests.pop(msg_id)

            # Track response time
            elapsed_ms = (now - pending.started_at) * 1000
            managed.response_times.append(elapsed_ms)
            if managed.response_times:
                managed.status.avg_response_time_ms = sum(managed.response_times) / len(
                    managed.response_times
                )

            # Update pending count
            managed.status.pending_request_count = len(managed.pending_requests)

            if pending.future.done():
                logger.debug(
                    f"[{name}] dropped a response for an already-settled request"
                )
            elif "error" in frame:
                pending.future.set_exception(_downstream_error(frame["error"]))
            else:
                pending.future.set_result(frame.get("result", {}))

    def _fail_oversized_line(
        self, name: str, managed: ManagedClient, limit: int
    ) -> None:
        """Drop an oversized stdout line: fail the in-flight request it most likely
        belongs to (the oldest pending) but keep the server connected.

        A huge single response (e.g. a browser page snapshot exceeding the read
        limit) used to disconnect the whole server and break the next call until
        reconnect (issue #79/1b). Instead we discard just that line and fail one
        request with an actionable message, leaving the connection and other
        pending requests intact.
        """
        msg = (
            f"Downstream response exceeded the {limit}-byte stdout line limit and "
            f"was dropped; the server stays connected. Raise PMCP_STDIO_READ_LIMIT "
            f"or reduce the tool's output size."
        )
        logger.warning(f"[{name}] {msg}")
        for req_id, pending in list(managed.pending_requests.items()):
            if not pending.future.done():
                pending.future.set_exception(Exception(msg))
            managed.pending_requests.pop(req_id, None)
            managed.status.pending_request_count = len(managed.pending_requests)
            break

    async def _read_stdout(self, name: str, managed: ManagedClient) -> None:
        """Read JSON-RPC messages from stdout.

        Reads in chunks and splits on newlines ourselves (rather than
        StreamReader.readline) so a single line larger than the read limit is
        dropped — failing only its request — instead of tearing down the whole
        server connection (issue #79/1b).
        """
        if not managed.process or not managed.process.stdout:
            return

        stream = managed.process.stdout
        limit = _stdio_read_limit()
        read_failure_reason: str | None = None
        buf = bytearray()
        # True while discarding the tail of an oversized line, staying byte-aligned
        # to the next newline so following messages are not corrupted.
        skipping = False
        try:
            while True:
                chunk = await stream.read(_STDIO_CHUNK_SIZE)
                if not chunk:
                    # EOF - server process has exited
                    break

                # UPDATE heartbeat on ANY output from server. This includes JSON
                # progress notifications (id: null) that don't resolve a request,
                # so per-request liveness drives the idle timeout in _send_request.
                now = self._clock()
                managed.status.last_activity_at = now
                for req in managed.pending_requests.values():
                    req.last_heartbeat = now

                buf.extend(chunk)
                while True:
                    nl = buf.find(b"\n")
                    if nl == -1:
                        # No complete line yet. If the in-progress line already
                        # exceeds the limit, drop it (fail its request) and skip to
                        # the next newline instead of disconnecting the server.
                        if len(buf) > limit:
                            if not skipping:
                                self._fail_oversized_line(name, managed, limit)
                                skipping = True
                            buf.clear()
                        break
                    raw = bytes(buf[:nl])
                    del buf[: nl + 1]
                    if skipping:
                        # This newline ends the oversized line we were discarding.
                        skipping = False
                        continue
                    self._handle_stdout_line(name, managed, raw, now)
        except Exception as e:
            read_failure_reason = f"stdout read error: {describe_exception(e)}"
            logger.warning(f"[{name}] {read_failure_reason}")
        finally:
            # Mark server as offline when stdout closes
            # Only warn if status was ONLINE (unexpected disconnect)
            # If status is already OFFLINE, it's a graceful shutdown
            if managed.status.status == ServerStatusEnum.ONLINE:
                detail = read_failure_reason or "process exited"
                logger.warning(f"Server {name} disconnected unexpectedly: {detail}")
                managed.status.status = ServerStatusEnum.ERROR
                managed.status.last_error = (
                    read_failure_reason or "Server process exited"
                )
                # Schedule auto-reconnect if we have the config (storm guard: only one task)
                if managed.config is not None and not managed.reconnecting:
                    managed.reconnecting = True
                    self._schedule_reconnect(name, managed.config)
            else:
                logger.debug(f"Server {name} disconnected (graceful shutdown)")
            # Cancel any pending requests
            for request_id, pending in list(managed.pending_requests.items()):
                if not pending.future.done():
                    pending.future.set_exception(
                        ConnectionError(f"Server {name} disconnected")
                    )
            managed.pending_requests.clear()
            managed.status.pending_request_count = 0

    def _schedule_reconnect(self, name: str, config: ResolvedServerConfig) -> None:
        """Schedule one reconnect task per server across client replacement."""
        task = self._reconnect_tasks.get(name)
        if task is not None and not task.done():
            return
        # The reconnect is requested now: capture the generation here, not per
        # attempt, so a disconnect cancelled after this point supersedes it.
        task = asyncio.create_task(
            self._reconnect_loop(name, config, ticket=self._lifecycle_seq),
            name=f"reconnect-{name}",
        )
        self._reconnect_tasks[name] = task
        self._reconnect_task_configs[task] = config
        self._track_background_task(task, name)

        def clear_reconnect(done: asyncio.Task[None]) -> None:
            if self._reconnect_tasks.get(name) is done:
                self._reconnect_tasks.pop(name, None)

        task.add_done_callback(clear_reconnect)

    async def _reconnect_loop(
        self,
        name: str,
        config: ResolvedServerConfig,
        ticket: int | None = None,
    ) -> None:
        """Attempt to reconnect a crashed server with exponential back-off.

        Tries up to 3 times with 5 s / 15 s / 30 s delays. Gives up if another
        caller has already brought the server back online, and on a refusal
        (`_ConnectRefused`): every later attempt carries the same ticket, so
        none would be admitted.

        `ticket` is the generation captured when the reconnect was scheduled
        (`_schedule_reconnect`); None means now.
        """
        token = _CONNECT_TICKET.set(
            _ConnectTicket(self, self._lifecycle_seq if ticket is None else ticket)
        )
        try:
            await self._reconnect_attempts(name, config)
        finally:
            _CONNECT_TICKET.reset(token)

    async def _reconnect_attempts(
        self, name: str, config: ResolvedServerConfig
    ) -> None:
        """`_reconnect_loop` inside its connect request."""
        delays = RECONNECT_DELAYS
        try:
            for attempt, delay in enumerate(delays, start=1):
                await asyncio.sleep(delay)
                # If someone else already reconnected (e.g. manual refresh), stop.
                managed = self._clients.get(name)
                if managed and managed.status.status == ServerStatusEnum.ONLINE:
                    logger.debug(
                        f"[{name}] already online; skipping reconnect attempt {attempt}"
                    )
                    return
                logger.info(f"[{name}] reconnect attempt {attempt}/{len(delays)} ...")
                try:
                    async with self._lifecycle_lock:
                        managed = self._clients.get(name)
                        if managed and managed.status.status == ServerStatusEnum.ONLINE:
                            logger.debug(
                                f"[{name}] already online; skipping reconnect attempt {attempt}"
                            )
                            return
                        # A reconnect is superseded only by a cancelled
                        # disconnect, which cancels it and settles for it
                        # (`_disconnect_server`); a refusal here is
                        # abandonment, which settles nothing.
                        await self._connect_singleflight(config)
                        final = self._settle_request(name, config=config, outcome=True)
                        if isinstance(final, _ConnectRefused):
                            raise final
                    logger.info(f"[{name}] reconnected successfully")
                    return
                except _ConnectRefused as e:
                    logger.info(f"[{name}] reconnect stopped: {describe_exception(e)}")
                    return
                except Exception as e:
                    safe_error = describe_exception(e)
                    logger.warning(
                        f"[{name}] reconnect attempt {attempt} failed: {safe_error}"
                    )
            logger.error(
                f"[{name}] all reconnect attempts failed; server remains offline"
            )
        finally:
            self._reconnect_tasks.pop(name, None)
            if managed := self._clients.get(name):
                managed.reconnecting = False

    async def _read_sse(
        self, name: str, managed: ManagedClient, read_stream: Any
    ) -> None:
        """Read JSON-RPC messages from an mcp read stream.

        Serves both remote transports: legacy SSE (`sse_client`) and
        streamable HTTP (`streamable_http_client`, including its server-pushed
        GET stream).
        """
        try:
            async for message in read_stream:
                # Any output counts as per-request liveness, including progress
                # notifications (id: null), so the idle timeout sees the keepalive.
                now = self._clock()
                managed.status.last_activity_at = now
                for req in managed.pending_requests.values():
                    req.last_heartbeat = now

                if isinstance(message, ValidationError):
                    # The mcp transports validate every incoming frame
                    # themselves (`jsonrpc_message_adapter.validate_json`) and,
                    # when that fails, put the pydantic `ValidationError` on the
                    # read stream in place of the message and keep reading
                    # (mcp/client/sse.py `sse_reader`, streamable_http.py
                    # `_handle_sse_event` with no originating request). It is
                    # one malformed frame -- non-JSON, not an object, a bad
                    # `id`/`method`/`params`/`result`/`error` -- not a dead
                    # transport, so drop it rather than end the loop. Only the
                    # type is logged: the error's text carries the frame's
                    # contents (`input_value=...`). Consiliency/pmcp#287.
                    logger.debug(
                        f"[{name}] dropped a downstream message that failed "
                        f"JSON-RPC validation ({type(message).__name__})"
                    )
                    continue
                if isinstance(message, Exception):
                    # Anything else on the stream is a transport failure
                    # (httpx/SSE errors, a broken stream): end the loop so the
                    # server goes to ERROR and reconnects.
                    raise message

                try:
                    # `mode="python"`, not "json": the SDK already parsed the
                    # frame (`validate_json`, which reads `NaN`, `Infinity` and
                    # `1e400` as floats), and a JSON-mode dump would turn those
                    # into `None` -- indistinguishable from a sent `null` (MCP's
                    # "unlimited" `ttl`). Python mode hands the dispatcher the
                    # same values the stdio path's `json.loads` does, so task
                    # normalisation sees them (Consiliency/pmcp#298).
                    payload = message.message.model_dump(
                        by_alias=True,
                        mode="python",
                        exclude_none=True,
                    )
                except Exception as e:
                    logger.debug(
                        f"[{name}] dropped undumpable frame ({type(e).__name__})"
                    )
                    continue
                # Same dispatcher as the stdio path, so the two cannot drift.
                # It never raises for a frame's shape, which matters more here
                # -- this loop's blanket `except Exception` would tear the
                # connection down and trigger a reconnect.
                self._dispatch_downstream_frame(name, managed, payload, now)
        except Exception as e:
            logger.debug(f"[{name}] SSE read error: {describe_exception(e)}")
        finally:
            if managed.status.status == ServerStatusEnum.ONLINE:
                logger.warning(f"Server {name} disconnected unexpectedly")
                managed.status.status = ServerStatusEnum.ERROR
                managed.status.last_error = "SSE connection closed"
                # Schedule auto-reconnect if we have the config (storm guard: only one task)
                if managed.config is not None and not managed.reconnecting:
                    managed.reconnecting = True
                    self._schedule_reconnect(name, managed.config)
            else:
                logger.debug(f"Server {name} disconnected (graceful shutdown)")

            for request_id, pending in list(managed.pending_requests.items()):
                if not pending.future.done():
                    pending.future.set_exception(
                        ConnectionError(f"Server {name} disconnected")
                    )
            managed.pending_requests.clear()
            managed.status.pending_request_count = 0

    async def _send_message_to_downstream(
        self, managed: ManagedClient, payload: dict[str, Any]
    ) -> None:
        """Write one fire-and-forget frame to a downstream server.

        The single guarded writer behind the bounded outbound path. Only for
        frames we originate and do not wait on (request replies,
        notifications/cancelled) -- `_send_request`'s own request write stays
        inline so its errors keep propagating to the caller. All write failures
        are logged and swallowed here (a dead pipe must not tear anything down),
        matching `_handle_downstream_notification`'s never-raises contract.
        """
        name = managed.config.name
        try:
            if managed.is_remote:
                if managed.write_stream is None:
                    return
                _encode_outbound_frame(payload)
                msg = mcp_types.jsonrpc_message_adapter.validate_python(payload)
                await managed.write_stream.send(SessionMessage(msg))
            else:
                if not managed.process or not managed.process.stdin:
                    return
                data = _encode_outbound_frame(payload) + "\n"
                managed.process.stdin.write(data.encode())
                await managed.process.stdin.drain()
        except Exception as e:
            logger.debug(
                f"[{name}] failed to write outbound frame "
                f"{payload.get('method') or payload.get('id')}: {describe_exception(e)}"
            )

    async def _teardown_outbound(
        self, managed: ManagedClient, *, timeout: float | None = None
    ) -> None:
        """Cancel a client's outbound writer and reset its outbound path.

        The postcondition every teardown path owes (Consiliency/pmcp#287): no
        live `_drain_outbound` task, and `managed.outbound` /
        `managed.outbound_writer` both None. `_cleanup_client` states it inline;
        `disconnect_server` and the handshake-failure paths of `_connect_stdio`
        and `_connect_remote_stream` share it here. Before this, a writer
        started during a failed handshake (a server->client `ping` answered
        before `initialize` completed) survived the except-path pop, because
        that path cancelled only the read/stderr tasks and -- deliberately --
        runs no server-name background-task sweep.

        The refs are dropped BEFORE the await, so a concurrent
        `_enqueue_outbound` during the wait lazily builds a fresh
        queue + writer rather than having its new writer's ref overwritten
        with None (which would orphan it). Never raises for a non-cancellation
        failure.
        """
        writer = managed.outbound_writer
        managed.outbound = None
        managed.outbound_writer = None
        # `_reap_child`: the writer's own cancellation is never seen; a
        # CancelledError out of here is the caller's and propagates
        # (Consiliency/pmcp#324).
        await _reap_child(writer, timeout=timeout)

    async def _drain_outbound(self, managed: ManagedClient) -> None:
        """The one writer task per client: drain the bounded outbound queue.

        Blocking on `queue.get()` and on a stalled sink is the point -- it bounds
        the whole path to `maxsize` queued plus one in-flight regardless of how
        hard a downstream floods us, using exactly one task.
        """
        queue = managed.outbound
        if queue is None:
            return
        while True:
            payload = await queue.get()
            await self._send_message_to_downstream(managed, payload)

    def _enqueue_outbound(
        self, name: str, managed: ManagedClient, frame: dict[str, Any]
    ) -> None:
        """Enqueue one fire-and-forget frame; create the queue/writer lazily.

        Sync and never raises: `create_task` is guarded (`except RuntimeError`
        for no running loop) and `put_nowait` overflow is caught and dropped, so
        callers (`_reply_to_downstream_request`,
        `_schedule_cancelled_notification`) can be called from a sync dispatch
        path and from a cancellation cleanup without any failure mode escaping.
        """
        if managed.is_remote and managed.write_stream is None:
            return
        if managed.outbound is None:
            managed.outbound = asyncio.Queue(maxsize=_OUTBOUND_QUEUE_MAXSIZE)
        if managed.outbound_writer is None or managed.outbound_writer.done():
            try:
                writer = asyncio.create_task(self._drain_outbound(managed))
            except RuntimeError:
                # No running loop: both real dispatch paths run inside one, so
                # this is unreachable in production. Drop rather than raise to
                # keep the never-raises guarantee.
                logger.debug(f"[{name}] no running loop; dropped outbound frame")
                return
            managed.outbound_writer = writer
            self._track_background_task(writer, name)
        try:
            managed.outbound.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning(
                f"[{name}] outbound queue full ({_OUTBOUND_QUEUE_MAXSIZE}); "
                "dropped a downstream reply/notification frame"
            )

    def _reply_to_downstream_request(
        self, name: str, managed: ManagedClient, msg_id: Any, method: str
    ) -> None:
        """Answer a server->client JSON-RPC request (C-01).

        We advertise no client capabilities (`"capabilities": {}` in
        `initialize`), so the only request we can honour is base-protocol `ping`
        (empty result). Any other method is refused with `-32601` rather than
        forwarded to the prompt-injectable agent -- a conscious trust-boundary
        refusal, not a gap.
        """
        if method == "ping":
            frame: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        else:
            frame = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": mcp_types.METHOD_NOT_FOUND,
                    "message": "Method not found",
                },
            }
        self._enqueue_outbound(name, managed, frame)

    def _schedule_cancelled_notification(
        self, managed: ManagedClient, request_id: int, method: str, reason: str
    ) -> None:
        """Send `notifications/cancelled` downstream for a request we abandoned (C-04).

        Enqueued, never awaited, so it can never skip a `finally` pop. The spec
        forbids cancelling `initialize`, so that method is silently skipped.
        """
        if method == "initialize":
            return
        frame = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": request_id, "reason": reason},
        }
        self._enqueue_outbound(managed.config.name, managed, frame)

    async def _send_request(
        self,
        managed: ManagedClient,
        method: str,
        params: dict[str, Any],
        tool_id: str = "",
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        request_id = self._next_request_id(managed.config.name)
        now = self._clock()

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        # Create PendingRequest with metadata for health monitoring
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=request_id,
            server_name=managed.config.name,
            tool_id=tool_id,
            started_at=now,
            last_heartbeat=now,
            timeout_ms=timeout_ms,
            future=future,
            method=method,
        )
        managed.pending_requests[request_id] = pending
        managed.status.pending_request_count = len(managed.pending_requests)

        # Wait for response with an inactivity (idle) timeout: the call survives
        # as long as the downstream keeps producing output (per-request
        # last_heartbeat), bounded by an absolute ceiling backstop.
        #
        # The generous absolute ceiling applies only to tool invocations, which
        # can legitimately run long (e.g. browser automation). Control-plane
        # requests (initialize, tools/list, resources/list, tasks/*) keep the
        # tighter idle deadline as their ceiling, so one chatty-but-stuck server
        # can't stall startup/refresh/connect_all for the full ceiling.
        idle_timeout_s = timeout_ms / 1000.0
        ceiling_s = (
            _request_ceiling_ms() / 1000.0 if method == "tools/call" else idle_timeout_s
        )
        try:
            # Send request. The write is INSIDE the try (C-02): a write error
            # (e.g. BrokenPipeError from drain()) is neither TimeoutError nor
            # CancelledError, so it propagates untouched -- and the `finally`
            # still pops the pending entry.
            if managed.is_remote:
                if managed.write_stream is None:
                    raise RuntimeError("Remote stream not connected")
                # mcp 2.0.0's JSONRPCMessage is a bare union (JSONRPCRequest |
                # JSONRPCNotification | JSONRPCResponse | JSONRPCError), not a
                # pydantic model, so it has no .model_validate(); construct via
                # its published TypeAdapter instead.
                _encode_outbound_frame(request)
                msg = mcp_types.jsonrpc_message_adapter.validate_python(request)
                await managed.write_stream.send(SessionMessage(msg))
            else:
                if not managed.process or not managed.process.stdin:
                    raise RuntimeError("Process not running")

                data = _encode_outbound_frame(request) + "\n"
                managed.process.stdin.write(data.encode())
                await managed.process.stdin.drain()

            result = await self._await_with_idle_timeout(
                managed,
                request_id,
                pending,
                future,
                idle_timeout_s=idle_timeout_s,
                ceiling_s=ceiling_s,
            )
            return result
        except asyncio.TimeoutError:
            # Idle or absolute-ceiling timeout: tell the downstream to stop work
            # on this id (C-04). The arm covers both, so the reason is neutral.
            self._schedule_cancelled_notification(
                managed, request_id, method, "timeout"
            )
            raise TimeoutError(f"Request {method} timed out")
        except asyncio.CancelledError:
            # The caller was cancelled while we were mid-flight (C-04). Only
            # notify when we still own the pending entry: `cancel_request` pops
            # BEFORE it cancels the future and sends its own notification, so
            # this guard dedupes the gateway.cancel path to exactly one frame.
            if request_id in managed.pending_requests:
                self._schedule_cancelled_notification(
                    managed, request_id, method, "caller cancelled"
                )
            raise
        finally:
            # C-02 invariant: the entry is popped on EVERY exit -- success (the
            # reader already popped; this is a no-op), timeout, cancellation, or
            # a mid-write error. Nothing above is awaited after this, so no
            # cancellation can skip it.
            managed.pending_requests.pop(request_id, None)
            managed.status.pending_request_count = len(managed.pending_requests)

    async def _await_with_idle_timeout(
        self,
        managed: ManagedClient,
        request_id: int,
        pending: PendingRequest,
        future: asyncio.Future[Any],
        idle_timeout_s: float,
        ceiling_s: float,
    ) -> Any:
        """Await ``future`` until it resolves, the downstream goes idle, or the
        absolute ceiling is hit.

        Waits in short slices so per-request liveness (``pending.last_heartbeat``,
        bumped by the stdout/SSE readers on any downstream output) can extend the
        deadline. ``asyncio.shield`` ensures a slice timeout never cancels the real
        future, so a response arriving mid-slice is returned rather than dropped.
        Raises ``asyncio.TimeoutError`` on idle/ceiling so the caller maps it to the
        usual ``TimeoutError``.

        The slice (``IDLE_POLL_SLICE_S``) is real event-loop time and only decides
        how often the check runs; what the check *sees* is ``self._clock``.
        """
        slice_s = min(idle_timeout_s, IDLE_POLL_SLICE_S)
        while True:
            try:
                return await bounded_wait(asyncio.shield(future), timeout=slice_s)
            except asyncio.TimeoutError:
                if future.done():
                    return future.result()
                now = self._clock()
                if now - pending.started_at >= ceiling_s:
                    logger.warning(
                        "[%s] request %d hit absolute ceiling (%.1fs)",
                        managed.config.name,
                        request_id,
                        ceiling_s,
                    )
                    raise
                if now - pending.last_heartbeat >= idle_timeout_s:
                    raise
                # Downstream is still active; keep waiting.

    async def _send_initialize(self, managed: ManagedClient) -> None:
        """Send initialize handshake."""
        params = {
            "protocolVersion": PREFERRED_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
        }
        try:
            result = await self._send_request(managed, "initialize", params)
            requested_protocol_version = PREFERRED_PROTOCOL_VERSION
        except Exception as exc:
            if not _is_protocol_version_initialize_error(exc):
                raise
            legacy_params = {**params, "protocolVersion": "2024-11-05"}
            result = await self._send_request(managed, "initialize", legacy_params)
            requested_protocol_version = "2024-11-05"

        protocol_version = result.get("protocolVersion")
        if isinstance(protocol_version, str):
            managed.status.protocol_version = protocol_version
            if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
                logger.debug(
                    "Server %s negotiated unrecognized protocol version %s",
                    managed.config.name,
                    protocol_version,
                )
        else:
            managed.status.protocol_version = requested_protocol_version

        capabilities = result.get("capabilities")
        if isinstance(capabilities, dict):
            managed.status.server_capabilities = capabilities

        # Send initialized notification (no response expected)
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        if managed.is_remote:
            if managed.write_stream is None:
                raise RuntimeError("Remote stream not connected")
            msg = mcp_types.jsonrpc_message_adapter.validate_python(notification)
            await managed.write_stream.send(SessionMessage(msg))
        elif managed.process and managed.process.stdin:
            data = _encode_outbound_frame(notification) + "\n"
            managed.process.stdin.write(data.encode())
            await managed.process.stdin.drain()

    async def disconnect_all(self) -> None:
        """Disconnect from all servers.

        A cancel while waiting for the lifecycle lock -- another operation
        holds it, and `GatewayServer.shutdown`'s budget runs out -- still
        abandons every client synchronously before it propagates
        (Consiliency/pmcp#324, implementation addition): the fallback inside
        `_disconnect_all_unlocked` is only reached once the lock is held."""
        try:
            await self._lifecycle_lock.acquire()
        except asyncio.CancelledError:
            self.abandon_all_now()
            raise
        try:
            await self._disconnect_all_unlocked()
        finally:
            self._lifecycle_lock.release()

    async def _disconnect_all_unlocked(self) -> None:
        """Disconnect from all servers while caller owns the lifecycle boundary."""
        # Stop health monitor if running
        self.stop_health_monitor()

        async def _shutdown_one(name: str, managed: ManagedClient) -> None:
            try:
                logger.info(f"Disconnecting from {name}")

                # Mark as disconnecting BEFORE canceling read task to avoid
                # false "disconnected unexpectedly" warnings
                managed.status.status = ServerStatusEnum.OFFLINE

                # Cancel pending requests first
                for request_id, pending in list(managed.pending_requests.items()):
                    if not pending.future.done():
                        pending.future.cancel()
                managed.pending_requests.clear()
                managed.status.pending_request_count = 0

                # Cancel read task
                await _reap_child(managed.read_task, timeout=1.0)

                # Close transport. _close_remote_transport itself never
                # swallows a genuine transport-exit failure; the swallow
                # belongs here -- this loop runs under `asyncio.gather`
                # across every connected client, and one client's teardown
                # failure must not abort the others' or the wholesale
                # shutdown budget (issue #79/1c).
                if managed.is_remote:
                    await self._close_remote_transport(name, managed)
                else:
                    await _terminate_process_tree(
                        managed.process, name, group_pgid=managed.group_pgid
                    )
            except asyncio.CancelledError:
                # disconnect_all was cancelled (e.g. the shutdown budget in
                # server.py ran out): kill and abandon now, without awaiting,
                # then re-raise; the gather re-raises it to disconnect_all's
                # caller (Consiliency/pmcp#324).
                self._abandon_client_io(name, managed)
                raise
            except Exception as e:
                logger.warning(
                    f"Error disconnecting from {name}: {describe_exception(e)}"
                )

        # Reap servers concurrently: each _terminate_process_tree can cost up to
        # ~8s for a hung stdio server, and disconnect_all() runs under a bounded
        # shutdown budget (server.py wraps it in bounded_wait). A sequential loop
        # would let two+ hung servers blow that budget and leave later groups
        # unsignalled — orphaning browsers (issue #79/1c) at shutdown. Concurrent
        # reaping makes total time ≈ the slowest single server.
        clients = list(self._clients.items())
        current = asyncio.current_task()
        exclude = {current} if current is not None else set()
        try:
            if clients:
                await asyncio.gather(
                    *(_shutdown_one(name, managed) for name, managed in clients),
                    return_exceptions=True,
                )
            await self._cancel_background_tasks(exclude=exclude)
        except asyncio.CancelledError:
            # Parent-level fallback (Consiliency/pmcp#324): a cancel that lands
            # before a `_shutdown_one` worker has run its first instruction
            # never reaches that worker's own handler, so kill and abandon
            # every client here, synchronously, whether or not its worker ran
            # (repeating it for one that did is harmless), then drop the
            # registries and re-raise.
            for name, managed in clients:
                self._abandon_client_io(name, managed)
            self._cancel_background_tasks_now()
            raise
        finally:
            # The registry clears are synchronous, so the cancelled path runs
            # them too; they stay in this method, which the publisher-coverage
            # AST guard (tests/runtime/test_publisher_coverage.py) requires.
            self._connect_tasks.clear()
            self._reconnect_tasks.clear()
            self._reconcile_tasks.clear()
            self._reconcile_reruns.clear()
            self._catalog_suppressed.clear()
            self._clients.clear()
            # Capture non-empty immediately before each clear (not after — the
            # clear must happen first for the dict to actually be empty
            # afterward, but the check must be the pre-clear state) so a
            # wholesale teardown still announces what it emptied. Without this,
            # refresh([]) (disconnect-all + no reconnect) empties every catalog
            # and publishes nothing — the listener-with-no-publishers failure
            # this phase exists to prevent. Deliberately NOT routed through
            # _remove_server_indexes: that method is per-server-name, this is a
            # wholesale clear, and rewriting it as a loop over names would
            # change shutdown semantics for no benefit.
            had_tools = bool(self._tools)
            had_resources = bool(self._resources)
            had_prompts = bool(self._prompts)
            self._tools.clear()
            self._resources.clear()
            self._prompts.clear()
            self._tasks.clear()
            self._servers.clear()
            self._lazy_configs.clear()
            if had_tools:
                self._catalog_events.note_tools_changed()
            if had_resources:
                self._catalog_events.note_resources_changed()
            if had_prompts:
                self._catalog_events.note_prompts_changed()

    async def _cleanup_client(self, name: str, managed: ManagedClient) -> None:
        """Cancel a client's read task, kill its process, and remove it from registries.

        Safe to call on any managed client regardless of state. A remote close
        failure is logged and suppressed; a terminate failure propagates after
        the registries are cleared. A cancellation of the caller finishes the
        teardown synchronously (`_abandon_client_io`) and then propagates
        (Consiliency/pmcp#324).

        Cancels only *this* client's own read/stderr tasks — not every background
        task scoped to the server name. A reconnect runs its connect inside a task
        that is itself scoped to the name; a server-name-wide cancel here would
        cancel the in-flight reconnect (cascading into the running connect task)
        and abort the very recovery that called us.
        """
        # Include `outbound_writer`: a fixed tuple was cancelled here, but the
        # `while True` writer is not a background-task sweep target on this path
        # (`_cleanup_client` deliberately does NOT call `_cancel_background_tasks`),
        # so without this it leaked one writer task per reconnect generation.
        try:
            await self._cleanup_client_io(name, managed)
        except asyncio.CancelledError:
            self._abandon_client_io(name, managed)
            raise
        finally:
            self._forget_client(name)

    def _forget_client(self, name: str) -> None:
        self._clients.pop(name, None)
        self._servers.pop(name, None)
        self._remove_server_indexes(name)

    async def _cleanup_client_io(self, name: str, managed: ManagedClient) -> None:
        """`_cleanup_client`'s graceful teardown; a caller cancellation
        propagates out of any await here."""
        for task in (managed.read_task, managed.stderr_task, managed.outbound_writer):
            await _reap_child(task)
        # Reset the outbound path so nothing survives onto a next generation.
        # The writer was cancelled above, but the Queue -- and any reply /
        # notifications/cancelled frames the dead connection left buffered,
        # keyed to request ids that no longer exist -- would otherwise stay on
        # this object. Dropping it means `_enqueue_outbound` lazily rebuilds a
        # fresh queue + writer, so a reused client can never drain a dead
        # connection's frames into a new downstream process, and stale frames
        # never occupy the bounded cap against the new connection's traffic.
        # (Reconnect today allocates a fresh ManagedClient, so this hardens
        # `_cleanup_client`'s postcondition rather than fixing an active bug --
        # but the guarantee should not depend on that distant invariant, on a
        # boundary where the peer is untrusted.) No `await` between the cancel
        # above and this reset, so it cannot race a concurrent recreate.
        managed.outbound = None
        managed.outbound_writer = None
        if managed.is_remote:
            # Previously a no-op for remote clients: this function closed no
            # transport at all here, so a reconnect (the only caller that hits
            # this branch) leaked the SSE/streamable-HTTP transport — and, since
            # IF-0-P2-2, would also leak the owned httpx2.AsyncClient. Guarded
            # the same way as the two explicit-disconnect close sites
            # (`disconnect_server`, `_disconnect_all_unlocked`), but this
            # function's contract is "never raises" (for non-cancellation
            # failures -- `_close_remote_transport` itself never swallows, so
            # an unmatched error is logged and swallowed here instead).
            try:
                await self._close_remote_transport(name, managed)
            except Exception as e:
                logger.warning(
                    f"[{name}] Error closing remote transport: {describe_exception(e)}"
                )
        else:
            await _terminate_process_tree(
                managed.process, name, group_pgid=managed.group_pgid
            )

    async def refresh(self, configs: list[ResolvedServerConfig]) -> list[str]:
        """Refresh connections (disconnect + reconnect).

        The reconnect is requested when `refresh` is called, so a
        `disconnect_server(name)` cancelled while this waits for the lock or
        tears down supersedes its connect of `name` (Consiliency/pmcp#324)."""
        with self._connect_request():
            async with self._lifecycle_lock:
                await self._disconnect_all_unlocked()
                return await self._connect_all_unlocked(configs)

    async def adopt_process(
        self,
        name: str,
        process: asyncio.subprocess.Process,
        config: ResolvedServerConfig,
    ) -> None:
        """Adopt an already-running subprocess as a managed MCP client.

        Used when npx-based servers start during installation.
        The process must have stdin/stdout pipes available.

        Args:
            name: Server name
            process: Running subprocess with stdin/stdout pipes
            config: Server configuration

        Raises:
            _ManagerAbandoned: If `abandon_all_now()` ran. Nothing is
                registered and the process is left as passed: the caller
                spawned it and owns its cleanup (`_finalize_server_ready`
                kills it on any handoff failure).
            RuntimeError: If process is not running or missing pipes
            Exception: If MCP initialization fails
        """
        # The same check as every connect path (Consiliency/pmcp#324), with
        # no await between it and the registration below. An adoption is
        # requested by this call and carries no earlier ticket, so only
        # abandonment can refuse it.
        self._admit_connect(name)
        # Validate process state
        if process.returncode is not None:
            raise RuntimeError(f"Process for {name} has already exited")
        if not process.stdin:
            raise RuntimeError(f"Process for {name} has no stdin pipe")
        if not process.stdout:
            raise RuntimeError(f"Process for {name} has no stdout pipe")

        logger.info(f"Adopting process for MCP server: {name}")

        # #175 item 2. Every other path into the indexers clears this server's
        # entries first. The two this one is modelled on are the connect and
        # cleanup paths -- `_connect_stdio`, `_connect_remote_stream` and
        # `_cleanup_client` -- which remove unconditionally, up front, exactly
        # as here. `_reconcile_once` also removes before it indexes, but it is
        # not the precedent for this line and should not be read as one: it
        # fetches first and removes inside a synchronous apply block, per kind,
        # only for the kinds whose re-listing actually succeeded. That is a
        # different rule for a different situation (a live server whose prior
        # catalog must survive a failed listing), and copying it here would be
        # wrong.
        #
        # Without this, adopting a server that had been indexed under the same
        # name left the previous listing's entries in the catalog beside the
        # new one: tools the adopted process does not serve, still routable,
        # until something else removed them. Uniform beats an exception
        # documented in two places. Deliberately no line numbers -- the ones
        # this comment first carried were stale within a single change.
        self._remove_server_indexes(name)

        # Initialize status
        status = ServerStatus(
            name=name,
            status=ServerStatusEnum.CONNECTING,
            tool_count=0,
        )
        self._servers[name] = status

        managed = ManagedClient(
            config=config,
            process=process,
            status=status,
            # Not spawned here, so there is no spawn contract: read the group
            # now; `None` unless the process leads its own (it was validated
            # as running above).
            group_pgid=_own_group_pgid(process),
        )
        self._clients[name] = managed

        # Start reading stderr in background (if available)
        if process.stderr:
            managed.stderr_task = self._track_background_task(
                asyncio.create_task(self._read_stderr(name, process.stderr)),
                name,
            )

        try:
            # Start reading stdout for JSON-RPC responses
            managed.read_task = self._track_background_task(
                asyncio.create_task(self._read_stdout(name, managed)),
                name,
            )

            # Initialize MCP connection
            await self._send_initialize(managed)

            indexed, resource_count, prompt_count = await self._index_capabilities(
                managed
            )

            # Update status
            status.status = ServerStatusEnum.ONLINE
            status.tool_count = indexed
            status.resource_count = resource_count
            status.prompt_count = prompt_count
            status.last_connected_at = time.time()

            # Update revision
            self._revision_id = _generate_revision_id()
            self._last_refresh_ts = time.time()

            logger.info(f"Adopted {name}: {indexed} tools indexed")

        except asyncio.CancelledError as e:
            # As in `_connect_stdio` (Consiliency/pmcp#324).
            status.status = ServerStatusEnum.ERROR
            status.last_error = _handshake_error(e)
            self._abandon_client_io(name, managed)
            self._forget_client(name)
            raise
        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = describe_exception(e)
            await self._cleanup_client(name, managed)
            raise

    async def call_tool(
        self,
        tool_id: str,
        args: dict[str, Any],
        timeout_ms: int = 30000,
        *,
        task: TaskMetadataInput | dict[str, Any] | None = None,
        trace_context: TraceContextInfo | dict[str, Any] | None = None,
    ) -> Any:
        """Call a tool on a downstream server."""
        tool_info = self._tools.get(tool_id)
        if not tool_info:
            raise ValueError(f"Unknown tool: {tool_id}")

        managed = self._clients.get(tool_info.server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {tool_info.server_name} is not connected")

        if managed.status.status != ServerStatusEnum.ONLINE:
            raise RuntimeError(
                f"Server {tool_info.server_name} is {managed.status.status.value}"
            )

        support = self._tool_task_support(tool_info)
        task_requested = task is not None
        if support == "required":
            task_requested = True
        if task_requested and support == "forbidden":
            raise RuntimeError(f"Tool {tool_id} does not support MCP task execution")
        if task_requested and not self._server_supports_tasks(managed):
            raise RuntimeError(
                f"Server {tool_info.server_name} does not advertise MCP task support"
            )

        params: dict[str, Any] = {"name": tool_info.tool_name, "arguments": args}
        trace_meta = _trace_context_payload(trace_context)
        if trace_meta:
            params["_meta"] = {**params.get("_meta", {}), **trace_meta}
        requestor_context: dict[str, Any] | None = None
        if task_requested:
            parsed_task = (
                task
                if isinstance(task, TaskMetadataInput)
                else TaskMetadataInput.model_validate(task or {})
            )
            if not parsed_task.enabled and support != "required":
                task_requested = False
            else:
                params["task"] = self._task_wire_metadata(parsed_task)
                requestor_context = parsed_task.requestor_context

        # Send tool call with metadata for health monitoring
        result = await self._send_request(
            managed,
            "tools/call",
            params,
            tool_id=tool_id,
            timeout_ms=timeout_ms,
        )
        if task_requested and isinstance(result, dict):
            task_payload = self._extract_task_payload(result)
            if task_payload is not None:
                task_info = self._task_info_from_payload(task_payload)
                if task_info is not None:
                    self._record_task(
                        tool_info.server_name,
                        task_info,
                        tool_id=tool_id,
                        requestor_context=requestor_context,
                    )

        return result

    def _task_client(self, server_name: str) -> ManagedClient:
        managed = self._clients.get(server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {server_name} is not connected")
        if not self._server_supports_tasks(managed):
            raise RuntimeError(
                f"Server {server_name} does not advertise MCP task support"
            )
        return managed

    async def list_tasks(
        self,
        server_name: str | None = None,
        cursor: str | None = None,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Proxy downstream tasks/list and update the transient task registry."""
        servers = [server_name] if server_name else sorted(self._clients)
        all_tasks: list[dict[str, Any]] = []
        next_cursor: str | None = None
        for name in servers:
            managed = self._task_client(name)
            params = self._task_request_params(
                cursor=cursor,
                requestor_context=requestor_context,
            )
            result = await self._send_request(managed, "tasks/list", params)
            for payload in result.get("tasks", []):
                if not isinstance(payload, dict):
                    continue
                task_info = self._task_info_from_payload(payload)
                if task_info is None:
                    continue
                record = self._record_task(name, task_info)
                all_tasks.append(record.model_dump())
            next_cursor = result.get("nextCursor") or result.get("next_cursor")
        return {"tasks": all_tasks, "nextCursor": next_cursor}

    async def get_task(
        self,
        server_name: str,
        task_id: str,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> McpTaskInfo:
        """Proxy downstream tasks/get and update the transient task registry."""
        managed = self._task_client(server_name)
        record = self.get_task_record(server_name, task_id)
        result = await self._send_request(
            managed,
            "tasks/get",
            self._task_request_params(
                task_id=task_id,
                requestor_context=requestor_context
                or (record.requestor_context if record is not None else None),
            ),
        )
        payload = self._extract_task_payload(result) or result
        task_info = self._task_info_from_payload(payload)
        if task_info is None:
            raise KeyError(f"Task not found: {server_name}::{task_id}")
        return self._record_task(server_name, task_info)

    async def get_task_result(
        self,
        server_name: str,
        task_id: str,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Proxy downstream tasks/result and update task metadata when returned."""
        managed = self._task_client(server_name)
        record = self.get_task_record(server_name, task_id)
        result = await self._send_request(
            managed,
            "tasks/result",
            self._task_request_params(
                task_id=task_id,
                requestor_context=requestor_context
                or (record.requestor_context if record is not None else None),
            ),
        )
        task_payload = self._extract_task_payload(result)
        if task_payload is not None:
            task_info = self._task_info_from_payload(task_payload)
            if task_info is not None:
                self._record_task(server_name, task_info)
        else:
            await self.get_task(
                server_name,
                task_id,
                requestor_context=requestor_context
                or (record.requestor_context if record is not None else None),
            )
        return result

    async def cancel_task(
        self,
        server_name: str,
        task_id: str,
        force: bool = False,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> tuple[bool, McpTaskInfo | None, str]:
        """Proxy downstream tasks/cancel with idempotent local terminal handling."""
        record = self.get_task_record(server_name, task_id)
        if record is not None and self._terminal_task(record):
            return (True, record, f"Task is already terminal: {record.status}")
        if record is None:
            return (False, None, f"Task not found: {server_name}::{task_id}")

        managed = self._task_client(server_name)
        params = self._task_request_params(
            task_id=task_id,
            requestor_context=requestor_context or record.requestor_context,
        )
        params["force"] = force
        result = await self._send_request(managed, "tasks/cancel", params)
        payload = self._extract_task_payload(result) or result
        task_info = self._task_info_from_payload(payload)
        if task_info is None:
            task_info = McpTaskInfo(
                task_id=task_id,
                status="cancelled",
                updated_at=time.time(),
                raw=result,
            )
        return (True, self._record_task(server_name, task_info), "Task cancelled")

    async def read_resource(self, resource_id: str, timeout_ms: int = 30000) -> Any:
        """Read a resource from a downstream server."""
        resource_info = self._resources.get(resource_id)
        if not resource_info:
            raise ValueError(f"Unknown resource: {resource_id}")

        managed = self._clients.get(resource_info.server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {resource_info.server_name} is not connected")

        if managed.status.status != ServerStatusEnum.ONLINE:
            raise RuntimeError(
                f"Server {resource_info.server_name} is {managed.status.status.value}"
            )

        result = await self._send_request(
            managed,
            "resources/read",
            {"uri": resource_info.uri},
            timeout_ms=timeout_ms,
        )

        return result

    async def get_prompt(
        self,
        prompt_id: str,
        arguments: dict[str, str] | None = None,
        timeout_ms: int = 30000,
    ) -> Any:
        """Get a prompt from a downstream server."""
        prompt_info = self._prompts.get(prompt_id)
        if not prompt_info:
            raise ValueError(f"Unknown prompt: {prompt_id}")

        managed = self._clients.get(prompt_info.server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {prompt_info.server_name} is not connected")

        if managed.status.status != ServerStatusEnum.ONLINE:
            raise RuntimeError(
                f"Server {prompt_info.server_name} is {managed.status.status.value}"
            )

        params: dict[str, Any] = {"name": prompt_info.name}
        if arguments:
            params["arguments"] = arguments

        result = await self._send_request(
            managed,
            "prompts/get",
            params,
            timeout_ms=timeout_ms,
        )

        return result

    def get_tool(self, tool_id: str) -> ToolInfo | None:
        """Get tool info by ID."""
        return self._tools.get(tool_id)

    def get_all_tools(self) -> list[ToolInfo]:
        """Get all tools."""
        return sorted(self._tools.values(), key=lambda tool: tool.tool_id)

    def get_resource(self, resource_id: str) -> ResourceInfo | None:
        """Get resource info by ID."""
        return self._resources.get(resource_id)

    def get_all_resources(self) -> list[ResourceInfo]:
        """Get all resources."""
        return sorted(
            self._resources.values(), key=lambda resource: resource.resource_id
        )

    def get_prompt_info(self, prompt_id: str) -> PromptInfo | None:
        """Get prompt info by ID."""
        return self._prompts.get(prompt_id)

    def get_all_prompts(self) -> list[PromptInfo]:
        """Get all prompts."""
        return sorted(self._prompts.values(), key=lambda prompt: prompt.prompt_id)

    def get_server_status(self, name: str) -> ServerStatus | None:
        """Get server status."""
        return self._servers.get(name)

    def get_all_server_statuses(self) -> list[ServerStatus]:
        """Get all server statuses."""
        return sorted(self._servers.values(), key=lambda status: status.name)

    def get_connected_configs(self) -> dict[str, ResolvedServerConfig]:
        """Return resolved configs for currently-connected servers, keyed by name.

        Used by gateway.refresh to diff the running set against a freshly
        resolved config set so unchanged servers are left running.
        """
        return {name: managed.config for name, managed in self._clients.items()}

    def get_connected_resolved_headers(self, name: str) -> dict[str, str] | None:
        """Return the remote auth headers a connected server was actually
        connected with (placeholders resolved at connect time), or None.

        Used by gateway.refresh to detect token rotation in the env store: the
        raw config keeps the same ``${VAR}`` placeholder, so only comparing the
        connect-time resolved value against a freshly-resolved value reveals the
        change.
        """
        managed = self._clients.get(name)
        return managed.resolved_remote_headers if managed is not None else None

    def get_registry_meta(self) -> tuple[str, float]:
        """Get registry metadata (revision_id, last_refresh_ts)."""
        return (self._revision_id, self._last_refresh_ts)

    def is_server_online(self, name: str) -> bool:
        """Check if server is online."""
        status = self._servers.get(name)
        return status is not None and status.status == ServerStatusEnum.ONLINE

    # === Health Monitoring Methods ===

    def start_health_monitor(self) -> None:
        """Start the background health monitoring task."""
        if not hasattr(self, "_health_task") or self._health_task is None:
            self._health_task: asyncio.Task[None] | None = self._track_background_task(
                asyncio.create_task(self._health_monitor_loop())
            )
            logger.info("Started health monitor background task")

    def stop_health_monitor(self) -> None:
        """Stop the health monitoring task."""
        if hasattr(self, "_health_task") and self._health_task:
            self._health_task.cancel()
            self._health_task = None
            logger.debug("Stopped health monitor background task")

    async def _health_monitor_loop(self) -> None:
        """Background task to monitor server and request health."""
        last_memory_log = 0.0
        while True:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                now = self._clock()

                # Periodic memory logging
                if now - last_memory_log >= MEMORY_LOG_INTERVAL:
                    proc_mem = _get_memory_usage_mb()
                    sys_mem_pct = _get_system_memory_pct()
                    server_count = len(self._clients)

                    # Count child processes
                    child_count = 0
                    for managed in self._clients.values():
                        if managed.process and managed.process.returncode is None:
                            child_count += 1

                    log_msg = (
                        f"[TELEMETRY] pmcp: {proc_mem:.1f}MB | "
                        f"system: {sys_mem_pct}% | "
                        f"servers: {server_count} ({child_count} alive)"
                    )

                    if proc_mem > MEMORY_WARN_THRESHOLD_MB:
                        logger.warning(f"{log_msg} - HIGH MEMORY")
                    elif sys_mem_pct > 80:
                        logger.warning(f"{log_msg} - SYSTEM MEMORY HIGH")
                    else:
                        logger.info(log_msg)

                    last_memory_log = now

                for name, managed in self._clients.items():
                    if not self._check_server_health(name, managed):
                        continue

                    # Check for stalled requests
                    for req_id, pending in list(managed.pending_requests.items()):
                        elapsed_since_heartbeat = now - pending.last_heartbeat

                        if elapsed_since_heartbeat > HEARTBEAT_STALL_THRESHOLD:
                            logger.warning(
                                f"Request {name}::{req_id} stalled "
                                f"(no heartbeat for {elapsed_since_heartbeat:.0f}s)"
                            )
                        elif elapsed_since_heartbeat > HEARTBEAT_WARN_THRESHOLD:
                            logger.info(
                                f"Request {name}::{req_id} slow "
                                f"(no heartbeat for {elapsed_since_heartbeat:.0f}s)"
                            )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Health monitor error: {describe_exception(e)}")

    def _check_server_health(self, name: str, managed: ManagedClient) -> bool:
        """Check server transport health, preserving status error strings."""
        if managed.is_remote:
            if managed.read_task and managed.read_task.done():
                if managed.status.status != ServerStatusEnum.ERROR:
                    logger.warning(f"Server {name} remote stream disconnected")
                    managed.status.status = ServerStatusEnum.ERROR
                    managed.status.last_error = "Remote stream disconnected"
                return False

            if managed.write_stream is None:
                if managed.status.status != ServerStatusEnum.ERROR:
                    managed.status.status = ServerStatusEnum.ERROR
                    managed.status.last_error = "Remote stream unavailable"
                return False

            return True

        if managed.process:
            returncode = managed.process.returncode
            if returncode is not None:
                logger.warning(f"Server {name} process exited with code {returncode}")
                managed.status.status = ServerStatusEnum.ERROR
                managed.status.last_error = f"Process exited: {returncode}"
                return False

        return True

    def get_pending_requests(self, server: str | None = None) -> list[PendingRequest]:
        """Get all pending requests, optionally filtered by server."""
        result: list[PendingRequest] = []
        for name, managed in sorted(self._clients.items()):
            if server and name != server:
                continue
            result.extend(list(managed.pending_requests.values()))
        return sorted(
            result, key=lambda pending: (pending.server_name, pending.request_id)
        )

    def cancel_all_pending_requests(self) -> int:
        """Cancel all pending requests and return the number newly cancelled."""
        cancelled = 0
        for _, managed in list(self._clients.items()):
            for request_id, pending in list(managed.pending_requests.items()):
                if not pending.future.done():
                    pending.future.cancel()
                    cancelled += 1
                managed.pending_requests.pop(request_id, None)
            managed.status.pending_request_count = len(managed.pending_requests)
        if cancelled:
            logger.warning(f"Force-cancelled {cancelled} pending requests")
        return cancelled

    def get_request_state(self, pending: PendingRequest) -> RequestState:
        """Determine current state of a pending request."""
        now = self._clock()
        elapsed = now - pending.started_at
        heartbeat_age = now - pending.last_heartbeat

        if pending.future.done():
            if pending.future.cancelled():
                return RequestState.CANCELLED
            return RequestState.COMPLETED
        if elapsed * 1000 > pending.timeout_ms:
            return RequestState.TIMEOUT
        if heartbeat_age > HEARTBEAT_STALL_THRESHOLD:
            return RequestState.STALLED
        if heartbeat_age > HEARTBEAT_WARN_THRESHOLD:
            return RequestState.ACTIVE  # Still active but slow
        return RequestState.PENDING

    async def cancel_request(
        self, request_id: str, force: bool = False
    ) -> tuple[str, str, bool, float | None]:
        """
        Cancel a pending request.

        Args:
            request_id: Format "server_name::local_id"
            force: Force cancel even if heartbeat is recent

        Returns:
            (status, message, was_stalled, elapsed_seconds)
            - status: "cancelled", "not_found", "already_complete", "refused"
        """
        # Parse request_id format "server_name::local_id"
        if "::" not in request_id:
            return (
                "not_found",
                f"Invalid request_id format: {request_id}",
                False,
                None,
            )

        server_name, local_id_str = request_id.rsplit("::", 1)
        try:
            local_id = int(local_id_str)
        except ValueError:
            return ("not_found", f"Invalid local_id: {local_id_str}", False, None)

        managed = self._clients.get(server_name)
        if not managed:
            return ("not_found", f"Server not found: {server_name}", False, None)

        pending = managed.pending_requests.get(local_id)
        if not pending:
            return ("not_found", f"Request not found: {request_id}", False, None)

        if pending.future.done():
            return ("already_complete", "Request already completed", False, None)

        now = self._clock()
        elapsed = now - pending.started_at
        heartbeat_age = now - pending.last_heartbeat
        was_stalled = heartbeat_age > HEARTBEAT_STALL_THRESHOLD

        # Safety check: refuse to cancel healthy long-running requests unless forced
        if not force and not was_stalled and elapsed < pending.timeout_ms / 1000:
            return (
                "refused",
                f"Request is healthy (heartbeat {heartbeat_age:.0f}s ago). "
                f"Use force=true to cancel anyway.",
                False,
                elapsed,
            )

        # Cancel the request. Pop BEFORE cancelling the future (C-04 dedup): the
        # future.cancel() propagates a CancelledError into `_send_request`, and
        # its `except CancelledError` only notifies when the id is still pending.
        # Removing it first makes THIS the single site that notifies downstream.
        managed.pending_requests.pop(local_id, None)
        managed.status.pending_request_count = len(managed.pending_requests)
        pending.future.cancel()
        self._schedule_cancelled_notification(
            managed, local_id, pending.method, "cancelled via gateway.cancel"
        )
        logger.info(
            f"Cancelled request {request_id} (stalled={was_stalled}, elapsed={elapsed:.1f}s)"
        )

        return ("cancelled", "Request cancelled successfully", was_stalled, elapsed)
