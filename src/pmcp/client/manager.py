"""MCP Client Manager - Manages connections to downstream MCP servers."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import logging
import os
from pathlib import Path
import random
import re
import signal
import string
import time
from collections import deque
from collections.abc import Collection
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, TypeVar

import httpx2
import mcp.types as mcp_types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage

from pmcp.auth import sanitize_auth_diagnostic
from pmcp.config.loader import make_tool_id
from pmcp.env_store import sanitized_subprocess_env
from pmcp.remote_auth import (
    MissingRemoteHeaderAuthError,
    resolve_remote_headers_for_tenant,
)
from pmcp.subscriptions import CatalogEventSink
from pmcp.types import (
    LocalMcpServerConfig,
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

resource_module: ModuleType | None

try:
    import resource as resource_module

    HAS_RESOURCE = True
except ImportError:
    resource_module = None
    HAS_RESOURCE = False

logger = logging.getLogger(__name__)
_TaskT = TypeVar("_TaskT", bound=asyncio.Task[Any])

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
# Sized against the thing that consumes the result: `max_tools_per_server`
# defaults to 100, and the MCP page size servers actually use is tens of
# entries, so 50 pages clears any honest catalog by a wide margin while still
# bounding a server that never stops paginating. Exceeding it makes the kind
# UNREADABLE, not partial -- indexing a truncated view is what #173 was about.
_MAX_LISTING_PAGES = 50


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


async def _terminate_process_tree(
    process: asyncio.subprocess.Process | None, name: str
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
        return

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
    group_pgid: int | None = None
    pid = process.pid
    if isinstance(pid, int) and hasattr(os, "getpgid") and hasattr(os, "killpg"):
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
        await asyncio.wait_for(process.wait(), timeout=5.0)
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
            await asyncio.wait_for(process.wait(), timeout=3.0)
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


# Heartbeat thresholds for health monitoring
HEARTBEAT_WARN_THRESHOLD = 60.0  # Warn if no activity for 60s
HEARTBEAT_STALL_THRESHOLD = 120.0  # Mark as stalled after 120s
HEALTH_CHECK_INTERVAL = 30.0  # Background health check every 30s

# Connection retry settings
MAX_CONNECTION_RETRIES = 3
RETRY_DELAYS = [1.0, 2.0, 4.0]  # Exponential backoff delays in seconds
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
        logger.debug(f"memory usage parse error: {e}")
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
        logger.debug(f"system memory check error: {e}")
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
            logger.warning(f"Server {name} has more than {limit} tools, truncating")
            break

        try:
            tool_name = _required_identity(tool, "name")
            tool_id = make_tool_id(name, tool_name)
            description = tool.get("description", "")
            input_schema = tool.get("inputSchema", {})
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
                f"[{name}] Skipping unparseable tool {_entry_label(tool)}: {e}"
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
                f"{_entry_label(resource, key='uri')}: {e}"
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
                f"[{name}] Skipping unparseable prompt {_entry_label(prompt)}: {e}"
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
    started_at: float  # time.time() when request started
    last_heartbeat: float  # time.time() of last activity
    timeout_ms: int  # Configured timeout
    future: asyncio.Future[Any]
    task_id: str | None = None
    task_status: str | None = None


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


class ClientManager:
    """Manages connections to downstream MCP servers."""

    def __init__(
        self,
        max_tools_per_server: int = 100,
        max_concurrent_spawns: int = 8,
        project_root: Path | None = None,
        *,
        catalog_events: CatalogEventSink | None = None,
    ) -> None:
        self._catalog_events: CatalogEventSink = (
            catalog_events or _NullCatalogEventSink()
        )
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
        self._connect_tasks: dict[str, asyncio.Task[None]] = {}
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

        async with self._lifecycle_lock:
            return await self._connect_all_unlocked(configs, retry=retry)

    async def _connect_all_unlocked(
        self, configs: list[ResolvedServerConfig], retry: bool = True
    ) -> list[str]:
        """Connect to all configured servers while caller owns lifecycle lock."""
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
                tasks_by_name[config.name] = task
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect errors from failed connections
        errors: list[str] = []
        for config, result in zip(configs, results):
            if isinstance(result, Exception):
                error_msg = f"Failed to connect to {config.name}: {result}"
                logger.error(error_msg)
                errors.append(error_msg)

        self._revision_id = _generate_revision_id()
        self._last_refresh_ts = time.time()

        return errors

    async def _connect_singleflight(
        self, config: ResolvedServerConfig, retry: bool = True
    ) -> None:
        """Share concurrent connection attempts for the same server name."""
        name = config.name
        status = self._servers.get(name)
        if status is not None and status.status == ServerStatusEnum.ONLINE:
            return

        task = self._connect_tasks.get(name)
        if task is None:
            if retry:
                task = asyncio.create_task(self._connect_with_retry(config))
            else:
                task = asyncio.create_task(self._connect_server(config))
            task = self._track_background_task(task, name)
            self._connect_tasks[name] = task

        try:
            await task
        finally:
            if self._connect_tasks.get(name) is task:
                self._connect_tasks.pop(name, None)

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
        exclude = set(exclude) if exclude else set()
        current = asyncio.current_task()
        if current is not None:
            exclude.add(current)
        tasks = [
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
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.difference_update(task for task in tasks if task.done())
        for task in tasks:
            if task.done():
                self._background_task_servers.pop(task, None)

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
        async with self._lifecycle_lock:
            if self.is_server_online(server_name):
                return True

            if server_name not in self._lazy_configs:
                if server_name not in self._servers:
                    raise ValueError(f"Unknown server: {server_name}")
                return False

            config = self._lazy_configs[server_name]
            logger.info(f"Lazy-starting server: {server_name}")
            task = self._connect_tasks.get(server_name)
            if task is None:
                task = self._track_background_task(
                    asyncio.create_task(self._connect_with_lifecycle_lock(config)),
                    server_name,
                )
                self._connect_tasks[server_name] = task

        try:
            await task
            async with self._lifecycle_lock:
                self._lazy_configs.pop(server_name, None)
            return True
        except Exception as e:
            logger.error(f"Failed to lazy-start {server_name}: {e}")
            async with self._lifecycle_lock:
                if server_name in self._servers:
                    self._servers[server_name].status = ServerStatusEnum.ERROR
                    self._servers[server_name].last_error = str(e)
            return False
        finally:
            async with self._lifecycle_lock:
                if self._connect_tasks.get(server_name) is task:
                    self._connect_tasks.pop(server_name, None)

    async def _connect_with_lifecycle_lock(self, config: ResolvedServerConfig) -> None:
        async with self._lifecycle_lock:
            await self._connect_with_retry(config)

    async def connect_server(
        self, config: ResolvedServerConfig, retry: bool = True
    ) -> list[str]:
        """Connect one server through same-server single-flight startup."""
        async with self._lifecycle_lock:
            try:
                await self._connect_singleflight(config, retry=retry)
                if self.is_server_online(config.name):
                    self._lazy_configs.pop(config.name, None)
                self._revision_id = _generate_revision_id()
                self._last_refresh_ts = time.time()
                return []
            except Exception as e:
                if config.name in self._servers:
                    self._servers[config.name].status = ServerStatusEnum.ERROR
                    self._servers[config.name].last_error = str(e)
                return [f"Failed to connect to {config.name}: {e}"]

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
        pending_requests = self.get_pending_requests(name)
        active_tasks = self.get_active_tasks(name)
        if (pending_requests or active_tasks) and not force:
            return (
                False,
                0,
                "Disconnect refused because this server has pending requests or active MCP tasks. "
                "Use gateway.list_pending to inspect them or retry with force=true.",
            )

        cancelled = self.cancel_pending_requests(name) if pending_requests else 0
        if active_tasks:
            for task in active_tasks:
                ok, _task, message = await self.cancel_task(
                    name, task.task_id, force=True
                )
                if not ok:
                    return (False, cancelled, message)

        async with self._lifecycle_lock:
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

            if managed.read_task and not managed.read_task.done():
                managed.read_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(managed.read_task), timeout=1.0
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception:
                    pass

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
                    await _terminate_process_tree(managed.process, name)
            except Exception as e:
                logger.warning(f"Error disconnecting from {name}: {e}")
                return (False, cancelled, str(e))

            await self._cancel_background_tasks(server_name=name)
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
            return (True, cancelled, None)

    async def restart_server(
        self, config: ResolvedServerConfig, force: bool = False
    ) -> tuple[bool, int, list[str]]:
        """Restart one server by disconnecting then connecting the same config."""
        disconnected, cancelled, error = await self.disconnect_server(
            config.name, force
        )
        if not disconnected:
            return (False, cancelled, [error or "Restart refused."])

        errors = await self.connect_server(config)
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
            except Exception as e:
                last_error = e
                if attempt < MAX_CONNECTION_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        f"Connection to {config.name} failed (attempt {attempt + 1}/"
                        f"{MAX_CONNECTION_RETRIES}), retrying in {delay}s: {e}"
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        if last_error:
            raise last_error

    async def _connect_server(self, config: ResolvedServerConfig) -> None:
        """Connect to a single MCP server."""
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
        poll_interval = payload.get("pollInterval", payload.get("poll_interval"))
        return McpTaskInfo(
            task_id=task_id,
            status=payload.get("status"),
            status_message=status_message if isinstance(status_message, str) else None,
            created_at=payload.get("createdAt", payload.get("created_at")),
            updated_at=payload.get(
                "updatedAt",
                payload.get(
                    "updated_at",
                    payload.get("lastUpdatedAt", payload.get("last_updated_at")),
                ),
            ),
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
        record = McpTaskRecord(
            task_id=task_info.task_id,
            status=task_info.status,
            status_message=task_info.status_message,
            created_at=task_info.created_at
            if existing is None
            else existing.created_at,
            updated_at=task_info.updated_at or time.time(),
            ttl=task_info.ttl,
            poll_interval=task_info.poll_interval,
            raw=task_info.raw,
            server_name=server_name,
            tool_id=tool_id or (existing.tool_id if existing else None),
            requestor_context=requestor_context
            or (existing.requestor_context if existing else None),
        )
        self._tasks[(server_name, task_info.task_id)] = record
        self._evict_terminal_tasks()
        return record

    def _evict_terminal_tasks(self) -> None:
        """Prune oldest terminal task records past the retention cap.

        Only completed/failed/cancelled records are candidates; active tasks are
        left untouched. Eviction targets the oldest records by ``updated_at`` so
        recently-finished tasks remain queryable.
        """
        terminal = [
            (key, record)
            for key, record in self._tasks.items()
            if self._terminal_task(record)
        ]
        excess = len(terminal) - self._max_terminal_tasks
        if excess <= 0:
            return
        terminal.sort(key=lambda item: (item[1].updated_at or 0.0, item[0]))
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
        """
        entries = (
            _parse_tool_entries(name, tools, self._max_tools_per_server)
            if parsed is None
            else parsed
        )
        for tool_id, tool_info in entries:
            self._tools[tool_id] = tool_info
        indexed = len(entries)
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
        overstating the catalog.
        """
        entries = _parse_resource_entries(name, resources) if parsed is None else parsed
        for resource_id, resource_info in entries:
            self._resources[resource_id] = resource_info
        indexed = len(entries)
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
        returned is what was actually indexed, not what was offered.
        """
        entries = _parse_prompt_entries(name, prompts) if parsed is None else parsed
        for prompt_id, prompt_info in entries:
            self._prompts[prompt_id] = prompt_info
        indexed = len(entries)
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
                        f"failed ({exc}); discarding {len(collected)} entries "
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
            logger.warning(f"[{name}] Catalog reconciliation task failed: {e}")
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
            logger.warning(f"[{name}] Catalog reconciliation failed: {e}")
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
                logger.warning(
                    f"[{name}] Every {kind} entry in the listing was unparseable "
                    f"({len(offered)} offered); keeping the previous {kind} "
                    "rather than reporting them removed"
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
        )
        self._clients[name] = managed

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

        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = str(e)
            for task in (managed.read_task, managed.stderr_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await asyncio.shield(task)
                    except (asyncio.CancelledError, Exception):
                        pass
            await _terminate_process_tree(process, name)
            # Drop the stale ERROR client so it can't be found as a live
            # connection on the next connect attempt (issue: stale entry + leak).
            if self._clients.get(name) is managed:
                self._clients.pop(name, None)
            raise

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
                f"[{name}] remote transport owner exited unexpectedly: {exc}"
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
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            # The 5s budget bounds this graceful wait only, not the
            # escalation below: awaiting the cancelled owner is itself
            # unbounded, and an __aexit__ that ignores cancellation hangs
            # there -- the same hang class as today's dead-peer teardown,
            # neither introduced nor removed by this method. Timeout-as-
            # success is deliberate (a dead peer must not read as "disconnect
            # refused"), but that only covers the timeout itself -- a genuine
            # failure surfacing *while* the owner unwinds under our cancel
            # must still propagate, so only the CancelledError our own
            # cancel() causes is swallowed below.
            logger.warning(
                f"[{name}] remote transport did not close within {timeout}s; cancelling"
            )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            # NOT the same case as the timeout above. The shield keeps the
            # owner alive, so a CancelledError here is *our caller* being
            # cancelled, not the owner. Escalate to the owner so its stack
            # still unwinds, then re-raise the caller's own cancellation --
            # swallowing it would suppress cancellation of whatever task is
            # running disconnect_server / _shutdown_one, which is the exact
            # cancellation-correctness class this fix exists to fix. A
            # genuine failure surfacing from the owner during this forced
            # unwind can't also be raised (the caller's own CancelledError
            # takes precedence, per the same reasoning), but is logged rather
            # than silently dropped.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # exc_info, not just the message: this is by construction the
                # hardest path here to reproduce (needs a caller cancelled
                # *while* a forced owner unwind is independently failing), so
                # the traceback matters if it's ever seen again.
                logger.warning(
                    f"[{name}] remote transport failed to unwind while "
                    f"escalating our caller's cancellation: {exc}",
                    exc_info=exc,
                )
            raise
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
        except BaseException:
            # BaseException, not Exception: cancellation is the case that
            # bites here, and it is a BaseException. Cancel rather than
            # signal-and-wait -- a cancelled caller must not linger, and the
            # peer reaps its own session on timeout. The owner's `async
            # with` unwinds in the owner, as always -- never touch its stack
            # from this task.
            owner_task.cancel()
            await asyncio.gather(owner_task, return_exceptions=True)
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

        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = str(e)
            if managed.read_task and not managed.read_task.done():
                managed.read_task.cancel()
                try:
                    await asyncio.shield(managed.read_task)
                except (asyncio.CancelledError, Exception):
                    pass
            await self._close_remote_transport(name, managed)
            # Drop the stale ERROR client so it can't be found as a live
            # connection on the next connect attempt.
            if self._clients.get(name) is managed:
                self._clients.pop(name, None)
            raise

    async def _read_stderr(self, name: str, stderr: asyncio.StreamReader) -> None:
        """Read stderr from a server process."""
        try:
            while True:
                try:
                    line = await asyncio.wait_for(stderr.readline(), timeout=120.0)
                except asyncio.TimeoutError:
                    logger.debug(f"[{name}] stderr readline timed out, continuing")
                    continue
                if not line:
                    break
                logger.debug(f"[{name}] stderr: {line.decode().strip()}")
        except Exception as e:
            logger.debug(f"[{name}] stderr reader error: {e}")

    def _handle_stdout_line(
        self, name: str, managed: ManagedClient, line: bytes, now: float
    ) -> None:
        """Dispatch one complete JSON-RPC line from a downstream server's stdout."""
        try:
            message = json.loads(line.decode())
            msg_id = message.get("id")
            if msg_id is not None and msg_id in managed.pending_requests:
                pending = managed.pending_requests.pop(msg_id)

                # Track response time
                elapsed_ms = (now - pending.started_at) * 1000
                managed.response_times.append(elapsed_ms)
                if managed.response_times:
                    managed.status.avg_response_time_ms = sum(
                        managed.response_times
                    ) / len(managed.response_times)

                # Update pending count
                managed.status.pending_request_count = len(managed.pending_requests)

                if "error" in message:
                    pending.future.set_exception(_downstream_error(message["error"]))
                else:
                    pending.future.set_result(message.get("result", {}))
            else:
                # A notification carries a `method` and no `id`, so it falls
                # through the gate above with nothing to resolve. Server->client
                # *requests* also carry a method but do have an id, and are not
                # ours to handle here — hence the explicit `msg_id is None`.
                method = message.get("method")
                if msg_id is None and isinstance(method, str):
                    self._handle_downstream_notification(name, managed, method)
        except json.JSONDecodeError:
            # Non-JSON output already counted as a heartbeat by the caller.
            logger.debug(
                f"[{name}] Non-JSON output: {line.decode(errors='replace').strip()}"
            )

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
                now = time.time()
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
            read_failure_reason = f"stdout read error: {e}"
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
        task = asyncio.create_task(
            self._reconnect_loop(name, config),
            name=f"reconnect-{name}",
        )
        self._reconnect_tasks[name] = task
        self._track_background_task(task, name)

        def clear_reconnect(done: asyncio.Task[None]) -> None:
            if self._reconnect_tasks.get(name) is done:
                self._reconnect_tasks.pop(name, None)

        task.add_done_callback(clear_reconnect)

    async def _reconnect_loop(self, name: str, config: ResolvedServerConfig) -> None:
        """Attempt to reconnect a crashed server with exponential back-off.

        Tries up to 3 times with 5 s / 15 s / 30 s delays. Gives up if another
        caller has already brought the server back online.
        """
        delays = [5.0, 15.0, 30.0]
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
                        await self._connect_singleflight(config)
                    logger.info(f"[{name}] reconnected successfully")
                    return
                except Exception as e:
                    safe_error = sanitize_auth_diagnostic(e)
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
        """Read JSON-RPC messages from an SSE stream."""
        try:
            async for message in read_stream:
                # Any output counts as per-request liveness, including progress
                # notifications (id: null), so the idle timeout sees the keepalive.
                now = time.time()
                managed.status.last_activity_at = now
                for req in managed.pending_requests.values():
                    req.last_heartbeat = now

                if isinstance(message, Exception):
                    raise message

                payload = message.message.model_dump(
                    by_alias=True,
                    mode="json",
                    exclude_none=True,
                )
                msg_id = payload.get("id")
                if msg_id is not None and msg_id in managed.pending_requests:
                    pending = managed.pending_requests.pop(msg_id)

                    elapsed_ms = (now - pending.started_at) * 1000
                    managed.response_times.append(elapsed_ms)
                    if managed.response_times:
                        managed.status.avg_response_time_ms = sum(
                            managed.response_times
                        ) / len(managed.response_times)

                    managed.status.pending_request_count = len(managed.pending_requests)

                    if "error" in payload:
                        pending.future.set_exception(
                            _downstream_error(payload["error"])
                        )
                    else:
                        pending.future.set_result(payload.get("result", {}))
                else:
                    # Same fall-through as the stdio path: a notification has a
                    # method and no id. `_handle_downstream_notification` never
                    # raises, which matters more here — this loop's blanket
                    # `except Exception` would tear the connection down and
                    # trigger a reconnect.
                    method = payload.get("method")
                    if msg_id is None and isinstance(method, str):
                        self._handle_downstream_notification(name, managed, method)
        except Exception as e:
            logger.debug(f"[{name}] SSE read error: {e}")
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
        now = time.time()

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
        )
        managed.pending_requests[request_id] = pending
        managed.status.pending_request_count = len(managed.pending_requests)

        # Send request
        if managed.is_remote:
            if managed.write_stream is None:
                raise RuntimeError("Remote stream not connected")
            # mcp 2.0.0's JSONRPCMessage is a bare union (JSONRPCRequest |
            # JSONRPCNotification | JSONRPCResponse | JSONRPCError), not a
            # pydantic model, so it has no .model_validate(); construct via its
            # published TypeAdapter instead.
            msg = mcp_types.jsonrpc_message_adapter.validate_python(request)
            await managed.write_stream.send(SessionMessage(msg))
        else:
            if not managed.process or not managed.process.stdin:
                raise RuntimeError("Process not running")

            data = json.dumps(request) + "\n"
            managed.process.stdin.write(data.encode())
            await managed.process.stdin.drain()

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
            managed.pending_requests.pop(request_id, None)
            managed.status.pending_request_count = len(managed.pending_requests)
            raise TimeoutError(f"Request {method} timed out")

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
        """
        slice_s = min(idle_timeout_s, 1.0)
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=slice_s)
            except asyncio.TimeoutError:
                if future.done():
                    return future.result()
                now = time.time()
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
            data = json.dumps(notification) + "\n"
            managed.process.stdin.write(data.encode())
            await managed.process.stdin.drain()

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        async with self._lifecycle_lock:
            await self._disconnect_all_unlocked()

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
                if managed.read_task:
                    managed.read_task.cancel()
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(managed.read_task), timeout=1.0
                        )
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass

                # Close transport. _close_remote_transport itself never
                # swallows a genuine transport-exit failure; the swallow
                # belongs here -- this loop runs under `asyncio.gather`
                # across every connected client, and one client's teardown
                # failure must not abort the others' or the wholesale
                # shutdown budget (issue #79/1c).
                if managed.is_remote:
                    await self._close_remote_transport(name, managed)
                else:
                    await _terminate_process_tree(managed.process, name)
            except Exception as e:
                logger.warning(f"Error disconnecting from {name}: {e}")

        # Reap servers concurrently: each _terminate_process_tree can cost up to
        # ~8s for a hung stdio server, and disconnect_all() runs under a bounded
        # shutdown budget (server.py wraps it in wait_for). A sequential loop
        # would let two+ hung servers blow that budget and leave later groups
        # unsignalled — orphaning browsers (issue #79/1c) at shutdown. Concurrent
        # reaping makes total time ≈ the slowest single server.
        clients = list(self._clients.items())
        if clients:
            await asyncio.gather(
                *(_shutdown_one(name, managed) for name, managed in clients),
                return_exceptions=True,
            )

        current = asyncio.current_task()
        exclude = {current} if current is not None else set()
        await self._cancel_background_tasks(exclude=exclude)
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

        Safe to call on any managed client regardless of state. All exceptions are
        suppressed so callers always complete successfully.

        Cancels only *this* client's own read/stderr tasks — not every background
        task scoped to the server name. A reconnect runs its connect inside a task
        that is itself scoped to the name; a server-name-wide cancel here would
        cancel the in-flight reconnect (cascading into the running connect task)
        and abort the very recovery that called us.
        """
        for task in (managed.read_task, managed.stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, Exception):
                    pass
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
                logger.warning(f"[{name}] Error closing remote transport: {e}")
        else:
            await _terminate_process_tree(managed.process, name)
        self._clients.pop(name, None)
        self._servers.pop(name, None)
        self._remove_server_indexes(name)

    async def refresh(self, configs: list[ResolvedServerConfig]) -> list[str]:
        """Refresh connections (disconnect + reconnect)."""
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
            RuntimeError: If process is not running or missing pipes
            Exception: If MCP initialization fails
        """
        # Validate process state
        if process.returncode is not None:
            raise RuntimeError(f"Process for {name} has already exited")
        if not process.stdin:
            raise RuntimeError(f"Process for {name} has no stdin pipe")
        if not process.stdout:
            raise RuntimeError(f"Process for {name} has no stdout pipe")

        logger.info(f"Adopting process for MCP server: {name}")

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

        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = str(e)
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
                now = time.time()

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
                logger.debug(f"Health monitor error: {e}")

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
        now = time.time()
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

        now = time.time()
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

        # Cancel the request
        pending.future.cancel()
        managed.pending_requests.pop(local_id, None)
        managed.status.pending_request_count = len(managed.pending_requests)
        logger.info(
            f"Cancelled request {request_id} (stalled={was_stalled}, elapsed={elapsed:.1f}s)"
        )

        return ("cancelled", "Request cancelled successfully", was_stalled, elapsed)
