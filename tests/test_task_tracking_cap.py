"""The task-tracking cap is per server and covers unfinished tasks
(Consiliency/pmcp#338).

`TaskRegistry.put` is the only way a record gets in, and `ClientManager.
_record_task` is its only caller. The structural tests at the bottom pin both
facts. Every record path is then driven through the real manager code and
must hit the cap.
"""

from __future__ import annotations

import ast
import asyncio
import random
from types import SimpleNamespace
from collections.abc import Awaitable, Callable, Collection, Iterator
from pathlib import Path
from typing import Any

import pytest

from pmcp.client.manager import ClientManager, ManagedClient, PendingRequest
from pmcp.client.task_registry import (
    DEFAULT_TASKS_PER_SERVER,
    DEFAULT_TASKS_TOTAL,
    TaskCustody,
    TaskRegistry,
    task_is_terminal,
)
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import (
    McpTaskInfo,
    McpTaskRecord,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)
from tests.test_task_numeric_bounds import _task_server

SRC = Path(__file__).resolve().parent.parent / "src" / "pmcp"


@pytest.fixture(autouse=True)
def _no_pin_leaks(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Rev 9: every pin taken during a test is released by its end. Each
    `TaskRegistry` built during the test -- every `ClientManager` builds one --
    is checked afterwards: a request that returned, raised, was cancelled or
    timed out must have left its pins at zero. Rev 11: and every teardown's
    custody is released too."""
    built: list[TaskRegistry] = []
    original = TaskRegistry.__init__

    def tracking(self: TaskRegistry, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(TaskRegistry, "__init__", tracking)
    yield
    leaks = [registry.pinned for registry in built if registry.pinned]
    assert leaks == [], leaks
    # getattr: the module also runs against earlier revisions' registries
    held = [getattr(r, "watching", 0) for r in built if getattr(r, "watching", 0)]
    assert held == [], held


def _manager(*servers: str) -> tuple[ClientManager, dict[tuple[str, str], Any]]:
    """A real ClientManager with one task-capable server per name (and the tool
    `tasks::run` on "tasks"). Each server answers from `replies`, keyed by
    (server, method)."""
    manager = ClientManager()
    replies: dict[tuple[str, str], Any] = {}
    clients = {}
    for name in servers:
        clients[name] = _task_server(manager, {})  # registers itself as "tasks"
        clients[name].status.name = name
    manager._clients = clients

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        return replies[(managed.status.name, method)]

    manager._send_request = send  # type: ignore[method-assign, assignment]
    return manager, replies


def _listing(prefix: str, n: int, status: Any = "completed") -> dict[str, Any]:
    return {"tasks": [{"taskId": f"{prefix}{i}", "status": status} for i in range(n)]}


def _ids(manager: ClientManager, server: str) -> list[str]:
    return [t.task_id for t in manager.get_tracked_tasks(server)]


def _put(registry: TaskRegistry, server: str, task_id: str, status: Any) -> None:
    registry.put(McpTaskRecord(server_name=server, task_id=task_id, status=status))


# --- the three repros from the issue, at the real default caps --------------------


def test_the_defaults_are_100_per_server_and_1000_in_total() -> None:
    registry = ClientManager()._tasks
    assert isinstance(registry, TaskRegistry)
    assert (registry.per_server, registry.total) == (100, 1000)
    assert (DEFAULT_TASKS_PER_SERVER, DEFAULT_TASKS_TOTAL) == (100, 1000)


@pytest.mark.parametrize("listed", [98, 99, 100, 500])
@pytest.mark.asyncio
async def test_one_server_listing_tasks_never_evicts_another_servers(
    listed: int,
) -> None:
    """Repro (a): on main, A listing 99 finished tasks evicted one of B's two,
    and 100 evicted both. Now B keeps both whatever A lists."""
    manager, replies = _manager("A", "B")
    replies[("B", "tasks/list")] = _listing("b", 2)
    replies[("A", "tasks/list")] = _listing("a", listed)
    await manager.list_tasks("B")
    await manager.list_tasks("A")
    assert _ids(manager, "B") == ["b0", "b1"]
    assert len(_ids(manager, "A")) == min(listed, 100)


@pytest.mark.parametrize("status", ["working", "input_required", "", " ", 7, None])
@pytest.mark.asyncio
async def test_an_unfinished_flood_is_capped_at_the_newest_100(status: Any) -> None:
    """Repro (b) and (c): 500 `working` records, or 500 whose status was
    unusable and became null (Consiliency/pmcp#298), were all kept on main."""
    manager, replies = _manager("F")
    replies[("F", "tasks/list")] = _listing("w", 500, status)
    listed = await manager.list_tasks("F")
    assert len(listed["tasks"]) == 500  # the cap bounds tracking, not the reply
    assert _ids(manager, "F") == sorted(f"w{i}" for i in range(400, 500))
    assert len(manager._tasks) == 100


@pytest.mark.asyncio
async def test_gateway_tasks_list_still_returns_every_listed_task() -> None:
    """The handler falls back to the listed payload for a record the cap
    already evicted, so a 500-task page is returned whole."""
    manager, replies = _manager("tasks")
    replies[("tasks", "tasks/list")] = _listing("w", 500, "working")
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.tasks_list({"server_name": "tasks"})
    assert out.ok, out.errors
    assert [t.task_id for t in out.tasks] == [f"w{i}" for i in range(500)]
    assert len(manager._tasks) == 100


# --- eviction order inside one server ----------------------------------------------


def test_finished_records_go_before_unfinished_ones_each_in_record_order() -> None:
    registry = TaskRegistry(per_server=3)
    for task_id, status in (("w1", "working"), ("c1", "completed"), ("w2", None)):
        _put(registry, "s", task_id, status)
    _put(registry, "s", "c2", "failed")  # evicts c1, not the older w1
    assert [k[1] for k in registry] == ["w1", "w2", "c2"]
    _put(registry, "s", "w3", "working")  # evicts c2, the only finished one
    assert [k[1] for k in registry] == ["w1", "w2", "w3"]
    _put(registry, "s", "w4", "working")  # none finished: the oldest, w1
    assert [k[1] for k in registry] == ["w2", "w3", "w4"]


def test_every_terminal_status_counts_as_finished() -> None:
    for status in ("completed", "failed", "cancelled"):
        registry = TaskRegistry(per_server=2)
        _put(registry, "s", "w", "working")
        _put(registry, "s", "f", status)
        _put(registry, "s", "n", "working")
        assert [k[1] for k in registry] == ["w", "n"], status
    assert not task_is_terminal(McpTaskRecord(server_name="s", task_id="t"))


def test_re_recording_a_task_makes_it_the_newest() -> None:
    registry = TaskRegistry(per_server=2)
    _put(registry, "s", "a", "working")
    _put(registry, "s", "b", "working")
    _put(registry, "s", "a", "working")
    _put(registry, "s", "c", "working")
    assert [k[1] for k in registry] == ["a", "c"]
    assert len(registry) == 2


def test_the_record_just_stored_is_never_its_own_victim() -> None:
    """A full bucket of unfinished records takes a finished one: the oldest
    unfinished record goes, not the new finished one."""
    registry = TaskRegistry(per_server=2)
    _put(registry, "s", "w1", "working")
    _put(registry, "s", "w2", "working")
    _put(registry, "s", "done", "completed")
    assert [k[1] for k in registry] == ["w2", "done"]


def test_a_status_change_moves_a_record_between_classes() -> None:
    registry = TaskRegistry(per_server=2)
    _put(registry, "s", "a", "working")
    _put(registry, "s", "b", "working")
    _put(registry, "s", "a", "completed")  # a is now finished (and newest)
    _put(registry, "s", "c", "working")  # finished first: a goes, not b
    assert [k[1] for k in registry] == ["b", "c"]


def test_put_returns_what_it_evicted_oldest_first() -> None:
    registry = TaskRegistry(per_server=1)
    _put(registry, "s", "a", "working")
    evicted = registry.put(McpTaskRecord(server_name="s", task_id="b"))
    assert [r.task_id for r in evicted] == ["a"]


@pytest.mark.parametrize("key", ["st", ("s",), ("s", "t", "x"), 7, None, (["s"], "t")])
def test_a_key_that_is_not_a_pair_is_absent_not_an_error(key: Any) -> None:
    """N3: the Mapping contract. `get` and `in` see a missing key, and `[]`
    raises KeyError, never ValueError or TypeError."""
    registry = TaskRegistry()
    _put(registry, "s", "t", "working")
    assert registry.get(key) is None
    assert key not in registry
    with pytest.raises(KeyError):
        registry[key]


@pytest.mark.parametrize("per_server,total", [(0, 1), (1, 0), (-1, 5)])
def test_a_cap_below_one_is_refused(per_server: int, total: int) -> None:
    with pytest.raises(ValueError):
        TaskRegistry(per_server=per_server, total=total)


# --- the total cap --------------------------------------------------------------------


def test_the_total_cap_takes_from_the_server_holding_the_most() -> None:
    registry = TaskRegistry(per_server=10, total=5)
    for i in range(3):
        _put(registry, "big", f"b{i}", "working")
    _put(registry, "small", "s0", "completed")
    _put(registry, "small", "s1", "completed")
    _put(registry, "new", "n0", "working")  # 6 > 5: big (3) gives up b0
    assert sorted(registry) == sorted(
        [("big", "b1"), ("big", "b2"), ("small", "s0"), ("small", "s1"), ("new", "n0")]
    )
    assert len(registry) == 5


def test_on_a_tie_the_total_cap_takes_the_oldest_victim() -> None:
    registry = TaskRegistry(per_server=10, total=4)
    _put(registry, "x", "x0", "working")
    _put(registry, "y", "y0", "completed")
    _put(registry, "x", "x1", "working")
    _put(registry, "y", "y1", "completed")
    _put(registry, "z", "z0", "working")  # x and y hold 2; x's victim x0 is older
    assert sorted(registry) == [("x", "x1"), ("y", "y0"), ("y", "y1"), ("z", "z0")]


def test_the_total_cap_never_takes_the_record_just_stored() -> None:
    registry = TaskRegistry(per_server=10, total=1)
    _put(registry, "a", "a0", "completed")
    _put(registry, "b", "b0", "working")
    assert list(registry) == [("b", "b0")]
    _put(registry, "b", "b1", "working")
    assert list(registry) == [("b", "b1")]


def _reference_victim(
    log: list[tuple[str, str, bool]], server: str, new: tuple[str, str]
) -> tuple[str, str]:
    """Plain restatement of the rule, over a list kept in record order."""
    mine = [e for e in log if e[0] == server and (e[0], e[1]) != new]
    finished = [e for e in mine if e[2]]
    victim = (finished or mine)[0]
    return (victim[0], victim[1])


def test_the_registry_matches_a_plain_restatement_of_the_rule() -> None:
    """Random puts (re-records, every status class, several servers, both caps
    binding) against a reference model written as list scans."""
    rng = random.Random(338)
    statuses = ["working", "completed", "failed", "cancelled", None, "input_required"]
    for per_server, total in ((3, 7), (5, 5), (4, 100), (100, 6), (1, 2)):
        registry = TaskRegistry(per_server=per_server, total=total)
        log: list[tuple[str, str, bool]] = []  # (server, task_id, finished)
        for _ in range(600):
            server = rng.choice("abcd")
            task_id = str(rng.randrange(12))
            status = rng.choice(statuses)
            record = McpTaskRecord(server_name=server, task_id=task_id, status=status)
            new = (server, task_id)
            log = [e for e in log if (e[0], e[1]) != new]
            log.append((server, task_id, task_is_terminal(record)))
            expected: list[tuple[str, str]] = []
            while sum(1 for e in log if e[0] == server) > per_server:
                victim = _reference_victim(log, server, new)
                expected.append(victim)
                log = [e for e in log if (e[0], e[1]) != victim]
            while len(log) > total:
                # the server holding the most; on a tie, the oldest victim
                # (`log` is in record order, so its index is the age)
                ranked = []
                for s in {e[0] for e in log}:
                    if any(e[0] == s and (e[0], e[1]) != new for e in log):
                        v = _reference_victim(log, s, new)
                        index = [(e[0], e[1]) for e in log].index(v)
                        size = sum(1 for e in log if e[0] == s)
                        ranked.append(((size, -index), v))
                victim = max(ranked)[1]
                expected.append(victim)
                log = [e for e in log if (e[0], e[1]) != victim]
            evicted = registry.put(record)
            assert [(r.server_name, r.task_id) for r in evicted] == expected
            assert sorted(registry) == sorted((e[0], e[1]) for e in log)
            assert registry[new] is record
            assert len(registry) == len(log) <= total


# --- disconnect, reconcile, shutdown ------------------------------------------------


def test_a_disconnect_drops_only_that_servers_records_and_frees_its_count() -> None:
    manager = ClientManager()
    manager._tasks.total = 4
    for i in range(2):
        _put(manager._tasks, "gone", f"g{i}", "working")
        _put(manager._tasks, "kept", f"k{i}", "working")
    manager._remove_server_indexes("gone")
    assert list(manager._tasks) == [("kept", "k0"), ("kept", "k1")]
    assert len(manager._tasks) == 2
    _put(manager._tasks, "next", "n0", "working")
    _put(manager._tasks, "next", "n1", "working")
    assert len(manager._tasks) == 4  # nothing evicted: the freed slots count


def test_a_reconcile_keeps_records_and_shutdown_clears_them() -> None:
    manager = ClientManager()
    _put(manager._tasks, "s", "t", "working")
    manager._remove_server_indexes("s", drop_tasks=False)
    assert ("s", "t") in manager._tasks
    manager._tasks.clear()
    assert len(manager._tasks) == 0 and list(manager._tasks) == []


# --- an evicted unfinished task: what the caller sees ---------------------------


@pytest.mark.asyncio
async def test_an_evicted_unfinished_task_is_re_tracked_by_tasks_get() -> None:
    """Eviction forgets pmcp's record, not the downstream task. `tasks_cancel`
    says "not found" (it needs the record); `tasks_get` asks the downstream,
    tracks the task again, and a cancel then goes through. Until then it is
    not an active task, so a disconnect does not wait on it."""
    manager, replies = _manager("tasks")
    manager._tasks.per_server = 1
    _put(manager._tasks, "tasks", "old", "working")
    _put(manager._tasks, "tasks", "new", "working")
    assert [t.task_id for t in manager.get_active_tasks("tasks")] == ["new"]

    ok, _record, message = await manager.cancel_task("tasks", "old")
    assert not ok and message == "Task not found: tasks::old"

    replies[("tasks", "tasks/get")] = {"taskId": "old", "status": "working"}
    replies[("tasks", "tasks/cancel")] = {"taskId": "old", "status": "cancelled"}
    await manager.get_task("tasks", "old")
    ok, record, _message = await manager.cancel_task("tasks", "old")
    assert ok and record is not None and record.status == "cancelled"


@pytest.mark.asyncio
async def test_invoke_at_the_cap_keeps_its_record_and_redacts_by_default() -> None:
    """`gateway.invoke` reads the record back to choose its default redaction.
    A bucket full of unfinished tasks must not evict the finished task the call
    just created."""
    manager, replies = _manager("tasks")
    for i in range(100):
        _put(manager._tasks, "tasks", f"w{i}", "working")
    secret = "sk-abcdef1234567890abcdef"
    replies[("tasks", "tools/call")] = {
        "task": {"taskId": "fresh", "status": "completed"},
        "content": [{"type": "text", "text": secret}],
    }
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.invoke({"tool_id": "tasks::run", "arguments": {}, "task": {}})
    assert out.ok, out.errors
    assert manager.get_task_record("tasks", "fresh") is not None
    assert secret not in out.model_dump_json()
    assert len(manager._tasks) == 100


# --- every record path goes through the cap ------------------------------------------

TASK = {"taskId": "t", "status": "working"}


async def _call_tool(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tools/call")] = {"task": TASK}
    await m.call_tool("tasks::run", {}, task={})


async def _list_tasks(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/list")] = {"tasks": [TASK]}
    await m.list_tasks("tasks")


async def _get_task(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/get")] = TASK
    await m.get_task("tasks", "t")


async def _get_task_result_payload(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/result")] = {"task": TASK, "result": {}}
    await m.get_task_result("tasks", "t")


async def _get_task_result_fallback(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/result")] = {"content": []}  # no task: falls back to get
    r[("tasks", "tasks/get")] = TASK
    await m.get_task_result("tasks", "t")


async def _cancel_task_payload(m: ClientManager, r: dict[Any, Any]) -> None:
    _put(m._tasks, "tasks", "t", "working")
    r[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "cancelled"}
    await m.cancel_task("tasks", "t")


async def _cancel_task_synthesized(m: ClientManager, r: dict[Any, Any]) -> None:
    _put(m._tasks, "tasks", "t", "working")
    r[("tasks", "tasks/cancel")] = {}  # no task: pmcp records "cancelled" itself
    await m.cancel_task("tasks", "t")


async def _teardown_cancel(m: ClientManager, r: dict[Any, Any]) -> None:
    _put(m._tasks, "tasks", "t", "working")
    r[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "cancelled"}
    await m.cancel_active_tasks("tasks")


#: (path, the manager method that calls `_record_task`)
RECORD_PATHS: dict[
    str, tuple[Callable[[ClientManager, dict[Any, Any]], Awaitable[None]], str]
] = {
    "tools/call": (_call_tool, "call_tool_with_task"),
    "tasks/list": (_list_tasks, "list_tasks"),
    # rev 9: the function that sends records its reply (`_record_reply` is gone)
    "tasks/get": (_get_task, "_fetch_task"),
    "tasks/result": (_get_task_result_payload, "get_task_result_with_task"),
    "tasks/result->tasks/get": (_get_task_result_fallback, "_fetch_task"),
    "tasks/cancel": (_cancel_task_payload, "_send_task_cancel"),
    "tasks/cancel synthesized": (_cancel_task_synthesized, "_send_task_cancel"),
    "teardown tasks/cancel": (_teardown_cancel, "_send_task_cancel"),
}


@pytest.mark.parametrize("path", list(RECORD_PATHS))
@pytest.mark.asyncio
async def test_every_record_path_is_capped(path: str) -> None:
    """Each path records `t` into a bucket already at its cap of 2. Another
    server's records are untouched, and the bucket stays at 2."""
    manager, replies = _manager("tasks", "other")
    manager._tasks.per_server = 2
    _put(manager._tasks, "other", "o", "completed")
    _put(manager._tasks, "tasks", "old", "completed")
    _put(manager._tasks, "tasks", "mid", "working")
    drive, _method = RECORD_PATHS[path]
    await drive(manager, replies)
    assert _ids(manager, "tasks") == ["mid", "t"]
    assert _ids(manager, "other") == ["o"]
    assert len(manager._tasks) == 3


# --- structure: one way in ---------------------------------------------------------------


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _calls(func: ast.AST, attr: str) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Attribute) and n.func.attr == attr)
            or (isinstance(n.func, ast.Name) and n.func.id == attr)
        )
        for n in ast.walk(func)
    )


def _where(attr: str) -> set[tuple[str, str]]:
    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for func in _functions(tree):
            # innermost function only: skip one whose nested function calls it
            inner = [f for f in _functions(func) if f is not func]
            if _calls(func, attr) and not any(_calls(f, attr) for f in inner):
                found.add((str(path.relative_to(SRC)), func.name))
    return found


def test_record_task_is_called_by_exactly_the_tested_paths() -> None:
    """A new caller of `_record_task` is a new record path: add it to
    RECORD_PATHS (with its test) before this passes."""
    tested = {("client/manager.py", method) for _drive, method in RECORD_PATHS.values()}
    assert _where("_record_task") == tested


def _owned(
    attr_test: Callable[[ast.AST, ast.AST | None], str | None],
) -> set[tuple[str, str, str]]:
    """(file, innermost function, label) for every node in src/pmcp for which
    `attr_test(node, parent)` returns a label."""
    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            label = attr_test(node, parents.get(node))
            if label is None:
                continue
            owner: ast.AST | None = node
            while owner is not None and not isinstance(
                owner, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                owner = parents.get(owner)
            name = owner.name if owner is not None else "<module>"
            found.add((str(path.relative_to(SRC)), name, label))
    return found


def test_only_record_task_builds_records_and_puts_them() -> None:
    """Over all of src/pmcp (rev 2, N2): `McpTaskRecord(` is built only in
    `_record_task`, and every `.put(` call is classified -- the registry's,
    in `_record_task`, and the npm resolver's line queue. A new `.put(` on
    anything fails here until it is added, so an alias of the registry
    (`reg = self._tasks; reg.put(...)`) cannot slip past."""
    assert _where("McpTaskRecord") == {("client/manager.py", "_record_task")}

    def put_call(node: ast.AST, _parent: ast.AST | None) -> str | None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "put"
        ):
            return ast.unparse(node.func.value)
        return None

    assert _owned(put_call) == {
        ("client/manager.py", "_record_task", "self._tasks"),
        ("manifest/npm_resolver.py", "_pump", "self._queue"),
    }


#: Every use of `_tasks` in src/pmcp that is not a plain read.
_REGISTRY_READS = {
    "get",
    "items",
    "keys",
    "values",
    "Subscript",
    "Compare",
    "held",
    "owed_records",
}


def test_the_registry_is_written_in_exactly_three_places() -> None:
    """Over all of src/pmcp: `_tasks` is bound once, written by `put` in
    `_record_task`, `drop_server` on a disconnect and `clear` at shutdown, and
    otherwise only read. It is never passed or assigned as a value, so it has
    no alias that a scan of `self._tasks` would miss."""

    def use(node: ast.AST, parent: ast.AST | None) -> str | None:
        if not (isinstance(node, ast.Attribute) and node.attr == "_tasks"):
            return None
        label = (
            parent.attr if isinstance(parent, ast.Attribute) else type(parent).__name__
        )
        return None if label in _REGISTRY_READS else label

    assert _owned(use) == {
        ("client/manager.py", "__init__", "Assign"),
        ("client/manager.py", "_record_task", "put"),
        ("client/manager.py", "_remove_server_indexes", "drop_server"),
        ("client/manager.py", "_disconnect_all_unlocked", "clear"),
        # rev 9: pins, taken and released only by `_pinned`
        ("client/manager.py", "_pinned", "pin"),
        ("client/manager.py", "_pinned", "unpin"),
        # rev 11: a teardown's custody, registered and released only by
        # `_custody`
        ("client/manager.py", "_custody", "watch"),
        ("client/manager.py", "_custody", "unwatch"),
        # rev 12: a pin puts a custody's record back, only in `_pinned`
        ("client/manager.py", "_pinned", "restore"),
        # rev 13: a release puts back what was not cancelled, only in `_custody`
        ("client/manager.py", "_custody", "restore"),
    }


def test_the_registry_cannot_be_written_or_replaced_around_put() -> None:
    for name in (
        "__setitem__",
        "__delitem__",
        "pop",
        "popitem",
        "setdefault",
        "update",
    ):
        assert not hasattr(TaskRegistry, name), name
    manager = ClientManager()
    with pytest.raises(TypeError):
        manager._tasks[("s", "t")] = McpTaskRecord(server_name="s", task_id="t")  # type: ignore[index]
    # `self._tasks` is bound once, in __init__; nothing rebinds it to a dict.
    tree = ast.parse((SRC / "client" / "manager.py").read_text())
    binders = {
        func.name
        for func in _functions(tree)
        for node in ast.walk(func)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute) and target.attr == "_tasks"
    }
    assert binders == {"__init__"}


def test_no_downstream_notification_records_a_task() -> None:
    """Notifications are not a record path: `_handle_downstream_notification`
    acts only on the three `list_changed` methods, so `notifications/tasks/
    status` records nothing (it would be listed above if it did)."""
    manager = ClientManager()
    managed = _task_server(manager, {})
    managed.status.status = ServerStatusEnum.ONLINE
    handled = manager._handle_downstream_notification(
        "tasks", managed, "notifications/tasks/status"
    )
    assert handled is False and len(manager._tasks) == 0


# --- snapshot-then-await sites (rev 2, panel finding F001) -------------------------
#
# A site that reads tracked records and then awaits can find a record gone when
# it resumes: the cap evicted it (another caller recorded a task on that server,
# or the downstream answered `tasks/cancel` with a different task id), or a
# disconnect dropped it. Each site has a defined outcome for that, pinned below.
# Records are made in the order b, a: the active-task snapshot is sorted by id
# (a, b), so cancelling `a` first and recording one more task evicts `b`, the
# oldest unfinished record, while it is still waiting its turn. `b` carries a
# stored requestor_context, which its cancel must still send (rev 3: an
# evicted task is cancelled by its snapshot, not skipped).

B_CONTEXT = {"tenant": "t-b"}


def _teardown_manager(
    trigger: str,
) -> tuple[ClientManager, dict[tuple[str, str], Any], list[tuple[str, Any]]]:
    manager, replies = _manager("tasks")
    manager._tasks.per_server = 2
    manager._tasks.put(
        McpTaskRecord(
            server_name="tasks",
            task_id="b",
            status="working",
            requestor_context=B_CONTEXT,
        )
    )
    _put(manager._tasks, "tasks", "a", "working")
    sent: list[tuple[str, Any]] = []
    plain = manager._send_request

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        sent.append((method, params))
        if method == "tasks/cancel":
            task_id = params["taskId"]
            if trigger == "concurrent-record":
                # another caller's tasks_list lands while this cancel is in flight
                replies[("tasks", "tasks/list")] = {
                    "tasks": [{"taskId": f"new-{task_id}", "status": "working"}]
                }
                await asyncio.gather(manager.list_tasks("tasks"))
                return {"taskId": task_id, "status": "cancelled"}
            if trigger == "re-tracked-without-context":
                # rev 4 (round-3 F001): a concurrent tasks_list lists a new
                # task (evicting b) and then b itself, which is recorded again
                # with no requestor_context
                if task_id == "a":
                    replies[("tasks", "tasks/list")] = {
                        "tasks": [
                            {"taskId": "x", "status": "working"},
                            {"taskId": "b", "status": "working"},
                        ]
                    }
                    await manager.list_tasks("tasks")
                    re_tracked = manager.get_task_record("tasks", "b")
                    # rev 11: the teardown owes "b" but does not pin it, so
                    # "x" evicted it into the teardown's custody, and the
                    # listing re-tracked it; the re-track merges with what
                    # the custody holds, so the context is kept
                    assert re_tracked is not None
                    assert manager._tasks.held(("tasks", "b")) is not None
                    assert re_tracked.requestor_context == B_CONTEXT
                return {"taskId": task_id, "status": "cancelled"}
            # the downstream answers with a different task id
            return {"taskId": f"other-{task_id}", "status": "working"}
        return await plain(managed, method, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    return manager, replies, sent


TRIGGERS = ["concurrent-record", "foreign-task-id"]
#: For the sites that hold a context across an await (rev 4): also a record
#: evicted and then re-tracked, context-less, by a concurrent listing.
CONTEXT_TRIGGERS = [*TRIGGERS, "re-tracked-without-context"]


def _cancelled_ids(sent: list[tuple[str, Any]]) -> list[str]:
    return [params["taskId"] for method, params in sent if method == "tasks/cancel"]


def _b_cancel_carries_its_context(sent: list[tuple[str, Any]]) -> bool:
    return any(
        method == "tasks/cancel"
        and params["taskId"] == "b"
        and params["task"]["requestorContext"] == B_CONTEXT
        and params["force"] is True
        for method, params in sent
    )


@pytest.mark.parametrize("trigger", CONTEXT_TRIGGERS)
@pytest.mark.asyncio
async def test_forced_cancel_loop_survives_concurrent_records(trigger: str) -> None:
    """F001's falsifier, `ClientManager.disconnect_server(force=True)`: `b` is
    evicted while `a`'s cancel is in flight. On rev 1 the disconnect returned
    `(False, 0, 'Task not found: tasks::b')`; rev 2 skipped `b`. Like main,
    rev 3 returns `(True, 0, None)` and sends `tasks/cancel` for both: on a
    remote server, `b` would otherwise keep running after the disconnect."""
    manager, _replies, sent = _teardown_manager(trigger)
    result = await manager.disconnect_server("tasks", force=True)
    assert result == (True, 0, None), result
    assert _cancelled_ids(sent) == ["a", "b"]
    assert _b_cancel_carries_its_context(sent)
    assert "tasks" not in manager._clients and len(manager._tasks) == 0


@pytest.mark.parametrize("trigger", CONTEXT_TRIGGERS)
@pytest.mark.asyncio
async def test_cancel_active_tasks_cancels_a_record_evicted_mid_loop(
    trigger: str,
) -> None:
    manager, _replies, sent = _teardown_manager(trigger)
    cancelled, errors = await manager.cancel_active_tasks()
    assert (cancelled, errors) == (2, [])
    assert _cancelled_ids(sent) == ["a", "b"]
    assert _b_cancel_carries_its_context(sent)


@pytest.mark.parametrize("trigger", CONTEXT_TRIGGERS)
@pytest.mark.asyncio
async def test_restart_survives_a_record_evicted_mid_teardown(
    trigger: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _replies, sent = _teardown_manager(trigger)
    config = manager._clients["tasks"].config
    connected: list[str] = []

    async def connect(cfg: Any) -> list[str]:
        connected.append(cfg.name)
        return []

    monkeypatch.setattr(manager, "connect_server", connect)
    assert await manager.restart_server(config, force=True) == (True, 0, [])
    assert connected == ["tasks"]
    assert _cancelled_ids(sent) == ["a", "b"]
    assert _b_cancel_carries_its_context(sent)


@pytest.mark.parametrize("trigger", CONTEXT_TRIGGERS)
@pytest.mark.asyncio
async def test_gateway_disconnect_server_survives_a_record_evicted_mid_teardown(
    trigger: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _replies, sent = _teardown_manager(trigger)
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    monkeypatch.setattr(
        tools,
        "_resolve_lifecycle_config",
        lambda name, *, action, prior_status: (manager._clients[name].config, None),
    )
    out = await tools.disconnect_server({"server_name": "tasks", "force": True})
    assert out.ok, out.errors
    # the count is "active tasks no longer tracked" (before - after), as on main
    assert out.cancelled_task_count == 2
    assert _cancelled_ids(sent) == ["a", "b"]
    assert _b_cancel_carries_its_context(sent)


@pytest.mark.parametrize("trigger", CONTEXT_TRIGGERS)
@pytest.mark.asyncio
async def test_forced_refresh_survives_a_record_evicted_mid_teardown(
    trigger: str,
) -> None:
    manager, _replies, sent = _teardown_manager(trigger)
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.refresh({"force": True})
    assert out.mcp_tasks_seen == 2
    assert out.mcp_tasks_cancelled == 2
    assert _cancelled_ids(sent)[:2] == ["a", "b"]
    assert _b_cancel_carries_its_context(sent)
    assert not any("Task not found" in e for e in out.errors or []), out.errors


@pytest.mark.asyncio
async def test_forced_refresh_reports_a_server_it_could_not_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh loop's forced disconnect used to drop its result, so a server
    removed from config, or now policy-denied, stayed in `_clients` while
    refresh said ok. Its failure is now reported."""
    manager, _replies = _manager("tasks")

    async def failing(name: str, force: bool = False) -> tuple[bool, int, str | None]:
        return (False, 0, "Error closing transport")

    monkeypatch.setattr(manager, "disconnect_server", failing)
    out = await GatewayTools(
        client_manager=manager, policy_manager=PolicyManager()
    ).refresh({"force": True})
    assert out.ok is False
    assert out.errors == [
        "Server 'tasks' could not be disconnected: Error closing transport"
    ]


@pytest.mark.parametrize("trigger", CONTEXT_TRIGGERS)
@pytest.mark.asyncio
async def test_get_task_result_keeps_the_stored_context_if_evicted_mid_call(
    trigger: str,
) -> None:
    """`get_task_result` reads the stored `requestor_context`, awaits
    `tasks/result`, and (with no task in the reply) falls back to `tasks/get`.
    The fallback uses the context read at the start, even if the record was
    evicted meanwhile."""
    manager, replies = _manager("tasks")
    manager._tasks.per_server = 1
    context = {"tenant": "t-1"}
    manager._tasks.put(
        McpTaskRecord(
            server_name="tasks",
            task_id="t",
            status="working",
            requestor_context=context,
        )
    )
    sent: list[tuple[str, Any]] = []
    plain = manager._send_request

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        sent.append((method, params))
        if method == "tasks/result":
            if trigger == "concurrent-record":
                _put(manager._tasks, "tasks", "intruder", "working")
            else:
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id="other", status="working"),
                    requestor_context=None,
                    connection=None,
                )
            # rev 9: the request pinned "t", so this pressure cannot evict it
            assert manager.get_task_record("tasks", "t") is not None
            if trigger == "re-tracked-without-context":
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id="t", status="working"),
                    requestor_context=None,
                    connection=None,
                )
                re_tracked = manager.get_task_record("tasks", "t")
                assert (
                    re_tracked is not None and re_tracked.requestor_context == context
                )
            return {"content": []}
        return await plain(managed, method, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    replies[("tasks", "tasks/get")] = {"taskId": "t", "status": "working"}
    await manager.get_task_result("tasks", "t")
    assert [m for m, _ in sent] == ["tasks/result", "tasks/get"]
    assert all(p["task"]["requestorContext"] == context for _, p in sent)


@pytest.mark.parametrize("trigger", TRIGGERS)
@pytest.mark.asyncio
async def test_cancel_task_records_the_reply_if_evicted_mid_call(trigger: str) -> None:
    """`cancel_task` (the user-facing path, unchanged) reads the record, awaits
    `tasks/cancel`, then records the reply. A record evicted meanwhile is
    recorded again from the reply."""
    manager, replies = _manager("tasks")
    manager._tasks.per_server = 1
    _put(manager._tasks, "tasks", "t", "working")
    plain = manager._send_request

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        if method == "tasks/cancel":
            if trigger == "concurrent-record":
                _put(manager._tasks, "tasks", "intruder", "working")
            else:
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id="other", status="working"),
                    requestor_context=None,
                    connection=None,
                )
            # rev 9: the request pinned "t", so this pressure cannot evict it
            assert manager.get_task_record("tasks", "t") is not None
            return {"taskId": "t", "status": "cancelled"}
        return await plain(managed, method, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    ok, record, message = await manager.cancel_task("tasks", "t")
    assert ok and message == "Task cancelled"
    assert record is not None and record.status == "cancelled"
    assert manager.get_task_record("tasks", "t") is record


@pytest.mark.parametrize("trigger", TRIGGERS)
@pytest.mark.asyncio
async def test_get_task_records_the_reply_if_evicted_mid_call(trigger: str) -> None:
    manager, replies = _manager("tasks")
    manager._tasks.per_server = 1
    _put(manager._tasks, "tasks", "t", "working")
    plain = manager._send_request

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        if method == "tasks/get":
            if trigger == "concurrent-record":
                _put(manager._tasks, "tasks", "intruder", "working")
            else:
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id="other", status="working"),
                    requestor_context=None,
                    connection=None,
                )
            return {"taskId": "t", "status": "completed"}
        return await plain(managed, method, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    record = await manager.get_task("tasks", "t")
    assert record.status == "completed"
    assert manager.get_task_record("tasks", "t") is record


#: Every function in src/pmcp that reads tracked records and later awaits, with
#: the outcome it gives a record that is gone when it resumes, and its test.
SNAPSHOT_SITES: dict[str, str] = {
    # the forced teardowns: a gone record is still cancelled, never an error
    "client/manager.py:disconnect_server": "via _cancel_tracked_for_teardown",
    "client/manager.py:cancel_active_tasks": "via _cancel_tracked_for_teardown",
    "client/manager.py:_cancel_tracked_for_teardown": "re-reads each; gone = cancel from custody",
    # the user-facing calls: read before the await, record the reply after
    "client/manager.py:get_task": "context read before the await",
    "client/manager.py:get_task_result_with_task": "context read once, before any await",
    "client/manager.py:cancel_task": "record read before the await",
    # the handlers: counts, or a read with no await before its use
    "tools/handlers.py:refresh": "counts; cancels via cancel_active_tasks",
    "tools/handlers.py:disconnect_server": "counts (before - after)",
    "tools/handlers.py:_restart_resolved_server": "counts (before - after)",
    # rev 4, N2b: found through the sync helpers `_record_task` and
    # `_remove_server_indexes`; each writes, and holds no record across an await
    "client/manager.py:list_tasks": "records then dumps each at once",
    "client/manager.py:_connect_stdio": "drops the server's records, holds none",
    "client/manager.py:_connect_remote_stream": "drops the server's records",
    "client/manager.py:_adopt_process_locked": "drops the server's records, holds none",
    # rev 9: the pin decorator. It pins (a write to the registry's pin
    # counts) and then awaits the request it wraps; it holds no record.
    "client/manager.py:_pins_task": "pins, then awaits; holds no record",
    "client/manager.py:pinned": "pins, then awaits; holds no record",
}

_ACCESSORS = {"get_active_tasks", "get_tracked_tasks", "get_task_record"}


def _reads_records(n: ast.AST, readers: Collection[str] = ()) -> bool:
    """Any *reference* to a way of reading tracked records, not only a direct
    call (rev 3, N2): `x.get_active_tasks` as an attribute (called, aliased,
    or passed as a bound method), the bare name, `getattr(x,
    "get_active_tasks")`, and `_tasks` -- and (rev 4, N2b) any reference to
    one of `readers`, the sync helpers that read."""
    names = _ACCESSORS | {"_tasks"} | set(readers)
    return (
        (isinstance(n, ast.Attribute) and n.attr in names)
        or (isinstance(n, ast.Name) and n.id in names)
        or (isinstance(n, ast.Constant) and n.value in names)
    )


def _sync_readers(trees: list[ast.AST]) -> set[str]:
    """Names of the functions whose reads run *inline* in their caller --
    before the caller's next await -- closed to a fixpoint (rev 4, N2b;
    rev 5, N2c):
    - a plain `def` that reads, directly or through another inline reader;
    - an `async def` that reads and never awaits. Awaiting it runs its body to
      completion without suspending, exactly like calling a sync helper. The
      round-4 seat measured this dodge.

    An `async def` that suspends is not included: its reads are scanned in
    its own body as a site of its own. Following its *returned* value into
    every caller by name was measured and rejected: by name-matching it flags
    28 to 93 functions across `src/pmcp` (`refresh`, `call_tool`, `main`
    collide), which would make the table meaningless. `__init__` binds
    `_tasks`; it does not read it."""
    readers: set[str] = set()
    while True:
        found = {
            func.name
            for tree in trees
            for func in _functions(tree)
            if func.name != "__init__"
            and (
                isinstance(func, ast.FunctionDef)
                or not any(isinstance(n, ast.Await) for n in ast.walk(func))
            )
            and any(_reads_records(n, readers) for n in ast.walk(func))
        }
        if found <= readers:
            return readers
        readers |= found


def _snapshot_sites(tree: ast.AST, readers: set[str]) -> set[str]:
    """Functions that read tracked records -- directly, or through a call to
    one of `readers` -- before one of their awaits ends."""
    found = set()
    for func in _functions(tree):
        if func.name == "__init__":
            continue
        reads = [
            (n.lineno, n.col_offset)
            for n in ast.walk(func)
            if _reads_records(n, readers)
        ]
        # where each await *finishes*: a read inside an awaited expression's
        # arguments still happens before that await resumes
        awaits = [
            (n.end_lineno or n.lineno, n.end_col_offset or 0)
            for n in ast.walk(func)
            if isinstance(n, ast.Await)
        ]
        # in a loop, a read can come before the *next* iteration's await
        looped = any(
            any(_reads_records(n, readers) for n in ast.walk(loop))
            and any(isinstance(n, ast.Await) for n in ast.walk(loop))
            for loop in ast.walk(func)
            if isinstance(loop, ast.For | ast.AsyncFor | ast.While)
        )
        if reads and awaits and (min(reads) < max(awaits) or looped):
            found.add(func.name)
    return found


def test_every_snapshot_then_await_site_is_classified() -> None:
    """A function that reads tracked records (any reference to an accessor,
    or `_tasks`) and then awaits is a site where a record can vanish in
    between. Each one must be in SNAPSHOT_SITES, with its defined outcome and
    a test above."""
    trees = {path: ast.parse(path.read_text()) for path in SRC.rglob("*.py")}
    readers = _sync_readers(list(trees.values()))
    found = {
        f"{path.relative_to(SRC)}:{name}"
        for path, tree in trees.items()
        for name in _snapshot_sites(tree, readers)
    }
    assert found == set(SNAPSHOT_SITES)


def test_the_inline_readers_in_src_are_the_expected_six() -> None:
    """The fixpoint over src/pmcp: the accessors themselves, and the three sync
    helpers that touch the registry. No `async def` in src reads without
    awaiting today; the fixtures pin that one would be caught."""
    trees = [ast.parse(path.read_text()) for path in SRC.rglob("*.py")]
    assert _sync_readers(trees) == {
        "get_task_record",
        "get_tracked_tasks",
        "get_active_tasks",
        "_record_task",
        "_remove_server_indexes",
        "_pinned",  # rev 9: takes and releases pins on `_tasks`
        "_pins_task",  # rev 9: refers to `_pinned`
        "_custody",  # rev 11: registers and releases a custody on `_tasks`
        # Consiliency/pmcp#324's bookkeeping helpers, which drop a name's
        # records through `_remove_server_indexes`
        "_forget_client",
        "_forget_disconnected",
    }


_ALIAS_FORMS = {
    "direct": "snapshot = self._cm.get_active_tasks()",
    "aliased-method": "active = self._cm.get_active_tasks; snapshot = active()",
    "bound-method-passed": "snapshot = list(map(str, self._cm.get_tracked_tasks()))",
    "bound-method-as-value": "snapshot = helper(self._cm.get_task_record)",
    "getattr-by-name": 'snapshot = getattr(self._cm, "get_active_tasks")()',
    "bare-name": "snapshot = get_active_tasks()",
    "the-registry": "snapshot = list(self._cm._tasks)",
    # rev 4, N2b: the seat's shape, a sync helper that reads, and a caller
    # that names no read method before it awaits
    "sync-helper": "snapshot = self._active_snapshot()",
    "sync-helper-of-a-helper": "snapshot = self._snapshot_via_helper()",
    # rev 5, N2c: the round-4 seat's shape, an async helper that reads and
    # never awaits
    "await-free-async-helper": "snapshot = await self._async_snapshot()",
}

_HELPERS = (
    "    def _active_snapshot(self):\n"
    "        return self._client_manager.get_active_tasks()\n"
    "    def _snapshot_via_helper(self):\n"
    "        return list(self._active_snapshot())\n"
    "    async def _async_snapshot(self):\n"
    "        return self._client_manager.get_active_tasks()\n"
)


@pytest.mark.parametrize("form", list(_ALIAS_FORMS))
def test_the_snapshot_scan_sees_every_way_of_reading(form: str) -> None:
    """N2 (rev 3): the seat's alias (`aliased-method`) passed rev 2's
    call-only scan. Each form reads records and then awaits; each must be
    found, and the same function without the read must not be."""
    source = (
        "class GatewayTools:\n"
        f"{_HELPERS}"
        "    async def handler(self):\n"
        f"        {_ALIAS_FORMS[form]}\n"
        "        await asyncio.sleep(0)\n"
        "        return snapshot\n"
    )
    tree = ast.parse(source)
    assert _snapshot_sites(tree, _sync_readers([tree])) == {"handler"}
    clean = ast.parse(source.replace(_ALIAS_FORMS[form], "snapshot = []"))
    assert _snapshot_sites(clean, _sync_readers([clean])) == set()


def test_every_tasks_cancel_goes_through_the_one_sender() -> None:
    """One path sends `tasks/cancel`: `_send_task_cancel`. It is called by
    `cancel_task` (the user-facing answer, "not found" for an untracked id)
    and by the teardown helper (which cancels an evicted task from its
    custody, rev 11). The teardowns do not call `cancel_task`, and nothing else
    names the method."""
    assert _where("_send_task_cancel") == {
        ("client/manager.py", "cancel_task"),
        ("client/manager.py", "_cancel_tracked_for_teardown"),
    }
    assert _where("cancel_task") == {("tools/handlers.py", "tasks_cancel")}

    def names_the_method(node: ast.AST, _parent: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and node.value == "tasks/cancel":
            return "tasks/cancel"
        return None

    assert _owned(names_the_method) == {
        ("client/manager.py", "_send_task_cancel", "tasks/cancel")
    }


@pytest.mark.asyncio
async def test_a_teardown_counts_a_task_finished_meanwhile_and_sends_nothing() -> None:
    """A snapshot task that a concurrent `tasks_get` saw finish is counted, as
    `cancel_task`'s "already terminal", and no cancel is sent for it."""
    manager, _replies, sent = _teardown_manager("foreign-task-id")
    manager._tasks.per_server = 10  # nothing is evicted here
    plain = manager._send_request

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        if method == "tasks/cancel" and params["taskId"] == "a":
            _put(manager._tasks, "tasks", "b", "completed")
        return await plain(managed, method, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    assert await manager.cancel_active_tasks() == (2, [])
    assert _cancelled_ids(sent) == ["a"]


@pytest.mark.parametrize("state", ["removed", "reconnecting", "tracked-reconnecting"])
@pytest.mark.asyncio
async def test_a_teardown_skips_a_task_whose_server_has_no_live_connection(
    state: str,
) -> None:
    """While `a`'s cancel is in flight, `b`'s server loses its connection:
    - removed: a concurrent disconnect dropped the client and its records;
    - reconnecting: the client is listed again but not yet connected (here,
      no write stream), and `b` was evicted;
    - tracked-reconnecting: the same, with `b` still tracked.

    There is nothing to send `b`'s cancel on. It is skipped, not counted, not
    an error, and the loop does not raise "not connected" (rev 4)."""
    manager, _replies, sent = _teardown_manager("foreign-task-id")
    if state == "tracked-reconnecting":
        manager._tasks.per_server = 10  # b stays tracked
    plain = manager._send_request

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        if method == "tasks/cancel" and params["taskId"] == "a":
            if state == "removed":
                manager._clients.pop("tasks")
                manager._tasks.drop_server("tasks")
            else:
                manager._clients["tasks"].write_stream = None
        return await plain(managed, method, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    assert await manager.cancel_active_tasks() == (1, [])
    assert _cancelled_ids(sent) == ["a"]
    if state == "tracked-reconnecting":
        # still tracked, so gateway.tasks_get/tasks_cancel can reach it later
        assert manager.get_task_record("tasks", "b") is not None


@pytest.mark.asyncio
async def test_teardown_cancel_keeps_the_snapshot_context() -> None:
    """The round-3 claude seat's falsifier, as written: both tasks were
    recorded with a context; while `a`'s cancel awaits, a concurrent listing
    records `x` (evicting `b`) and then `b` again without a context. `b`'s
    cancel must still carry the context the snapshot held, as main's does."""
    context = {"tenant": "t-1"}
    manager = ClientManager()
    manager._clients = {"tasks": _task_server(manager, {})}
    manager._tasks.per_server = 2
    for task_id in ("b", "a"):  # the snapshot is sorted a, b; b is the oldest
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=context,
            connection=None,
        )
    sent: dict[str, Any] = {}

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        assert method == "tasks/cancel"
        sent[params["taskId"]] = params
        if params["taskId"] == "a":
            for listed in ("x", "b"):
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id=listed, status="working"),
                    requestor_context=None,
                    connection=None,
                )
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    await manager.cancel_active_tasks("tasks")
    assert sorted(sent) == ["a", "b"]
    assert sent["b"]["task"]["requestorContext"] == context, sent["b"]


#: Every function in src/pmcp that reads a *stored* record's requestor_context
#: (not the caller's own input), and when it reads it (rev 4). The rule: a
#: context read before an await is used as read, and the one site that holds a
#: record across awaits prefers the current record's context, else the held one.
STORED_CONTEXT_READS: dict[str, str] = {
    "client/manager.py:_record_task": "merges from the existing record; sync",
    "client/manager.py:get_task": "read before its only await",
    "client/manager.py:get_task_result_with_task": "read once, before any await",
    "client/manager.py:cancel_task": "read before its only await",
    # rev 11: the record it is about to cancel -- the current one, else the
    # custody's -- read under that entry's pin, right before its only await
    "client/manager.py:_cancel_tracked_for_teardown": "current, else custody's",
}


def test_every_read_of_a_stored_context_is_classified() -> None:
    def stored_context(node: ast.AST, _parent: ast.AST | None) -> str | None:
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "requestor_context"
            and not (
                isinstance(node.value, ast.Name)
                and node.value.id in {"parsed", "parsed_task"}
            )
        ):
            return "read"
        return None

    found = {f"{path}:{func}" for path, func, _ in _owned(stored_context)}
    assert found == set(STORED_CONTEXT_READS)


# --- rev 5 (round-4 F001): every record write carries the context its caller held


#: Every call, in src/pmcp, that passes a context on its way into a record, by
#: (callee, caller, the value passed). Derived from the AST and compared whole:
#: a new call, a changed value or a dropped one fails until it is listed here
#: with its reason. `_record_task`'s `requestor_context` and the
#: `stored_context` of the three helpers that lead to it are keyword-only and
#: have no default, so a call cannot leave them out.
CONTEXT_FLOW: dict[tuple[str, str, str], str] = {
    # rev 9: one context. A caller-supplied one wins; a reply supplies none,
    # so the existing record's -- pinned by the request, so still there -- is
    # kept. Rev 8's `fallback_context` and rev 5's `stored_context` chain are
    # gone.
    ("_record_task", "call_tool_with_task", "requestor_context=requestor_context"): (
        "the context the caller supplied with this call"
    ),
    ("_record_task", "list_tasks", "requestor_context=None"): (
        "a listing does not attribute a context; a tracked task keeps its own"
    ),
    ("_record_task", "_fetch_task", "requestor_context=None"): (
        "a reply supplies no context; the pinned record keeps its own"
    ),
    ("_record_task", "get_task_result_with_task", "requestor_context=None"): (
        "a reply supplies no context; the pinned record keeps its own"
    ),
    ("_record_task", "_send_task_cancel", "requestor_context=None"): (
        "a reply supplies no context; the pinned record keeps its own"
    ),
}

_CONTEXT_PARAMETERS: dict[str, tuple[str, ...]] = {
    "_record_task": ("requestor_context",),
}


def test_every_record_write_passes_the_context_its_caller_held() -> None:
    """Round-4 F001, fixed at the choke point rather than site by site: no
    call that leads to `_record_task` can leave the context out, and every
    value passed is listed with its reason."""
    import inspect

    for callee, parameters in _CONTEXT_PARAMETERS.items():
        signature = inspect.signature(getattr(ClientManager, callee))
        for parameter in parameters:
            param = signature.parameters[parameter]
            assert param.kind is inspect.Parameter.KEYWORD_ONLY, callee
            assert param.default is inspect.Parameter.empty, callee

    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for func in _functions(tree):
            for node in ast.walk(func):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _CONTEXT_PARAMETERS
                ):
                    continue
                inner = [f for f in _functions(func) if f is not func]
                if any(node in set(ast.walk(f)) for f in inner):
                    continue  # counted in the innermost function
                callee = node.func.attr
                for parameter in _CONTEXT_PARAMETERS[callee]:
                    passed = [k.value for k in node.keywords if k.arg == parameter]
                    assert len(passed) == 1, (callee, func.name, ast.unparse(node))
                    found.add(
                        (callee, func.name, f"{parameter}={ast.unparse(passed[0])}")
                    )
    assert found == set(CONTEXT_FLOW)


CTX = {"tenant": "t-1"}


async def _flow_get(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/get")] = {"taskId": "t", "status": "working"}
    await m.get_task("tasks", "t")


async def _flow_result(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/result")] = {"task": {"taskId": "t", "status": "working"}}
    await m.get_task_result("tasks", "t")


async def _flow_result_fallback(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/result")] = {"content": []}
    r[("tasks", "tasks/get")] = {"taskId": "t", "status": "working"}
    await m.get_task_result("tasks", "t")


async def _flow_cancel(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "working"}
    await m.cancel_task("tasks", "t")


async def _flow_teardown(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "working"}
    await m.cancel_active_tasks("tasks")


#: Every path that records a reply after an await, and its first request.
CONTEXT_PATHS = {
    "tasks/get": (_flow_get, "tasks/get"),
    "tasks/result": (_flow_result, "tasks/result"),
    "tasks/result->tasks/get": (_flow_result_fallback, "tasks/result"),
    "tasks/cancel": (_flow_cancel, "tasks/cancel"),
    "teardown tasks/cancel": (_flow_teardown, "tasks/cancel"),
}


@pytest.mark.parametrize("path", list(CONTEXT_PATHS))
@pytest.mark.asyncio
async def test_a_reply_recorded_after_an_eviction_keeps_the_held_context(
    path: str,
) -> None:
    """Round-4 F001 on every path: `t` is tracked with a context; while the
    path's first request awaits, another caller records two tasks, which
    evicts `t`. The reply is recorded with the context pmcp held, as on main,
    where `t` is never evicted. The reply here keeps `t` unfinished, so a
    later forced teardown must still send that context."""
    manager, replies = _manager("tasks")
    manager._tasks.per_server = 2
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=None,
    )
    drive, first = CONTEXT_PATHS[path]
    plain = manager._send_request
    sent: list[tuple[str, Any]] = []
    evicted = False

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        nonlocal evicted
        sent.append((method, params))
        if method == first and not evicted:
            evicted = True
            for other in ("x", "y"):
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id=other, status="working"),
                    requestor_context=None,
                    connection=None,
                )
            # rev 9: the request pinned "t", so this pressure cannot evict it
            assert manager.get_task_record("tasks", "t") is not None
        return await plain(managed, method, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    await drive(manager, replies)
    record = manager.get_task_record("tasks", "t")
    assert record is not None and record.requestor_context == CTX, record

    # and the next cancel of `t` carries it (the round-4 seat's falsifier)
    sent.clear()
    replies[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "cancelled"}
    manager._tasks.per_server = 10
    await manager.cancel_task("tasks", "t")
    cancels = [p for m, p in sent if m == "tasks/cancel" and p["taskId"] == "t"]
    assert cancels and cancels[0]["task"]["requestorContext"] == CTX, cancels


@pytest.mark.asyncio
async def test_get_task_keeps_the_context_it_held_if_evicted_mid_call() -> None:
    """The round-4 claude seat's falsifier, as written."""
    manager = ClientManager()
    manager._clients = {"tasks": _task_server(manager, {})}
    manager._tasks.per_server = 2
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=None,
    )
    sent: dict[str, Any] = {}

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        if method == "tasks/get":
            for other in ("x", "y"):
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id=other, status="working"),
                    requestor_context=None,
                    connection=None,
                )
            return {"taskId": "t", "status": "working"}
        assert method == "tasks/cancel"
        sent[params["taskId"]] = params
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    await manager.get_task("tasks", "t")
    assert manager.get_task_record("tasks", "t") is not None
    await manager.cancel_active_tasks("tasks")
    assert sent["t"]["task"]["requestorContext"] == CTX, sent["t"]


@pytest.mark.parametrize("method", ["tasks/get", "tasks/cancel"])
@pytest.mark.asyncio
async def test_a_reply_naming_another_task_does_not_take_this_tasks_context(
    method: str,
) -> None:
    """A downstream may answer about a different task. That task is recorded
    with no context: `t`'s context is not its."""
    manager, replies = _manager("tasks")
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=None,
    )
    replies[("tasks", method)] = {"taskId": "other", "status": "working"}
    if method == "tasks/get":
        await manager.get_task("tasks", "t")
    else:
        await manager.cancel_task("tasks", "t")
    other = manager.get_task_record("tasks", "other")
    assert other is not None and other.requestor_context is None
    kept = manager.get_task_record("tasks", "t")
    assert kept is not None and kept.requestor_context == CTX


# --- rev 5 (codex F001): task state is bound to the connection it came from -----


def _connection(name: str, url: str) -> ManagedClient:
    return ManagedClient(
        config=ResolvedServerConfig(
            name=name,
            source="custom",
            config=RemoteMcpServerConfig(type="streamable-http", url=url),
        ),
        is_remote=True,
        write_stream=object(),
        status=ServerStatus(
            name=name,
            status=ServerStatusEnum.ONLINE,
            tool_count=0,
            server_capabilities={"tasks": {}},
        ),
    )


def test_teardown_does_not_send_old_context_to_a_replacement_server() -> None:
    """The round-4 codex seat's falsifier (keyword arguments added for rev 5's
    required parameters). While `a`'s cancel awaits, a reconnect cleans up
    `beta` and registers a replacement connection, possibly another tenant.
    `b`'s id and `{"tenant": "old-tenant"}` must not go to it. Main sends
    nothing to the replacement; rev 4 sent `b`'s cancel with the old
    tenant."""

    async def scenario() -> list[Any]:
        manager = ClientManager()
        first = _connection("alpha", "https://alpha.example/mcp")
        old = _connection("beta", "https://old-tenant.example/mcp")
        replacement = _connection("beta", "https://new-tenant.example/mcp")
        manager._clients.update(alpha=first, beta=old)
        manager._record_task(
            "alpha",
            McpTaskInfo(task_id="a", status="working"),
            requestor_context=None,
            connection=first,
        )
        manager._record_task(
            "beta",
            McpTaskInfo(task_id="b", status="working"),
            requestor_context={"tenant": "old-tenant"},
            connection=old,
        )
        replacement_requests: list[Any] = []

        async def send(managed: Any, method: str, params: Any, **_: Any) -> Any:
            if managed is first:
                # the same cleanup `_connect_remote_stream` runs on replacement
                await manager._cleanup_client("beta", old)
                manager._clients["beta"] = replacement
            if managed is replacement:
                replacement_requests.append((method, params))
            return {"taskId": params["taskId"], "status": "cancelled"}

        manager._send_request = send  # type: ignore[method-assign]
        assert await manager.cancel_active_tasks() == (1, [])
        return replacement_requests

    assert asyncio.run(scenario()) == []


def _replaced(manager: ClientManager) -> ManagedClient:
    """Register a new connection for "tasks", the way a reconnect does, but
    keep the old records (a record that outlived its connection)."""
    replacement = _task_server(manager, {})
    manager._clients["tasks"] = replacement
    return replacement


#: Every send of stored task state, with how a record from a previous
#: connection is refused there.
BOUND_SENDS = {
    "tasks/get": lambda m: m.get_task("tasks", "t"),
    "tasks/result": lambda m: m.get_task_result("tasks", "t"),
    "tasks/cancel": lambda m: m.cancel_task("tasks", "t"),
}


@pytest.mark.parametrize("path", list(BOUND_SENDS))
@pytest.mark.asyncio
async def test_a_record_from_a_previous_connection_is_never_sent_on_the_new_one(
    path: str,
) -> None:
    """The funnel: `_task_client(server, bound_to=record)` refuses a record
    whose connection is no longer the server's. Nothing reaches the
    replacement, and the caller is told why."""
    manager, replies = _manager("tasks")
    old = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=old,
    )
    _replaced(manager)
    sent: list[Any] = []

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        sent.append(method)
        return {"taskId": "t", "status": "working"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    with pytest.raises(RuntimeError, match="was reconnected"):
        await BOUND_SENDS[path](manager)
    assert sent == []


@pytest.mark.asyncio
async def test_a_teardown_skips_a_snapshot_entry_from_a_previous_connection() -> None:
    manager, _replies = _manager("tasks")
    old = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=old,
    )
    _replaced(manager)
    sent: list[Any] = []

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        sent.append(method)
        return {}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    assert await manager.cancel_active_tasks() == (0, [])
    assert sent == []


async def _rec_call_tool(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tools/call")] = {"task": {"taskId": "t", "status": "working"}}
    await m.call_tool("tasks::run", {}, task={})


async def _rec_list(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/list")] = {"tasks": [{"taskId": "t", "status": "working"}]}
    await m.list_tasks("tasks")


async def _rec_get(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/get")] = {"taskId": "t", "status": "working"}
    await m.get_task("tasks", "t")


async def _rec_result(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/result")] = {"task": {"taskId": "t", "status": "working"}}
    await m.get_task_result("tasks", "t")


async def _rec_cancel(m: ClientManager, r: dict[Any, Any]) -> None:
    m._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=m._clients["tasks"],
    )
    r[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "working"}
    await m.cancel_task("tasks", "t")


#: Every record path, driven so that the connection is replaced while its
#: request is in flight.
RECORD_PATHS_ACROSS_A_RECONNECT = {
    "tools/call": (_rec_call_tool, "tools/call"),
    "tasks/list": (_rec_list, "tasks/list"),
    "tasks/get": (_rec_get, "tasks/get"),
    "tasks/result": (_rec_result, "tasks/result"),
    "tasks/cancel": (_rec_cancel, "tasks/cancel"),
}


@pytest.mark.parametrize("path", list(RECORD_PATHS_ACROSS_A_RECONNECT))
@pytest.mark.asyncio
async def test_a_reply_from_a_replaced_connection_is_not_recorded(path: str) -> None:
    """The connection is replaced while the request is in flight (the old
    connection's records are dropped, as every replacement path does). The
    reply came from the old connection: it must not become state of the new
    one."""
    manager, replies = _manager("tasks")
    drive, method = RECORD_PATHS_ACROSS_A_RECONNECT[path]
    plain = manager._send_request

    async def send(managed: ManagedClient, m: str, params: Any, **kw: Any) -> Any:
        if m == method:
            manager._remove_server_indexes("tasks")
            _replaced(manager)
        return await plain(managed, m, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    await drive(manager, replies)
    assert manager.get_task_record("tasks", "t") is None


@pytest.mark.asyncio
async def test_invoke_redacts_a_task_reply_by_default_even_when_not_recorded() -> None:
    """Rev 5 made a reply from a replaced connection unrecorded. `gateway.invoke`
    chose its default redaction by "is the record there", so it would have
    stopped redacting; it now redacts any task reply by default."""
    manager, replies = _manager("tasks")
    secret = "sk-abcdef1234567890abcdef"
    plain = manager._send_request
    replies[("tasks", "tools/call")] = {
        "task": {"taskId": "fresh", "status": "completed"},
        "content": [{"type": "text", "text": secret}],
    }

    async def send(managed: ManagedClient, m: str, params: Any, **kw: Any) -> Any:
        if m == "tools/call":
            manager._remove_server_indexes("tasks")
            replacement = _replaced(manager)
            replacement.status.status = ServerStatusEnum.ONLINE
        return await plain(managed, m, params, **kw)

    manager._send_request = send  # type: ignore[method-assign, assignment]
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.invoke({"tool_id": "tasks::run", "arguments": {}, "task": {}})
    assert out.ok, out.errors
    assert manager.get_task_record("tasks", "fresh") is None
    assert secret not in out.model_dump_json()


#: Every `tasks/*` request in src/pmcp, and the stored record it is bound to.
#: Each obtains its connection from `_task_client(server, bound_to=...)` in the
#: same function. `None` is for a request built from no stored task state.
TASK_SENDS: dict[tuple[str, str], str] = {
    ("list_tasks", "tasks/list"): "None",
    ("_fetch_task", "tasks/get"): "connection",  # rev 7: the caller's capture
    ("get_task_result_with_task", "tasks/result"): "record",
    ("_send_task_cancel", "tasks/cancel"): "bound_to",
}

#: Every other call that passes a binding on, with its value.
BINDINGS: dict[tuple[str, str, str], str] = {
    # rev 7: every resolution of a request's connection, each once at entry
    ("_task_client", "get_task", "record"): "its one resolution, at entry",
    ("_task_client", "get_task_result_with_task", "record"): (
        "its one resolution, at entry"
    ),
    ("_task_client", "list_tasks", "None"): "per server, before that server's await",
    ("_task_client", "_send_task_cancel", "bound_to"): "its one resolution, at entry",
    ("_fetch_task", "get_task", "managed"): "the connection it resolved at entry",
    ("_fetch_task", "get_task_result_with_task", "managed"): (
        "the connection it resolved at entry (rev 7: never the record, which "
        "may be gone)"
    ),
    ("_send_task_cancel", "cancel_task", "record"): "the record it read",
    ("_send_task_cancel", "_cancel_tracked_for_teardown", "snapped"): (
        "the snapshot entry's connection"
    ),
    ("_task_client_unavailable", "_task_client", "bound_to"): "its parameter",
    ("_task_client_unavailable", "_cancel_tracked_for_teardown", "snapped"): (
        "checked before anything else in the loop"
    ),
    ("_record_task", "call_tool_with_task", "managed"): "the connection it sent on",
    ("_record_task", "list_tasks", "managed"): "the connection it sent on",
    ("_record_task", "_fetch_task", "managed"): "the connection it sent on",
    ("_record_task", "get_task_result_with_task", "managed"): (
        "the connection it sent on"
    ),
    ("_record_task", "_send_task_cancel", "managed"): "the connection it sent on",
}

_BINDING_PARAMETERS = {
    "_task_client": "bound_to",
    "_task_client_unavailable": "bound_to",
    "_fetch_task": "connection",
    "_send_task_cancel": "bound_to",
    "_record_task": "connection",
}


def test_every_task_request_is_bound_to_the_connection_its_state_came_from() -> None:
    """Codex F001, as one mechanism: every `tasks/*` request goes through
    `_task_client(server, bound_to=...)`, every record is stamped with the
    connection it came from, and every binding parameter is keyword-only with
    no default. Derived from the AST and compared whole."""
    import inspect

    for callee, parameter in _BINDING_PARAMETERS.items():
        param = inspect.signature(getattr(ClientManager, callee)).parameters[parameter]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, callee
        assert param.default is inspect.Parameter.empty, callee

    sends: dict[tuple[str, str], str] = {}
    bindings = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        by_func: dict[ast.AST, list[ast.Call]] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                owner: ast.AST | None = parents.get(n)
                while owner is not None and not isinstance(
                    owner, ast.FunctionDef | ast.AsyncFunctionDef
                ):
                    owner = parents.get(owner)
                if owner is not None:
                    by_func.setdefault(owner, []).append(n)
        for func, calls in by_func.items():
            name = func.name  # type: ignore[attr-defined]
            clients = {
                ast.unparse(k.value)
                for n in calls
                if n.func.attr == "_task_client"  # type: ignore[attr-defined]
                for k in n.keywords
                if k.arg == "bound_to"
            }
            # rev 7: a function that takes its connection as a parameter
            # resolves nothing itself
            if not clients and any(
                a.arg == "connection"
                for a in func.args.kwonlyargs  # type: ignore[attr-defined]
            ):
                clients = {"connection"}
            for n in calls:
                attr = n.func.attr  # type: ignore[attr-defined]
                if (
                    attr == "_send_request"
                    and len(n.args) >= 2
                    and isinstance(n.args[1], ast.Constant)
                    and str(n.args[1].value).startswith("tasks/")
                ):
                    assert len(clients) == 1, (name, clients)
                    sends[(name, n.args[1].value)] = next(iter(clients))
                if attr in _BINDING_PARAMETERS:
                    passed = [
                        k.value
                        for k in n.keywords
                        if k.arg == _BINDING_PARAMETERS[attr]
                    ]
                    assert len(passed) == 1, (attr, name)
                    bindings.add((attr, name, ast.unparse(passed[0])))
    assert sends == TASK_SENDS
    assert bindings == set(BINDINGS)


#: Every write to `ClientManager._clients` in src/pmcp, by any means: item
#: assignment, `update`, `setdefault`, `__setitem__`, `pop`, `clear`, and
#: binding the attribute itself (rev 6). Derived and compared whole.
CLIENT_WRITES = {
    ("__init__", "bind"),
    ("_connect_stdio", "register"),
    ("_connect_remote_stream", "register"),
    # Consiliency/pmcp#324 (on main since this plan's base) moved these writes
    # into helpers; each is classified where it now lives
    ("_adopt_process_locked", "register"),
    ("_drop_client", "pop"),  # a failed connect forgets its own client only
    ("_forget_client", "pop"),  # `_cleanup_client`'s bookkeeping
    ("_forget_disconnected", "pop"),  # `disconnect_server`'s bookkeeping
    ("_disconnect_all_unlocked", "clear"),
    ("abandon_all_now", "clear"),  # the process is exiting (pmcp#324)
}
_REGISTERING = {"update", "setdefault", "__setitem__"}


def _client_writes(tree: ast.AST) -> set[tuple[str, str, int]]:
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def owner(node: ast.AST) -> str:
        cur: ast.AST | None = node
        while cur is not None and not isinstance(
            cur, ast.FunctionDef | ast.AsyncFunctionDef
        ):
            cur = parents.get(cur)
        return cur.name if cur is not None else "<module>"

    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "_clients"):
            continue
        parent = parents.get(node)
        kind = None
        if isinstance(parent, ast.Subscript) and isinstance(
            parent.ctx, ast.Store | ast.Del
        ):
            kind = "register" if isinstance(parent.ctx, ast.Store) else "pop"
        elif isinstance(node.ctx, ast.Store):
            kind = "bind"
        elif isinstance(parent, ast.Attribute) and isinstance(
            parents.get(parent), ast.Call
        ):
            if parent.attr in _REGISTERING:
                kind = "register"
            elif parent.attr in {"pop", "popitem", "clear", "__delitem__"}:
                kind = parent.attr if parent.attr != "popitem" else "pop"
        if kind is not None:
            found.add((owner(node), kind, node.lineno))
    return found


def _is_drop(node: ast.AST) -> bool:
    """`_cleanup_client(...)`, or `_remove_server_indexes(...)` that does not
    pass `drop_tasks=False`: a call that removes the name's task records."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr == "_cleanup_client"
            or (
                node.func.attr == "_remove_server_indexes"
                and not any(
                    k.arg == "drop_tasks"
                    and isinstance(k.value, ast.Constant)
                    and k.value.value is False
                    for k in node.keywords
                )
            )
        )
    )


def _block_drops(stmts: list[ast.stmt]) -> bool:
    """Whether every path through `stmts` drops: a plain statement holding a
    drop call, an `if` whose two branches both drop, or a `try`/`with` whose
    body drops. A loop may not run, so it never counts."""
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            if _block_drops(stmt.body) and _block_drops(stmt.orelse):
                return True
        elif isinstance(stmt, ast.Try | ast.With | ast.AsyncWith):
            if _block_drops(stmt.body):
                return True
        elif not isinstance(stmt, ast.For | ast.AsyncFor | ast.While):
            if any(_is_drop(n) for n in ast.walk(stmt)):
                return True
    return False


def _dropped_before(stmts: list[ast.stmt], target: ast.AST, dropped: bool) -> bool:
    """Whether every path to `target` passes a drop first (rev 6: by
    dominance over the statement structure, not by line order, so a drop on
    one branch only does not count)."""
    for stmt in stmts:
        if target in set(ast.walk(stmt)):
            for field in ("body", "orelse", "finalbody", "handlers"):
                for block in [getattr(stmt, field, None)]:
                    if not isinstance(block, list):
                        continue
                    stmts_in = [b for b in block if isinstance(b, ast.stmt)] or [
                        s for h in block for s in getattr(h, "body", [])
                    ]
                    if any(target in set(ast.walk(b)) for b in stmts_in):
                        return _dropped_before(stmts_in, target, dropped)
            return dropped
        dropped = dropped or _block_drops([stmt])
    return dropped


def test_every_connection_replacement_drops_the_names_records_first() -> None:
    """Every function that registers a connection, by any means, drops the
    name's records first on every path: `_cleanup_client`, or
    `_remove_server_indexes` without `drop_tasks=False`. So no record from an
    old connection survives into the new one; the binding above covers a
    reply that arrives later."""
    writes: set[tuple[str, str]] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        found = _client_writes(tree)
        writes |= {(func, kind) for func, kind, _ in found}
        for func in _functions(tree):
            for node in ast.walk(func):
                if not (isinstance(node, ast.Attribute) and node.attr == "_clients"):
                    continue
                if (func.name, "register", node.lineno) not in found:
                    continue
                assert _dropped_before(func.body, node, False), func.name
    assert writes == CLIENT_WRITES


# --- rev 6 (round-5 F001): a reply's own record, never a lookup by key after an await


def _replace_with_b(manager: ClientManager, status_message: str = "from-B") -> None:
    """A reconnect: the old connection's records are dropped, a new connection
    registered, and the new connection tracks its own, unrelated task "t"."""
    manager._remove_server_indexes("tasks")
    new = _task_server(manager, {})
    new.status.name = "tasks"
    manager._clients = {"tasks": new}
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working", status_message=status_message),
        requestor_context=None,
        connection=new,
    )


@pytest.mark.asyncio
async def test_invoke_never_returns_the_replacement_connections_record() -> None:
    """The round-5 claude seat's falsifier (adapted to rev 5's required
    keywords): the call runs on A; meanwhile the server is replaced by B, which
    tracks its own task "t". A's reply names "t" too. The output must be A's
    task, never B's."""
    manager, replies = _manager("tasks")

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        assert method == "tools/call"
        _replace_with_b(manager)
        return {
            "task": {"taskId": "t", "status": "completed", "statusMessage": "from-A"}
        }

    manager._send_request = send  # type: ignore[method-assign, assignment]
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.invoke({"tool_id": "tasks::run", "arguments": {}, "task": {}})
    assert out.ok, out.errors
    assert "from-B" not in out.model_dump_json()
    assert out.task is not None and out.task.status_message == "from-A"


@pytest.mark.asyncio
async def test_tasks_result_never_pairs_the_reply_with_the_replacements_record() -> (
    None
):
    manager, replies = _manager("tasks")
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=None,
        connection=manager._clients["tasks"],
    )

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        assert method == "tasks/result"
        _replace_with_b(manager)
        return {
            "result": {"v": "A"},
            "task": {"taskId": "t", "status": "completed", "statusMessage": "from-A"},
        }

    manager._send_request = send  # type: ignore[method-assign, assignment]
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.tasks_result({"server_name": "tasks", "task_id": "t"})
    assert out.ok, out.errors
    assert "from-B" not in out.model_dump_json()
    assert out.task is not None and out.task.status_message == "from-A"


@pytest.mark.asyncio
async def test_tasks_list_never_shows_the_replacements_record() -> None:
    manager, replies = _manager("tasks")

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        assert method == "tasks/list"
        _replace_with_b(manager)
        return {
            "tasks": [{"taskId": "t", "status": "working", "statusMessage": "from-A"}]
        }

    manager._send_request = send  # type: ignore[method-assign, assignment]
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.tasks_list({"server_name": "tasks"})
    assert out.ok, out.errors
    assert "from-B" not in out.model_dump_json()
    assert [t.status_message for t in out.tasks] == ["from-A"]


@pytest.mark.asyncio
async def test_invoke_redacts_by_default_only_when_a_task_was_requested() -> None:
    """Round-5 non-blocking: default redaction follows this call's task record,
    which exists only when the call requested a task and the reply carries one
    (main's rule). A tool that returns a `task` object as plain data, on a call
    that did not request a task, is not redacted by default."""
    manager, replies = _manager("tasks")
    secret = "sk-abcdef1234567890abcdef"
    replies[("tasks", "tools/call")] = {
        "task": {"taskId": "t", "status": "completed"},
        "content": [{"type": "text", "text": secret}],
    }
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    plain = await tools.invoke({"tool_id": "tasks::run", "arguments": {}})
    assert plain.ok and secret in plain.model_dump_json()
    asked = await tools.invoke({"tool_id": "tasks::run", "arguments": {}, "task": {}})
    assert asked.ok and secret not in asked.model_dump_json()


#: Every function in src/pmcp that reads task state -- an accessor, `_tasks`,
#: or an inline reader -- after one of its awaits, with the reason that is
#: safe (rev 6). Derived from the AST and compared whole: a handler that looks
#: a task up by key after awaiting the downstream fails here.
READ_AFTER_AWAIT: dict[str, str] = {
    # records this reply, through the choke point (bound to its connection)
    "client/manager.py:call_tool_with_task": "records this reply",
    "client/manager.py:list_tasks": "records each listed task",
    "client/manager.py:_fetch_task": "records this reply",
    "client/manager.py:get_task_result_with_task": "records this reply",
    "client/manager.py:_send_task_cancel": "records this reply",
    # drops or clears records (writes)
    "client/manager.py:disconnect_server": "drops the server's records",
    "client/manager.py:_reconcile_once": "drops records of re-listed kinds",
    "client/manager.py:_connect_stdio": "drops the name's records",
    "client/manager.py:_connect_remote_stream": "drops the name's records",
    "client/manager.py:_cleanup_client": "drops the name's records",
    "client/manager.py:_disconnect_all_unlocked": "clears every record",
    # Consiliency/pmcp#324's split of disconnect_server and adopt_process
    "client/manager.py:_disconnect_server_locked": "drops the server's records",
    "client/manager.py:_adopt_process_locked": "drops the name's records",
    # re-reads by key, after checking the connection is still the snapshot's
    "client/manager.py:_cancel_tracked_for_teardown": (
        "each entry's binding is checked first, so the record under the key is "
        "that connection's"
    ),
    # counts only: no record is used
    "tools/handlers.py:refresh": "counts the active tasks remaining",
    "tools/handlers.py:disconnect_server": "counts (before - after)",
    "tools/handlers.py:_restart_resolved_server": "counts (before - after)",
}


def _reads_after_an_await(tree: ast.AST, readers: set[str]) -> set[str]:
    found = set()
    for func in _functions(tree):
        if func.name == "__init__":
            continue
        awaits = [
            (n.end_lineno or n.lineno, n.end_col_offset or 0)
            for n in ast.walk(func)
            if isinstance(n, ast.Await)
        ]
        if not awaits:
            continue
        reads = [
            (n.lineno, n.col_offset)
            for n in ast.walk(func)
            if _reads_records(n, readers)
        ]
        looped = any(
            any(_reads_records(n, readers) for n in ast.walk(loop))
            and any(isinstance(n, ast.Await) for n in ast.walk(loop))
            for loop in ast.walk(func)
            if isinstance(loop, ast.For | ast.AsyncFor | ast.While)
        )
        if any(r > min(awaits) for r in reads) or looped:
            found.add(func.name)
    return found


def test_no_task_state_is_looked_up_after_an_await_unless_listed() -> None:
    trees = {path: ast.parse(path.read_text()) for path in SRC.rglob("*.py")}
    readers = _sync_readers(list(trees.values()))
    found = {
        f"{path.relative_to(SRC)}:{name}"
        for path, tree in trees.items()
        for name in _reads_after_an_await(tree, readers)
    }
    assert found == set(READ_AFTER_AWAIT)


def test_the_after_await_scan_sees_a_handler_lookup() -> None:
    """The scan on the shape round 5 found: a handler that awaits the manager
    and then looks the task up by key."""
    source = (
        "class GatewayTools:\n"
        "    async def invoke(self):\n"
        "        result = await self._client_manager.call_tool('t', {}, 1)\n"
        "        return self._client_manager.get_task_record('s', 't')\n"
    )
    tree = ast.parse(source)
    assert _reads_after_an_await(tree, _sync_readers([tree])) == {"invoke"}


#: Every `_send_request` call in src/pmcp, by function and method expression
#: (rev 6): a method passed as a variable or keyword would appear here, and
#: every `tasks/*` method must be one of TASK_SENDS.
SEND_METHODS = {
    ("call_tool_with_task", "'tools/call'"),
    ("_fetch_task", "'tasks/get'"),
    ("get_task_result_with_task", "'tasks/result'"),
    ("_send_task_cancel", "'tasks/cancel'"),
    ("list_tasks", "'tasks/list'"),
    ("read_resource", "'resources/read'"),
    ("get_prompt", "'prompts/get'"),
    ("_send_initialize", "'initialize'"),
    ("_fetch_listing_pages", "f'{kind}/list'"),
}


def test_every_downstream_request_method_is_classified() -> None:
    sends = set()
    tasks_constants = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for func in _functions(tree):
            for n in ast.walk(func):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_send_request"
                ):
                    method = n.args[1] if len(n.args) > 1 else None
                    for k in n.keywords:
                        if k.arg == "method":
                            method = k.value
                    assert method is not None, func.name
                    sends.add((func.name, ast.unparse(method)))
                if (
                    isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and n.value.startswith("tasks/")
                    and not isinstance(parents.get(n), ast.Expr)  # a docstring
                ):
                    tasks_constants.add((func.name, n.value))
    assert sends == SEND_METHODS
    # every `tasks/*` string in src/pmcp is the method of a bound send
    assert tasks_constants == {(func, method) for func, method in TASK_SENDS}


@pytest.mark.asyncio
async def test_tasks_result_does_not_take_a_reply_about_another_task() -> None:
    """A downstream may answer `tasks/result` about a different task. That task
    is recorded, but it is not the task asked for: `gateway.tasks_result`
    reports no task rather than another task's record."""
    manager, replies = _manager("tasks")
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=None,
        connection=manager._clients["tasks"],
    )
    replies[("tasks", "tasks/result")] = {
        "result": {"v": 1},
        "task": {"taskId": "other", "status": "completed"},
    }
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.tasks_result({"server_name": "tasks", "task_id": "t"})
    assert out.ok, out.errors
    assert out.task is None


@pytest.mark.parametrize("existing_client", [False, True])
@pytest.mark.asyncio
async def test_a_stdio_connect_drops_the_names_records_before_anything_else(
    existing_client: bool,
) -> None:
    """Behavioural twin of the registration guard (rev 6): a connect for a
    name drops that name's task records first, on both of its branches (a
    live client to clean up, or none), even when the connect then fails."""
    manager, _replies = _manager("tasks")
    old = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=old,
    )
    if not existing_client:
        manager._clients.pop("tasks")
    remote = ResolvedServerConfig(
        name="tasks",
        source="custom",
        config=RemoteMcpServerConfig(type="streamable-http", url="https://t/mcp"),
    )
    with pytest.raises(ValueError, match="unsupported local config type"):
        await manager._connect_stdio(remote)
    assert manager.get_task_record("tasks", "t") is None


# --- rev 7 (round-6 F001/F002): one connection per request, captured at entry


def test_late_reply_does_not_merge_replacement_state() -> None:
    """Round-6 codex F001, as written: while `tasks/get` awaits on A, B
    replaces A and records its own "t". A's reply must not inherit B's
    `created_at`, `tool_id` or `requestor_context`."""

    async def scenario() -> None:
        manager, _ = _manager("tasks")
        old = manager._clients["tasks"]
        replacement = _connection("tasks", "https://b.example/mcp")
        replacement_record = None

        async def send(managed: Any, method: str, params: Any, **kwargs: Any) -> Any:
            nonlocal replacement_record
            assert managed is old and method == "tasks/get"
            manager._remove_server_indexes("tasks")
            manager._clients["tasks"] = replacement
            replacement_record = manager._record_task(
                "tasks",
                McpTaskInfo(task_id="t", status="working", created_at=222),
                tool_id="tasks::tenant-b-tool",
                requestor_context={"tenant": "B"},
                connection=replacement,
            )
            return {"taskId": "t", "status": "completed", "createdAt": 111}

        manager._send_request = send  # type: ignore[method-assign]
        reply = await manager.get_task("tasks", "t")
        assert manager.get_task_record("tasks", "t") is replacement_record
        assert (reply.created_at, reply.tool_id, reply.requestor_context) == (  # type: ignore[attr-defined]
            111,
            None,
            None,
        )

    asyncio.run(scenario())


def test_evicted_result_fallback_never_sends_to_replacement() -> None:
    """Round-6 codex F002, as written: "t" was evicted, so the request has no
    record to bind to; while `tasks/result` awaits on A, B replaces A. The
    `tasks/get` fallback must not go to B, and B's task must not be paired
    with A's result."""

    async def scenario() -> None:
        manager, _ = _manager("tasks")
        old = manager._clients["tasks"]
        replacement = _connection("tasks", "https://b.example/mcp")
        manager._tasks.per_server = 1
        for task_id in ("t", "evictor"):
            manager._record_task(
                "tasks",
                McpTaskInfo(task_id=task_id, status="working"),
                requestor_context={"tenant": "A"},
                connection=old,
            )
        assert manager.get_task_record("tasks", "t") is None
        replacement_requests: list[Any] = []

        async def send(managed: Any, method: str, params: Any, **kwargs: Any) -> Any:
            if managed is old:
                assert method == "tasks/result"
                manager._remove_server_indexes("tasks")
                manager._clients["tasks"] = replacement
                return {"result": {"value": "from-A"}}
            replacement_requests.append((method, params))
            return {"taskId": "t", "status": "completed", "statusMessage": "from-B"}

        manager._send_request = send  # type: ignore[method-assign]
        gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
        output = await gateway.tasks_result(
            {
                "server_name": "tasks",
                "task_id": "t",
                "requestor_context": {"tenant": "A"},
            }
        )
        assert replacement_requests == [], (replacement_requests, output)
        assert output.task is None or output.task.status_message != "from-B"

    asyncio.run(scenario())


B_STATE = {
    "status_message": "from-B",
    "created_at": 222.0,
    "tool_id": "tasks::B",
    "requestor_context": {"tenant": "B"},
}


def _carries_b_state(record: Any) -> list[str]:
    if record is None:
        return []
    data = record if isinstance(record, dict) else record.model_dump()
    return [k for k, v in B_STATE.items() if data.get(k) == v]


async def _op_call_tool(m: ClientManager) -> list[Any]:
    reply = await m.call_tool_with_task("tasks::run", {}, task={})
    return [reply.task]


async def _op_list(m: ClientManager) -> list[Any]:
    return list((await m.list_tasks("tasks"))["tasks"])


async def _op_get(m: ClientManager) -> list[Any]:
    return [await m.get_task("tasks", "t")]


async def _op_result(m: ClientManager) -> list[Any]:
    return [(await m.get_task_result_with_task("tasks", "t")).task]


async def _op_cancel(m: ClientManager) -> list[Any]:
    ok, record, _message = await m.cancel_task("tasks", "t")
    return [record]


async def _op_teardown(m: ClientManager) -> list[Any]:
    await m.cancel_active_tasks("tasks")
    return []


#: Every manager task operation, with each of its reply shapes: the request
#: functions the guard below derives are each reached by at least one.
TASK_OPS: dict[str, tuple[Callable[[ClientManager], Awaitable[list[Any]]], Any]] = {
    "tools/call": (_op_call_tool, {"task": {"taskId": "t", "status": "working"}}),
    "tasks/list": (_op_list, {"tasks": [{"taskId": "t", "status": "working"}]}),
    "tasks/get": (_op_get, {"taskId": "t", "status": "working"}),
    "tasks/result payload": (
        _op_result,
        {"result": {"v": "A"}, "task": {"taskId": "t", "status": "completed"}},
    ),
    "tasks/result -> tasks/get fallback": (_op_result, {"result": {"v": "A"}}),
    "tasks/cancel": (_op_cancel, {"taskId": "t", "status": "cancelled"}),
    "teardown tasks/cancel": (_op_teardown, {"taskId": "t", "status": "cancelled"}),
}


@pytest.mark.parametrize("tracked", ["tracked", "evicted"])
@pytest.mark.parametrize("op", list(TASK_OPS))
@pytest.mark.asyncio
async def test_no_task_op_sends_to_records_from_or_merges_a_replacement(
    op: str, tracked: str
) -> None:
    """Generated (rev 7): every manager task operation, on every reply path,
    with `t` tracked or already evicted, and a replacement connection B
    registered while the operation's first request awaits on A. B already
    tracks its own `t`. Nothing may be sent to B; B's record stays exactly
    as it is; nothing the operation returns carries B's state."""
    manager, _replies = _manager("tasks")
    a = manager._clients["tasks"]
    b = _connection("tasks", "https://b.example/mcp")
    manager._tasks.per_server = 1
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working", created_at=111),
        tool_id="tasks::run",
        requestor_context={"tenant": "A"},
        connection=a,
    )
    if tracked == "evicted":
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id="evictor", status="working"),
            requestor_context=None,
            connection=a,
        )
        assert manager.get_task_record("tasks", "t") is None
    drive, reply = TASK_OPS[op]
    to_b: list[Any] = []
    b_record: list[Any] = []

    async def send(managed: Any, method: str, params: Any, **_: Any) -> Any:
        if managed is b:
            to_b.append((method, params))
            return {"taskId": "t", "status": "working", "statusMessage": "from-B"}
        if manager._clients.get("tasks") is a:
            manager._remove_server_indexes("tasks")
            manager._clients["tasks"] = b
            b_record.append(
                manager._record_task(
                    "tasks",
                    McpTaskInfo(
                        task_id="t",
                        status="working",
                        status_message="from-B",
                        created_at=222.0,
                    ),
                    tool_id="tasks::B",
                    requestor_context={"tenant": "B"},
                    connection=b,
                )
            )
        return reply

    manager._send_request = send  # type: ignore[method-assign, assignment]
    try:
        returned = await drive(manager)
    except RuntimeError as exc:  # refused before sending: nothing reached B
        assert "reconnected" in str(exc) or "not connected" in str(exc), exc
        returned = []
    assert to_b == [], to_b
    for record in returned:
        assert _carries_b_state(record) == [], (record, op)
    if b_record:
        assert manager.get_task_record("tasks", "t") is b_record[0]
        assert b_record[0].status_message == "from-B"


def _request_functions(tree: ast.AST) -> set[str]:
    """Async functions that carry a downstream task request: they send
    `tools/call` or a `tasks/*` method, or call the functions that do, or
    resolve a task connection."""
    calls = {"_fetch_task", "_send_task_cancel", "_task_client"}
    found = set()
    for func in _functions(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        for n in ast.walk(func):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr in calls or (
                n.func.attr == "_send_request"
                and len(n.args) > 1
                and isinstance(n.args[1], ast.Constant)
                and (
                    str(n.args[1].value).startswith("tasks/")
                    or n.args[1].value == "tools/call"
                )
            ):
                found.add(func.name)
                break
    return found


#: Request functions that resolve a connection inside a loop: each iteration
#: is its own request, resolved once at the top of the iteration.
RESOLVE_PER_ITERATION = {
    "list_tasks": "each listed server is its own request",
    "_cancel_tracked_for_teardown": (
        "each snapshot entry is its own request, bound to the entry's connection"
    ),
}

_RESOLVERS = {"_task_client", "_task_client_unavailable"}


def _client_resolvers(trees: list[ast.AST]) -> set[str]:
    """Everything that resolves a client by name (rev 8, closing the seat's
    helper dodge): the two resolvers, and every plain helper -- a `def`, or an
    `async def` with no await -- that touches `_clients` or calls a resolver,
    closed to a fixpoint. `_owns` is the one exception: it compares a
    captured connection with `_clients`; it hands no client back."""
    found = set(_RESOLVERS)
    while True:
        more = {
            func.name
            for tree in trees
            for func in _functions(tree)
            if func.name not in found | {"_owns", "__init__"}
            and (
                isinstance(func, ast.FunctionDef)
                or not any(isinstance(n, ast.Await) for n in ast.walk(func))
            )
            and any(
                (isinstance(n, ast.Attribute) and n.attr in found | {"_clients"})
                for n in ast.walk(func)
            )
        }
        if not more:
            return found
        found |= more


def _late_resolutions(
    tree: ast.AST, requests: set[str], resolvers: set[str]
) -> dict[str, list[str]]:
    late: dict[str, list[str]] = {}
    for func in _functions(tree):
        if func.name not in requests:
            continue
        awaits = [
            (n.end_lineno or n.lineno, n.end_col_offset or 0)
            for n in ast.walk(func)
            if isinstance(n, ast.Await)
        ]
        if not awaits:
            continue
        first = min(awaits)

        def resolves(n: ast.AST) -> bool:
            return isinstance(n, ast.Attribute) and n.attr in resolvers | {"_clients"}

        hits = [
            ast.unparse(n)
            for n in ast.walk(func)
            if hasattr(n, "lineno")
            and (n.lineno, n.col_offset) > first  # type: ignore[attr-defined]
            and resolves(n)
        ]
        looped = any(
            any(resolves(x) for x in ast.walk(loop))
            and any(isinstance(x, ast.Await) for x in ast.walk(loop))
            for loop in ast.walk(func)
            if isinstance(loop, ast.For | ast.AsyncFor | ast.While)
        )
        if hits or looped:
            late[func.name] = hits
    return late


def test_no_request_resolves_its_connection_again_after_an_await() -> None:
    """Rev 7: a request captures its connection once, at entry. After its
    first await, no request function resolves a client by name: no
    `_task_client`, `_task_client_unavailable`, `_clients[...]` or
    `_clients.get(...)`, and (rev 8) no plain helper that does any of those,
    followed transitively. The only allowed look at `_clients` is the
    identity check `_owns(server, captured)`. The request functions are
    derived.

    Limit (rev 9, round-8 note): a static scan cannot see a name computed at
    run time, such as `getattr(self, "_task_" + "client")`. That shape is
    caught behaviourally instead: the generated
    `test_no_task_op_sends_to_records_from_or_merges_a_replacement` sends
    nothing to a replacement on any path."""
    trees = [ast.parse(path.read_text()) for path in SRC.rglob("*.py")]
    tree = ast.parse((SRC / "client" / "manager.py").read_text())
    requests = _request_functions(tree)
    assert requests == {
        "call_tool_with_task",
        "list_tasks",
        "get_task",
        "_fetch_task",
        "get_task_result_with_task",
        "cancel_task",
        "_send_task_cancel",
        "_cancel_tracked_for_teardown",
    }
    every_op = {
        "call_tool_with_task",
        "list_tasks",
        "get_task",
        "get_task_result_with_task",
        "cancel_task",
        "cancel_active_tasks",
    }
    # every public request function is driven by the generated test above
    driven = {
        "_op_call_tool": "call_tool_with_task",
        "_op_list": "list_tasks",
        "_op_get": "get_task",
        "_op_result": "get_task_result_with_task",
        "_op_cancel": "cancel_task",
        "_op_teardown": "cancel_active_tasks",
    }
    assert {driven[d.__name__] for d, _ in TASK_OPS.values()} == every_op
    late = _late_resolutions(tree, requests, _client_resolvers(trees))
    assert set(late) == set(RESOLVE_PER_ITERATION), late
    # and those resolve only at the top of each iteration, never later
    assert all(hits == [] for hits in late.values()), late


_HELPER_SHAPES = {
    # the round-7 claude seat's probe: a plain helper that looks `_clients` up
    "helper-get": "def _current_client(self, name):\n"
    "        return self._clients.get(name)\n",
    "helper-subscript": "def _current_client(self, name):\n"
    "        return self._clients[name]\n",
    "helper-of-a-helper": "def _inner(self, name):\n"
    "        return self._clients.get(name)\n"
    "    def _current_client(self, name):\n"
    "        return self._inner(name)\n",
    "await-free-async-helper": "async def _current_client(self, name):\n"
    "        return self._clients.get(name)\n",
}


@pytest.mark.parametrize("shape", list(_HELPER_SHAPES))
def test_the_resolution_guard_follows_helpers_but_not_owns(shape: str) -> None:
    """Rev 8 (round-7 claude note): a request that, after its await, calls a
    helper that resolves a client by name is caught; one that calls `_owns`
    is not."""
    template = (
        "class ClientManager:\n"
        "    def _owns(self, name, connection):\n"
        "        return self._clients.get(name) is connection\n"
        "    {helper}"
        "    async def get_task_result_with_task(self, name):\n"
        "        managed = self._task_client(name, bound_to=None)\n"
        "        await self._send_request(managed, 'tasks/result', {{}})\n"
        "        {after}\n"
    )
    dodge = ast.parse(
        template.format(
            helper=_HELPER_SHAPES[shape],
            after="managed = self._current_client(name)",
        )
    )
    late = _late_resolutions(
        dodge, {"get_task_result_with_task"}, _client_resolvers([dodge])
    )
    assert late == {"get_task_result_with_task": ["self._current_client"]}, late
    allowed = ast.parse(
        template.format(
            helper=_HELPER_SHAPES[shape], after="ok = self._owns(name, managed)"
        )
    )
    assert (
        _late_resolutions(
            allowed, {"get_task_result_with_task"}, _client_resolvers([allowed])
        )
        == {}
    )


def test_a_leftover_record_from_another_connection_is_never_merged() -> None:
    """The second layer of rev 7's merge rule: even on an owned connection, an
    existing record stamped with a *different* connection is not merged. Every
    replacement path drops the name's records first, so this needs a record
    that outlived its connection; it is built here directly."""
    manager, _replies = _manager("tasks")
    a = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working", created_at=111),
        tool_id="tasks::A",
        requestor_context={"tenant": "A"},
        connection=a,
    )
    b = _connection("tasks", "https://b.example/mcp")
    manager._clients["tasks"] = b  # no drop: A's record is left over
    record = manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=None,
        connection=b,
    )
    assert (record.created_at, record.tool_id, record.requestor_context) == (
        None,
        None,
        None,
    )


def test_a_reply_on_a_connection_that_is_not_the_servers_reads_nothing() -> None:
    """The first layer of rev 7's merge rule, on its own: ownership is decided
    before the registry is read. A reply on a connection that is not the
    server's is built from the reply alone, whatever the registry holds --
    here an unbound record (no connection stamp), which the second layer
    (another connection's stamp) would not catch."""
    manager, _replies = _manager("tasks")
    a = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working", created_at=222),
        tool_id="tasks::unbound",
        requestor_context={"tenant": "unbound"},
        connection=None,
    )
    manager._clients["tasks"] = _connection("tasks", "https://b.example/mcp")
    record = manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="completed", created_at=111),
        requestor_context=None,
        connection=a,
    )
    assert (record.created_at, record.tool_id, record.requestor_context) == (
        111,
        None,
        None,
    )


# --- rev 8 (round-7 codex F001): the newest context pmcp holds wins


def test_late_status_reply_preserves_newer_context() -> None:
    """The round-7 codex seat's falsifier, as written: `tasks/get` captures
    the old context and awaits; meanwhile a new call on the same connection
    records the same task with a new context. The late reply must not put
    the old one back, and the next cancel sends the new one (as main does)."""

    async def scenario() -> None:
        manager = ClientManager()
        connection = _task_server(manager, {})
        old = {"correlation": "old"}
        new = {"correlation": "new"}
        cancels = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def send(managed: Any, method: str, params: Any, **kwargs: Any) -> Any:
            assert managed is connection
            if method == "tools/call":
                return {"task": {"taskId": "t", "status": "working"}}
            if method == "tasks/get":
                entered.set()
                await release.wait()
                return {"taskId": "t", "status": "working"}
            assert method == "tasks/cancel"
            cancels.append(params["task"]["requestorContext"])
            return {"taskId": "t", "status": "cancelled"}

        manager._send_request = send  # type: ignore[method-assign]
        await manager.call_tool("tasks::run", {}, task={"requestor_context": old})
        poll = asyncio.create_task(manager.get_task("tasks", "t"))
        await entered.wait()
        await manager.call_tool("tasks::run", {}, task={"requestor_context": new})
        record = manager.get_task_record("tasks", "t")
        assert record is not None and record.requestor_context == new
        release.set()
        await poll
        await manager.cancel_task("tasks", "t")
        assert cancels == [new], cancels

    asyncio.run(scenario())


OLD = {"correlation": "old"}
NEW = {"correlation": "new"}


async def _held_get(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/get")] = {"taskId": "t", "status": "working"}
    await m.get_task("tasks", "t")


async def _held_result(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/result")] = {"task": {"taskId": "t", "status": "working"}}
    await m.get_task_result_with_task("tasks", "t")


async def _held_result_fallback(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/result")] = {"result": {}}
    r[("tasks", "tasks/get")] = {"taskId": "t", "status": "working"}
    await m.get_task_result_with_task("tasks", "t")


async def _held_cancel(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "working"}
    await m.cancel_task("tasks", "t")


async def _held_teardown(m: ClientManager, r: dict[Any, Any]) -> None:
    r[("tasks", "tasks/cancel")] = {"taskId": "t", "status": "working"}
    await m.cancel_active_tasks("tasks")


#: Every path that records a reply with a context captured before its await.
HELD_CONTEXT_OPS = {
    "tasks/get": (_held_get, "tasks/get"),
    "tasks/result": (_held_result, "tasks/result"),
    "tasks/result -> tasks/get": (_held_result_fallback, "tasks/result"),
    "tasks/cancel": (_held_cancel, "tasks/cancel"),
    "teardown tasks/cancel": (_held_teardown, "tasks/cancel"),
}


# --- rev 9 (round-8 F001): a record with a request in flight cannot be evicted


@pytest.mark.asyncio
async def test_a_newer_context_evicted_mid_await_is_not_replaced_by_the_older() -> None:
    """The round-8 claude seat's falsifier, as written: an older `tasks/get`
    captured OLD; during its await a newer call stores NEW and *then* two
    more tasks would evict `t`. `t` is pinned by the in-flight request, so it
    stays, and the reply keeps NEW."""
    import inspect

    m = ClientManager()
    m._clients = {"tasks": _task_server(m, {})}
    cap = hasattr(m._tasks, "per_server")
    if cap:
        m._tasks.per_server = 2
    fired = False
    cancels: list[Any] = []

    def _record(task_id: str) -> None:
        params = inspect.signature(m._record_task).parameters
        kw: dict[str, Any] = {}
        for name in ("requestor_context", "fallback_context"):
            if name in params:
                kw[name] = None
        if "connection" in params:
            kw["connection"] = m._clients["tasks"]
        m._record_task("tasks", McpTaskInfo(task_id=task_id, status="working"), **kw)

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        nonlocal fired
        if method == "tools/call":
            return {"task": {"taskId": "t", "status": "working"}}
        if method == "tasks/cancel":
            cancels.append(params.get("task", {}).get("requestorContext"))
            return {"taskId": "t", "status": "cancelled"}
        if not fired:  # the older tasks/get is in flight
            fired = True
            await m.call_tool("tasks::run", {}, task={"requestor_context": NEW})
            if cap:  # then two more tasks would evict t (holding NEW)
                _record("x")
                _record("y")
        return {"taskId": "t", "status": "working"}

    m._send_request = send  # type: ignore[method-assign, assignment]
    await m.call_tool("tasks::run", {}, task={"requestor_context": OLD})
    await m.get_task("tasks", "t")
    await m.cancel_task("tasks", "t")
    assert cancels == [NEW], cancels


def _bounded(registry: TaskRegistry) -> None:
    """Wrap `put` and `unpin` so every call checks rev 9's stated bound:
    a server holds at most max(per_server, its pinned records + 1), and the
    registry at most max(total, pinned records + 1)."""

    def check() -> None:
        pinned = {key for key in registry.pinned if key in registry}
        for server in {server for server, _ in registry}:
            size = sum(1 for key in registry if key[0] == server)
            mine = sum(1 for key in pinned if key[0] == server)
            assert size <= max(registry.per_server, mine + 1), (server, size, mine)
        assert len(registry) <= max(registry.total, len(pinned) + 1)

    for name in ("put", "unpin"):
        original = getattr(registry, name)

        def wrapped(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            result = _original(*args, **kwargs)
            check()
            return result

        setattr(registry, name, wrapped)


PHASES = ("pre", "during", "post")


@pytest.mark.parametrize("pressure", ["others-pinned-at-cap", "others-unpinned"])
@pytest.mark.parametrize("eviction", ["before-newer", "after-newer", "after-all"])
@pytest.mark.parametrize("newer", ["before", "during", "after"])
@pytest.mark.parametrize("op", list(HELD_CONTEXT_OPS))
@pytest.mark.asyncio
async def test_a_pinned_record_keeps_the_newest_context_in_every_order(
    op: str, newer: str, eviction: str, pressure: str
) -> None:
    """Generated (rev 9): every held-context path x a newer call (NEW) before,
    during or after the request's await x a flood of other tasks before or
    after the newer call, or after everything x the server's other records
    pinned at the cap (other requests in flight) or not.

    - While the request is in flight, `t` is never evicted.
    - Every `put` and `unpin` keeps the stated bound.
    - Every pin is released at the end (the autouse guard).
    - If no flood evicted `t` while no request pinned it, the next cancel
      sends NEW. Losing NEW is possible only to an eviction outside every
      request (the stated residual), and even then OLD never comes back.
    - Rev 11, the teardown: it owes `t` (custody) but pins it only while
      `t`'s own cancel is in flight. Its cancel for `t` carries the newest
      context pmcp held at that moment, the custody's included, and a `t` the
      cap evicted while owed is re-tracked (by a reply or a listing) with
      that context, not without it."""
    manager, replies = _manager("tasks")
    registry = manager._tasks
    registry.per_server = 3
    _bounded(registry)
    a = manager._clients["tasks"]
    replies[("tasks", "tools/call")] = {"task": {"taskId": "t", "status": "working"}}
    await manager.call_tool_with_task("tasks::run", {}, task={"requestor_context": OLD})
    for filler in ("f1", "f2"):
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=filler, status="working"),
            requestor_context=None,
            connection=a,
        )
    held = (
        [("tasks", "f1"), ("tasks", "f2")] if pressure == "others-pinned-at-cap" else []
    )
    for key in held:
        registry.pin(key)

    steps: dict[str, list[str]] = {phase: [] for phase in PHASES}
    newer_phase = {"before": "pre", "during": "during", "after": "post"}[newer]
    if eviction == "before-newer":
        steps[newer_phase] += ["flood", "newer"]
    elif eviction == "after-newer":
        steps[newer_phase] += ["newer", "flood"]
    else:
        steps[newer_phase].append("newer")
        steps["post"].append("flood")
    lost = False
    floods = 0
    held_ctx: Any = OLD  # the newest context pmcp holds for t, custody included
    teardown = op == "teardown tasks/cancel"

    def owed() -> bool:
        return any(("tasks", "t") in c._held for c in registry._custodies)

    async def run(phase: str) -> None:
        nonlocal lost, floods, held_ctx
        for step in steps[phase]:
            if step == "newer":
                await manager.call_tool_with_task(
                    "tasks::run", {}, task={"requestor_context": NEW}
                )
                lost = False  # NEW is held again
                held_ctx = NEW
                continue
            floods += 1
            for i in range(3):
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id=f"x{floods}-{i}", status="working"),
                    requestor_context=None,
                    connection=a,
                )
            if ("tasks", "t") in registry.pinned:
                assert ("tasks", "t") in registry, "pinned: never evicted in flight"
            elif ("tasks", "t") not in registry and not owed():
                lost = True  # evicted while no request pinned it
                held_ctx = None

    drive, first = HELD_CONTEXT_OPS[op]
    plain = manager._send_request
    fired = False
    sent_for_t: list[Any] = []
    expected_for_t: list[Any] = []

    async def send(managed: ManagedClient, method: str, params: Any, **kw: Any) -> Any:
        nonlocal fired
        if method == first and not fired:
            fired = True
            if t_at_start and teardown:
                assert owed(), "owed by the teardown from its request"
            elif t_at_start:
                assert ("tasks", "t") in registry.pinned, "pinned at request start"
            await run("during")
        if teardown and method == "tasks/cancel" and params["taskId"] == "t":
            assert ("tasks", "t") in registry.pinned, "its own cancel pins it"
            sent_for_t.append(params.get("task", {}).get("requestorContext"))
            expected_for_t.append(held_ctx)
        return await plain(managed, method, params, **kw)

    await run("pre")
    t_at_start = ("tasks", "t") in registry
    manager._send_request = send  # type: ignore[method-assign, assignment]
    await drive(manager, replies)
    manager._send_request = plain  # type: ignore[method-assign]
    if teardown:
        # owed iff tracked and unfinished at its request: then exactly one
        # cancel for t, carrying the newest context pmcp held for it
        assert sent_for_t == expected_for_t, (sent_for_t, expected_for_t)
        assert len(sent_for_t) == (1 if t_at_start else 0), sent_for_t
        assert OLD not in sent_for_t or held_ctx == OLD
    await run("post")
    for key in held:
        registry.unpin(key)
    assert registry.pinned == {}
    assert len(registry) <= registry.per_server  # the slack is given back

    record = manager.get_task_record("tasks", "t")
    if lost:
        # NEW was evicted after it was supplied, while no request pinned `t`:
        # the stated residual (pmcp cannot keep what it no longer tracks). The
        # stale context still never comes back.
        assert record is None or record.requestor_context != OLD, record
        return
    assert record is not None and record.requestor_context == NEW, (record, op)
    sent: list[Any] = []

    async def cancel_send(
        managed: ManagedClient, method: str, params: Any, **_: Any
    ) -> Any:
        sent.append(params["task"]["requestorContext"])
        return {"taskId": "t", "status": "cancelled"}

    manager._send_request = cancel_send  # type: ignore[method-assign, assignment]
    await manager.cancel_task("tasks", "t")
    assert sent == [NEW], sent


# --- pins are released however a request ends -----------------------------------


async def _end_get(m: ClientManager) -> Any:
    return await m.get_task("tasks", "t")


async def _end_result(m: ClientManager) -> Any:
    return await m.get_task_result_with_task("tasks", "t")


async def _end_cancel(m: ClientManager) -> Any:
    return await m.cancel_task("tasks", "t")


async def _end_teardown(m: ClientManager) -> Any:
    return await m.cancel_active_tasks("tasks")


PINNING_OPS = {
    "tasks/get": _end_get,
    "tasks/result": _end_result,
    "tasks/cancel": _end_cancel,
    "teardown": _end_teardown,
}


@pytest.mark.parametrize("ending", ["reply", "exception", "cancelled", "timeout"])
@pytest.mark.parametrize("op", list(PINNING_OPS))
@pytest.mark.asyncio
async def test_every_pin_is_released_however_the_request_ends(
    op: str, ending: str
) -> None:
    """Rev 9: a pinning request releases its pins on a reply, on an exception
    from the downstream, when its task is cancelled, and when it times out.
    The pin is held while the request awaits."""
    manager, replies = _manager("tasks")
    a = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=CTX,
        connection=a,
    )
    entered = asyncio.Event()

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        assert ("tasks", "t") in manager._tasks.pinned
        entered.set()
        if ending == "exception":
            raise RuntimeError("downstream failed")
        if ending in ("cancelled", "timeout"):
            await asyncio.Event().wait()  # never answers
        return {"taskId": "t", "status": "working", "task": {"taskId": "t"}}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    call = PINNING_OPS[op](manager)
    if ending == "reply":
        await call
    elif ending == "exception":
        with pytest.raises(RuntimeError):
            await call
    elif ending == "timeout":
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(call, timeout=0.05)
    else:
        running = asyncio.ensure_future(call)
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert manager._tasks.pinned == {}


# --- the registry's pins, directly ---------------------------------------------


def test_a_pinned_record_is_never_evicted_and_unpinning_gives_the_slack_back() -> None:
    registry = TaskRegistry(per_server=2)
    _put(registry, "s", "a", "working")
    _put(registry, "s", "b", "working")
    registry.pin(("s", "a"))
    registry.pin(("s", "b"))
    _put(registry, "s", "c", "working")  # a and b pinned: nothing to give up
    assert sorted(k[1] for k in registry) == ["a", "b", "c"]  # 3 <= max(2, 2 + 1)
    _put(registry, "s", "d", "working")  # c is the only unpinned candidate
    assert sorted(k[1] for k in registry) == ["a", "b", "d"]
    evicted = registry.unpin(("s", "a"))  # the cap comes back at once
    assert [r.task_id for r in evicted] == ["a"]
    assert sorted(k[1] for k in registry) == ["b", "d"]
    registry.unpin(("s", "b"))
    assert registry.pinned == {}


def test_pins_nest_and_an_unbalanced_unpin_raises() -> None:
    registry = TaskRegistry(per_server=1)
    _put(registry, "s", "a", "working")
    registry.pin(("s", "a"))
    registry.pin(("s", "a"))
    _put(registry, "s", "b", "working")
    assert ("s", "a") in registry
    registry.unpin(("s", "a"))
    _put(registry, "s", "c", "working")
    assert ("s", "a") in registry  # still pinned once
    registry.unpin(("s", "a"))
    assert ("s", "a") not in registry  # released: the cap took it
    with pytest.raises(KeyError):
        registry.unpin(("s", "a"))


def test_the_total_cap_skips_pinned_records_too() -> None:
    registry = TaskRegistry(per_server=10, total=2)
    _put(registry, "x", "x0", "working")
    _put(registry, "y", "y0", "working")
    registry.pin(("x", "x0"))
    registry.pin(("y", "y0"))
    _put(registry, "z", "z0", "working")  # nothing unpinned to give up
    assert len(registry) == 3  # <= max(2, 2 + 1)
    registry.unpin(("x", "x0"))
    assert sorted(registry) == [("y", "y0"), ("z", "z0")]
    registry.unpin(("y", "y0"))


def test_pins_are_taken_and_released_only_by_the_one_context_manager() -> None:
    """Rev 9: `pin`/`unpin` are called only inside `_pinned`, and `_pinned` is
    used only as a `with` item, so release is always in a `finally`. Every
    request function that reads a record before its first await is pinned:
    decorated with `_pins_task`, or inside `with self._pinned(...)`."""

    def calls(node: ast.AST, _parent: ast.AST | None) -> str | None:
        # any reference, called or passed on (rev 10 registers `unpin` as an
        # ExitStack callback)
        if isinstance(node, ast.Attribute) and node.attr in {"pin", "unpin"}:
            return node.attr
        return None

    assert _owned(calls) == {
        ("client/manager.py", "_pinned", "pin"),
        ("client/manager.py", "_pinned", "unpin"),
    }
    tree = ast.parse((SRC / "client" / "manager.py").read_text())
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_pinned"
        ):
            assert isinstance(parents.get(node), ast.withitem), ast.unparse(node)
    pinned = set()
    for func in _functions(tree):
        decorated = any(
            isinstance(d, ast.Name) and d.id == "_pins_task"
            for d in func.decorator_list
        )
        with_block = any(
            isinstance(n, ast.With)
            and any(
                isinstance(i.context_expr, ast.Call)
                and isinstance(i.context_expr.func, ast.Attribute)
                and i.context_expr.func.attr == "_pinned"
                for i in n.items
            )
            for n in ast.walk(func)
        )
        if decorated or with_block:
            pinned.add(func.name)
    readers = _sync_readers([ast.parse(path.read_text()) for path in SRC.rglob("*.py")])
    capture = {
        func.name
        for func in _functions(tree)
        if func.name in _request_functions(tree)
        and any(
            isinstance(n, ast.Attribute)
            and n.attr in {"get_task_record", "get_active_tasks", "get_tracked_tasks"}
            for n in ast.walk(func)
        )
    }
    assert capture == {
        "get_task",
        "get_task_result_with_task",
        "cancel_task",
        "_cancel_tracked_for_teardown",
    }
    assert capture <= pinned, capture - pinned
    assert readers  # the inline-reader set is in use above


def test_newer_held_context_survives_an_older_reply_after_eviction() -> None:
    """The round-8 codex seat's falsifier, as written: poll A holds `old`; an
    invocation supplies `new` and poll B holds it; a listing would evict the
    task; A's reply lands, then B's. Pinned by both polls, the task is never
    evicted, no reply supplies a context, and `new` stays."""

    async def scenario() -> None:
        manager = ClientManager()
        connection = _task_server(manager, {})
        if hasattr(manager._tasks, "per_server"):
            manager._tasks.per_server = 2
        old_context = {"correlation": "old"}
        new_context = {"correlation": "new"}
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        polls = 0
        cancels = []

        async def send(managed: Any, method: str, params: Any, **kwargs: Any) -> Any:
            nonlocal polls
            assert managed is connection
            if method == "tools/call":
                return {"task": {"taskId": "t", "status": "working"}}
            if method == "tasks/get":
                slot = polls
                polls += 1
                entered[slot].set()
                await release[slot].wait()
                return {"taskId": "t", "status": "working"}
            if method == "tasks/list":
                return {
                    "tasks": [
                        {"taskId": task_id, "status": "working"}
                        for task_id in ("x", "y")
                    ]
                }
            assert method == "tasks/cancel"
            cancels.append(params["task"]["requestorContext"])
            return {"taskId": "t", "status": "cancelled"}

        manager._send_request = send  # type: ignore[method-assign]
        await manager.call_tool(
            "tasks::run", {}, task={"requestor_context": old_context}
        )
        older = asyncio.create_task(manager.get_task("tasks", "t"))
        await entered[0].wait()
        await manager.call_tool(
            "tasks::run", {}, task={"requestor_context": new_context}
        )
        newer = asyncio.create_task(manager.get_task("tasks", "t"))
        await entered[1].wait()
        await manager.list_tasks("tasks")
        release[0].set()
        await older
        release[1].set()
        await newer
        await manager.cancel_task("tasks", "t")
        assert cancels == [new_context], cancels

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_the_teardown_sends_each_records_current_context() -> None:
    """Rev 9: the teardown sends the pinned record's *current* context, not
    the snapshot's copy. While `a`'s cancel awaits, a newer call supplies NEW
    for `b`; `b`'s cancel carries NEW."""
    manager, replies = _manager("tasks")
    a = manager._clients["tasks"]
    for task_id in ("b", "a"):
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=OLD,
            connection=a,
        )
    sent: dict[str, Any] = {}

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        if method == "tools/call":
            return {"task": {"taskId": "b", "status": "working"}}
        sent[params["taskId"]] = params["task"]["requestorContext"]
        if params["taskId"] == "a":
            await manager.call_tool_with_task(
                "tasks::run", {}, task={"requestor_context": NEW}
            )
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    assert await manager.cancel_active_tasks("tasks") == (2, [])
    assert sent == {"a": OLD, "b": NEW}, sent


# --- rev 10 (round-9 codex F001) and rev 11: retention is bounded by caps and live requests


class _LiveBounds:
    """The bounds, checked after every `put`, `unpin` and `watch` (round-10
    codex F001, part 2: rev 10's test fixed R at its peak; this reads it live,
    so a request that finishes shrinks the bound at once).

    1. The registry: with R the requests in flight *now* -- the sum of the pin
       counts, each in-flight get/result/cancel holding one -- a server holds
       at most max(per_server, R(s) + 1) records and the registry at most
       max(total, R + 1). And R never exceeds the requests the test actually
       has in flight (`in_flight`).
    2. A custody is exactly its request-time snapshot, never more, for as long
       as it is registered. Per server that snapshot is at most
       max(per_server, R(s) + 1) -- what the registry may hold -- plus the
       owed records the registry had already given up (rev 12: a teardown
       inherits what the running ones owe, round-11 F001).
    3. Rev 12, the obligations: the tasks of s pmcp tracks only through a
       custody (`get_tracked_tasks(s)` minus the registry: owed, gone from the
       registry, on s's current connection) never exceed the owed records the
       registry has evicted since the current chain of overlapping teardowns
       of s began (a chain ends when no custody owes anything of s). That is the only way
       they accumulate: each is a distinct task a teardown was asked to cancel
       and none has yet. It is history within the chain, not live
       concurrency, and it is zero once no teardown runs."""

    def __init__(
        self,
        registry: TaskRegistry,
        in_flight: Callable[[], int],
        manager: ClientManager | None = None,
    ) -> None:
        self.registry = registry
        self.in_flight = in_flight
        self.manager = manager
        self.peak: dict[str, int] = {}
        self.sizes: dict[int, int] = {}  # id(custody) -> its size at watch
        self.chain: dict[str, int] = {}  # server -> owed evictions this chain
        self.largest_custody = 0
        for name in ("put", "unpin", "watch"):
            if not hasattr(registry, name):
                continue  # an earlier revision's registry (red runs)
            original = getattr(registry, name)

            def wrapped(*a: Any, _original: Any = original, **k: Any) -> Any:
                out = _original(*a, **k)
                if isinstance(out, TaskCustody):
                    self.watched(out)
                self.check()
                return out

            setattr(registry, name, wrapped)
        evict = registry._evict

        def counting(*a: Any, **k: Any) -> Any:
            victim = evict(*a, **k)
            if victim is not None:
                key = (victim.server_name, victim.task_id)
                if any(key in c for c in getattr(registry, "_custodies", [])):
                    server = victim.server_name
                    self.chain[server] = self.chain.get(server, 0) + 1
            return victim

        registry._evict = counting  # type: ignore[method-assign]

    def pins(self, server: str | None = None) -> int:
        return sum(
            n for key, n in self.registry.pinned.items() if server in (None, key[0])
        )

    def owed_gone(self, server: str) -> int:
        if self.manager is None:
            return 0
        return sum(
            1
            for t in self.manager.get_tracked_tasks(server)
            if (server, t.task_id) not in self.registry
        )

    def watched(self, custody: TaskCustody) -> None:
        self.sizes[id(custody)] = len(custody)
        self.largest_custody = max(self.largest_custody, len(custody))
        reg = self.registry
        for server in {key[0] for key in custody._held}:
            owed = sum(1 for key in custody._held if key[0] == server)
            bound = max(reg.per_server, self.pins(server) + 1) + self.owed_gone(server)
            assert owed <= bound, (server, owed, bound)

    def check(self) -> None:
        reg = self.registry
        R = self.pins()
        assert R <= self.in_flight(), (R, self.in_flight())
        for server in {key[0] for key in reg}:
            size = sum(1 for key in reg if key[0] == server)
            self.peak[server] = max(self.peak.get(server, 0), size)
            bound = max(reg.per_server, self.pins(server) + 1)
            assert size <= bound, (server, size, bound)
        assert len(reg) <= max(reg.total, R + 1), (len(reg), R)
        custodies = getattr(reg, "_custodies", [])
        for custody in custodies:
            assert len(custody) == self.sizes[id(custody)], "a custody never grows"
        for server in list(self.chain):
            if not any(key[0] == server for c in custodies for key in c._held):
                self.chain[server] = 0  # the chain ended: nothing is owed
        for server in {key[0] for c in custodies for key in c._held}:
            gone = self.owed_gone(server)
            assert gone <= self.chain.get(server, 0), (server, gone, self.chain)


class _Handoffs:
    """A real manager whose downstream never answers `tasks/*` requests: each
    one is a real `PendingRequest`, so a forced disconnect's
    `cancel_pending_requests` cancels it, exactly as on a live server. The
    registry is checked against rev 11's bounds, with R read live, after every
    `put`, `unpin` and `watch` (`_LiveBounds`). Tests add the asyncio tasks
    they start to `running`: what is in flight is what has not finished."""

    def __init__(self, per_server: int, total: int, requests: int) -> None:
        self.manager, _ = _manager("tasks")
        m = self.manager
        m._tasks.per_server = per_server
        m._tasks.total = total
        self.conn = m._clients["tasks"]
        self.ids = 0
        self.running: list[asyncio.Task[Any]] = []
        self.bounds = _LiveBounds(
            m._tasks, lambda: sum(1 for t in self.running if not t.done()), m
        )

        async def send(
            managed: ManagedClient, method: str, params: Any, **_: Any
        ) -> Any:
            if method == "tools/call":
                return {"task": {"taskId": "t0", "status": "working"}}
            self.ids += 1
            future = asyncio.get_running_loop().create_future()
            managed.pending_requests[self.ids] = PendingRequest(
                request_id=self.ids,
                server_name="tasks",
                tool_id="",
                started_at=0.0,
                last_heartbeat=0.0,
                timeout_ms=30000,
                future=future,
            )
            return await future  # never answered: only a disconnect ends it

        m._send_request = send  # type: ignore[method-assign, assignment]
        for i in range(per_server):
            self.record(f"w{i}")

    def record(self, task_id: str) -> None:
        self.manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=None,
            connection=self.conn,
        )

    @property
    def peak(self) -> int:
        return self.bounds.peak.get("tasks", 0)

    async def settle(self) -> None:
        for _ in range(5):
            await asyncio.sleep(0)


async def _drain(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_overlapping_forced_disconnects_retain_a_bounded_number_of_records() -> None:
    """Round-9 codex F001: each forced disconnect cancels its predecessor's
    in-flight `tasks/cancel` and starts its own teardown; one new task is
    recorded per handoff. On rev 9 each teardown pinned the snapshot its
    predecessor had kept past the cap, so the server grew by one record per
    handoff (codex: 100 -> 1,101 after 1,001). Rev 11 holds what a teardown
    owes in a custody outside the caps, so the registry stays at 101.

    Rev 12 (round-11 F001): each teardown also owes what the running ones
    owe. Since Consiliency/pmcp#324 (merged after the plan's base), the first
    forced disconnect whose own teardown is interrupted -- here, by the next
    one cancelling its in-flight `tasks/cancel` -- still disconnects,
    synchronously, so the server is gone after the first handoff and the
    chain cannot grow: the newest custody holds the 100 + 1 tasks tracked
    when the second disconnect started. (The plan measured 1,100 on its
    base, where an interrupted disconnect left the server connected; the
    obligation bound is exercised there by chains of forced refreshes, which
    do not disconnect.)"""

    async def scenario() -> tuple[int, int]:
        h = _Handoffs(per_server=100, total=1000, requests=0)
        m = h.manager
        running = h.running
        for k in range(1001):
            running.append(
                asyncio.create_task(m.disconnect_server("tasks", force=True))
            )
            await h.settle()
            h.record(f"new{k}")
            await h.settle()
        await _drain(running)
        assert m._tasks.pinned == {}
        return h.peak, h.bounds.largest_custody

    peak, largest = asyncio.run(scenario())
    assert peak <= 101, peak  # the registry: max(per_server, 0 + 1)
    # the obligations: the first handoff ends the chain (pmcp#324, above)
    assert largest == 100 + 1, largest


async def _held_request(h: _Handoffs, kind: str, task_id: str) -> Any:
    m = h.manager
    if kind == "get":
        return await m.get_task("tasks", task_id)
    if kind == "result":
        return await m.get_task_result_with_task("tasks", task_id)
    return await m.cancel_task("tasks", task_id)


@pytest.mark.parametrize("requests", [0, 1, 3])
@pytest.mark.parametrize("overlap", [2, 3])
@pytest.mark.parametrize("trigger", ["forced-disconnect", "cancel-active-tasks"])
@pytest.mark.asyncio
async def test_retention_is_bounded_by_caps_and_requests_in_flight(
    overlap: int, requests: int, trigger: str
) -> None:
    """Generated (rev 10; rev 11 reads R live): rounds of `overlap` forced
    teardowns started back to back (each one cancels the in-flight requests
    before it), interleaved with up to `requests` in-flight
    `tasks/get`/`tasks/result`/`tasks/cancel` requests on fresh tasks, and a
    flood of new tasks in every round. After every `put`, `unpin` and `watch`,
    `_LiveBounds` checks rev 11's bounds with the requests in flight at that
    instant. Every pin and every custody is released."""
    h = _Handoffs(per_server=5, total=7, requests=requests)
    m = h.manager
    running = h.running
    kinds = ["get", "result", "cancel"]
    for round_ in range(40):
        for j in range(overlap):
            if trigger == "forced-disconnect":
                running.append(
                    asyncio.create_task(m.disconnect_server("tasks", force=True))
                )
            else:
                m.cancel_pending_requests("tasks")
                running.append(asyncio.create_task(m.cancel_active_tasks("tasks")))
            await h.settle()
            h.record(f"r{round_}-{j}")
        live = sum(1 for t in running if not t.done() and getattr(t, "_held", False))
        for i in range(requests - live):
            task_id = f"q{round_}-{i}"
            h.record(task_id)
            held = asyncio.create_task(_held_request(h, kinds[i % 3], task_id))
            held._held = True  # type: ignore[attr-defined]
            running.append(held)
            await h.settle()
        for i in range(3):
            h.record(f"f{round_}-{i}")
        await h.settle()
    await _drain(running)
    assert m._tasks.pinned == {}
    assert sum(1 for key in m._tasks if key[0] == "tasks") <= 5


def test_a_teardown_fixes_what_it_owes_before_its_first_await() -> None:
    """Structural (rev 11, round-10 codex F001): the teardown reads every
    snapshot it owes -- all its servers' -- before anything in it awaits, and
    its callers do not await before calling it. So an eviction while it waits
    cannot change what it owes. Its custody is registered and released only
    by `_custody`, used only as a `with` item; there is no teardown lock."""
    tree = ast.parse((SRC / "client" / "manager.py").read_text())
    funcs = {f.name: f for f in _functions(tree)}
    func = funcs["_cancel_tracked_for_teardown"]
    assert [a.arg for a in func.args.args] == ["self", "server_names"]
    first = func.body[0] if not isinstance(func.body[0], ast.Expr) else func.body[1]
    assert isinstance(first, ast.Assign), ast.unparse(first)
    assert "self.get_active_tasks(server_name)" in ast.unparse(first)
    assert "sorted(set(server_names))" in ast.unparse(first)
    assert not any(isinstance(n, ast.Await) for n in ast.walk(first))
    reads = [
        n
        for n in ast.walk(func)
        if isinstance(n, ast.Call) and "get_active_tasks" in ast.unparse(n.func)
    ]
    assert len(reads) == 1  # read once, at entry
    for caller in ("disconnect_server", "cancel_active_tasks"):
        body = funcs[caller]
        call = next(
            n
            for n in ast.walk(body)
            if isinstance(n, ast.Call)
            and ast.unparse(n.func) == "self._cancel_tracked_for_teardown"
        )
        earlier = [
            n
            for n in ast.walk(body)
            if isinstance(n, ast.Await)
            and n.lineno < call.lineno
            and n.value is not call
        ]
        assert earlier == [], caller
    assert "_teardown_locks" not in ast.unparse(tree)
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    custody_uses = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_custody"
    ]
    assert len(custody_uses) == 1
    assert isinstance(parents[custody_uses[0]], ast.withitem)


@pytest.mark.asyncio
async def test_a_failing_release_does_not_leak_the_other_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-9 N3: if one release raises, the other keys are still released."""
    manager, _replies = _manager("tasks")
    registry = manager._tasks
    original = registry.unpin

    def flaky(key: Any) -> Any:
        if key == ("tasks", "b"):
            original(key)
            raise RuntimeError("release failed")
        return original(key)

    monkeypatch.setattr(registry, "unpin", flaky)
    with pytest.raises(RuntimeError, match="release failed"):
        with manager._pinned([("tasks", "a"), ("tasks", "b"), ("tasks", "c")]):
            pass
    assert registry.pinned == {}


@pytest.mark.asyncio
async def test_a_teardown_skips_an_entry_whose_record_is_unexpectedly_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-9 N1 (restored in rev 12): the "a pinned entry has a record"
    invariant is an explicit skip, not an `assert`: if it were ever violated,
    the forced teardown still completes."""
    manager, replies = _manager("tasks")
    a = manager._clients["tasks"]
    for task_id in ("a", "b"):
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=None,
            connection=a,
        )
    real = manager.get_task_record

    def vanishing(server_name: str, task_id: str) -> Any:
        return None if task_id == "a" else real(server_name, task_id)

    monkeypatch.setattr(manager, "get_task_record", vanishing)
    replies[("tasks", "tasks/cancel")] = {"taskId": "b", "status": "cancelled"}
    assert await manager.cancel_active_tasks("tasks") == (1, [])


@pytest.mark.asyncio
async def test_an_owed_entry_the_cap_evicted_is_restored_by_its_pin_and_cancelled() -> (
    None
):
    """Rev 12: `b` is evicted into the teardown's custody while `a`'s cancel
    waits. At its turn the entry's pin puts `b` back in the registry, where it
    stays for its own request, and its cancel carries its context."""
    manager, _replies = _manager("tasks")
    manager._tasks.per_server = 2
    a = manager._clients["tasks"]
    for task_id in ("a", "b"):
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context={"for": task_id},
            connection=a,
        )
    sent: list[Any] = []

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        task_id = params["taskId"]
        if task_id == "a":
            for flood in ("x", "y"):
                manager._record_task(
                    "tasks",
                    McpTaskInfo(task_id=flood, status="working"),
                    requestor_context=None,
                    connection=a,
                )
            assert manager.get_task_record("tasks", "b") is None  # in custody
        else:
            assert ("tasks", task_id) in manager._tasks  # restored by its pin
        sent.append((task_id, params["task"]["requestorContext"]))
        return {"taskId": task_id, "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    assert await manager.cancel_active_tasks("tasks") == (2, [])
    assert sent == [("a", {"for": "a"}), ("b", {"for": "b"})]


# --- rev 11 (round-10 codex F001): a teardown owes what was tracked at its request


def test_teardown_snapshot_acceptance() -> None:
    """The round-10 codex seat's falsifier, as written. (1) A forced refresh
    over alpha and beta: while alpha's cancel awaits, a listing on beta evicts
    `beta::b`; rev 10 read beta's snapshot only at its turn, missed `b` and
    left it running. (2) 1,100 pinned polls, a teardown that snapshots their
    records, then every poll finishes: rev 10 retained 1,100 records with no
    request in flight."""

    def manager_for(*names: str) -> ClientManager:
        manager = ClientManager()
        for index, name in enumerate(names, 1):
            manager._clients[name] = SimpleNamespace(  # type: ignore[assignment]
                connection_id=index,
                is_remote=True,
                write_stream=object(),
                status=SimpleNamespace(name=name, server_capabilities={"tasks": {}}),
            )
        return manager

    def record(manager: ClientManager, server: str, task_id: str) -> None:
        manager._record_task(
            server,
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context={"tenant": server},
            connection=manager._clients[server],
        )

    async def later_server() -> dict[str, Any]:
        manager = manager_for("alpha", "beta")
        record(manager, "alpha", "a")
        record(manager, "beta", "b")
        sent = []

        async def send(connection: Any, method: str, params: Any, **kwargs: Any) -> Any:
            if method == "tasks/list":
                return {
                    "tasks": [
                        {"taskId": f"new-{i}", "status": "working"} for i in range(100)
                    ]
                }
            assert method == "tasks/cancel"
            server = connection.status.name
            sent.append((server, params["taskId"]))
            if server == "alpha":
                await manager.list_tasks("beta")
            return {"taskId": params["taskId"], "status": "cancelled"}

        manager._send_request = send  # type: ignore[method-assign, assignment]
        result = await manager.cancel_active_tasks()
        return {"beta_cancelled": ("beta", "b") in sent, "result": result}

    async def shrinking_request_count() -> dict[str, Any]:
        manager = manager_for("tasks")
        release = asyncio.Event()
        poll_started = asyncio.Event()
        teardown_started = asyncio.Event()
        polls = []
        teardown = None

        async def send(connection: Any, method: str, params: Any, **kwargs: Any) -> Any:
            if method == "tasks/get":
                poll_started.set()
                await release.wait()
                return {"taskId": params["taskId"], "status": "working"}
            assert method == "tasks/cancel"
            teardown_started.set()
            await asyncio.Event().wait()

        manager._send_request = send  # type: ignore[method-assign, assignment]
        try:
            for index in range(1100):
                task_id = f"q{index:04}"
                record(manager, "tasks", task_id)
                poll_started.clear()
                polls.append(asyncio.create_task(manager.get_task("tasks", task_id)))
                await asyncio.wait_for(poll_started.wait(), 5)
            teardown = asyncio.create_task(manager.cancel_active_tasks("tasks"))
            await asyncio.wait_for(teardown_started.wait(), 5)
            release.set()
            await asyncio.wait_for(asyncio.gather(*polls), 10)
            requests = sum(not task.done() for task in polls)
            per_server = manager._tasks.per_server
            total = manager._tasks.total
            return {
                "R": requests,
                "T": 1,
                "retained": len(manager._tasks),
                "server_bound": max(
                    per_server, requests + max(per_server, requests + 1) + 1
                ),
                "total_bound": max(total, requests + max(per_server, requests + 1) + 1),
            }
        finally:
            running = polls + ([teardown] if teardown is not None else [])
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            assert manager._tasks.pinned == {}

    async def scenario() -> None:
        cancellation = await later_server()
        retention = await shrinking_request_count()
        assert (
            cancellation["beta_cancelled"]
            and retention["retained"] <= retention["server_bound"]
            and retention["retained"] <= retention["total_bound"]
        ), (cancellation, retention)

    asyncio.run(scenario())


def test_a_custody_keeps_an_evicted_owed_record_and_never_grows() -> None:
    registry = TaskRegistry(per_server=1)
    registry.put(
        McpTaskRecord(
            server_name="s", task_id="a", status="working", requestor_context=OLD
        )
    )
    custody = registry.watch(list(registry.values()))
    assert len(custody) == 1 and ("s", "a") in custody
    registry.put(McpTaskRecord(server_name="s", task_id="b", status="working"))
    assert ("s", "a") not in registry  # evicted, into the custody
    assert custody.held(("s", "a")).requestor_context == OLD
    assert ("s", "b") not in custody  # never owed: a custody never grows
    registry.put(McpTaskRecord(server_name="s", task_id="c", status="working"))
    assert len(custody) == 1 and ("s", "b") not in custody
    assert registry.held(("s", "a")) is custody.held(("s", "a"))
    assert registry.held(("s", "b")) is None
    registry.unwatch(custody)
    assert registry.held(("s", "a")) is None and registry.watching == 0
    with pytest.raises(ValueError):
        registry.unwatch(custody)  # an unbalanced release is a bug


def test_a_custody_keeps_the_context_through_a_context_less_re_eviction() -> None:
    """Evicted holding NEW, re-tracked with no context, evicted again: the
    re-track merges with the custody's record (`_record_task`, rev 11), so the
    custody still holds NEW, with the later record's status."""
    manager, _replies = _manager("tasks")
    registry = manager._tasks
    registry.per_server = 1
    a = manager._clients["tasks"]

    def record(task_id: str, status: str, context: Any) -> None:
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status=status),
            requestor_context=context,
            connection=a,
        )

    record("t", "working", NEW)
    custody = registry.watch(list(registry.values()))
    record("x", "working", None)  # evicts t into the custody
    record("t", "completed", None)  # re-tracked with no context of its own
    assert manager.get_task_record("tasks", "t").requestor_context == NEW  # type: ignore[union-attr]
    record("y", "working", None)  # evicts t again
    held = custody.held(("tasks", "t"))
    assert held.status == "completed" and held.requestor_context == NEW
    registry.unwatch(custody)
    record("t", "working", None)  # nothing owes it any more: nothing to merge
    assert manager.get_task_record("tasks", "t").requestor_context is None  # type: ignore[union-attr]


def test_every_custody_owing_a_key_holds_its_latest_record() -> None:
    """Two teardowns owe `t`, from different snapshots, and `t` changes again
    after both: its eviction reaches both, so once the registry gives it up
    they hold the same, latest record. Each teardown's own fallback is
    current, and the merge may take it from either (rev 11)."""
    registry = TaskRegistry(per_server=1)
    old = McpTaskRecord(
        server_name="s", task_id="t", status="working", requestor_context=OLD
    )
    registry.put(old)
    early = registry.watch([old])
    newer = McpTaskRecord(
        server_name="s", task_id="t", status="working", requestor_context=NEW
    )
    registry.put(newer)  # re-recorded in place: no eviction
    late = registry.watch([newer])
    assert early.held(("s", "t")) is old and late.held(("s", "t")) is newer
    newest = McpTaskRecord(
        server_name="s", task_id="t", status="completed", requestor_context=NEW
    )
    registry.put(newest)  # in place again: neither custody has seen it
    registry.put(McpTaskRecord(server_name="s", task_id="y", status="working"))
    # the eviction reaches both, so each teardown's own fallback is current
    assert early.held(("s", "t")) is newest and late.held(("s", "t")) is newest
    assert registry.held(("s", "t")) is newest
    registry.unwatch(late)
    registry.unwatch(early)


@pytest.mark.asyncio
async def test_a_teardown_holds_one_pin_at_a_time_and_its_custody_until_it_ends() -> (
    None
):
    """Rev 11: the teardown pins only the entry whose `tasks/cancel` is in
    flight, and its custody is registered from its request to its end."""
    manager, _replies = _manager("tasks")
    a = manager._clients["tasks"]
    for task_id in ("a", "b", "c"):
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=None,
            connection=a,
        )
    seen: list[tuple[dict[Any, int], int]] = []

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        seen.append((manager._tasks.pinned, manager._tasks.watching))
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    assert await manager.cancel_active_tasks("tasks") == (3, [])
    assert seen == [
        ({("tasks", "a"): 1}, 1),
        ({("tasks", "b"): 1}, 1),
        ({("tasks", "c"): 1}, 1),
    ]
    assert manager._tasks.watching == 0


@pytest.mark.parametrize("ending", ["exception", "cancelled"])
@pytest.mark.asyncio
async def test_a_teardowns_custody_is_released_however_it_ends(ending: str) -> None:
    manager, _replies = _manager("tasks")
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=None,
        connection=manager._clients["tasks"],
    )
    entered = asyncio.Event()

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        assert manager._tasks.watching == 1
        entered.set()
        if ending == "exception":
            raise RuntimeError("downstream failed")
        await asyncio.Event().wait()

    manager._send_request = send  # type: ignore[method-assign, assignment]
    running = asyncio.ensure_future(manager.cancel_active_tasks("tasks"))
    await entered.wait()
    if ending == "cancelled":
        running.cancel()
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await running
    assert manager._tasks.watching == 0 and manager._tasks.pinned == {}


A_CTX = {"tenant": "alpha"}
B1_CTX = {"tenant": "b1"}
B2_CTX = {"tenant": "b2"}


class _Interleaving:
    """Two task servers, alpha and beta, at small caps (2 per server, 3 in
    total), whose `tasks/*` requests wait until the test answers them (real
    `PendingRequest`s, so a forced disconnect cancels them). An independent
    model -- not the registry's custody -- says, at every instant, the newest
    context pmcp holds for each task: a supplied context sets it, and an
    eviction loses it only if no teardown in flight owed the task. Every
    `tasks/cancel` a teardown sends is checked against it."""

    def __init__(self) -> None:
        self.manager, _ = _manager("alpha", "beta")
        m = self.manager
        m._tasks.per_server = 2
        m._tasks.total = 3
        self.running: list[asyncio.Task[Any]] = []
        self.waiting: list[tuple[str, str, asyncio.Future[Any]]] = []
        self.owes: list[tuple[asyncio.Task[Any], set[tuple[str, str]]]] = []
        self.model: dict[tuple[str, str], Any] = {}
        self.sent: list[tuple[str, str]] = []
        self.ids = 0
        self.bounds = _LiveBounds(
            m._tasks, lambda: sum(1 for t in self.running if not t.done()), m
        )
        registry = m._tasks
        for name in ("put", "unpin"):
            original = getattr(registry, name)

            def wrapped(*a: Any, _original: Any = original, **k: Any) -> Any:
                out = _original(*a, **k)
                self.note_losses()
                return out

            setattr(registry, name, wrapped)
        self.record("alpha", "a1", A_CTX)
        self.record("beta", "b1", B1_CTX)
        self.record("beta", "b2", B2_CTX)

        async def send(
            managed: ManagedClient, method: str, params: Any, **_: Any
        ) -> Any:
            server = managed.status.name
            if method == "tasks/cancel":
                key = (server, params["taskId"])
                context = params.get("task", {}).get("requestorContext")
                assert context == self.model.get(key), (key, context, self.model)
                self.sent.append(key)
            self.ids += 1
            future = asyncio.get_running_loop().create_future()
            managed.pending_requests[self.ids] = PendingRequest(
                request_id=self.ids,
                server_name=server,
                tool_id="",
                started_at=0.0,
                last_heartbeat=0.0,
                timeout_ms=30000,
                future=future,
            )
            self.waiting.append((method, params["taskId"], future))
            return await future

        m._send_request = send  # type: ignore[method-assign, assignment]

    def owed_now(self) -> set[tuple[str, str]]:
        return {k for task, keys in self.owes if not task.done() for k in keys}

    def note_losses(self) -> None:
        owed = self.owed_now()
        for key in list(self.model):
            if key not in self.manager._tasks and key not in owed:
                self.model[key] = None  # evicted while nothing owed it

    def record(self, server: str, task_id: str, context: Any) -> None:
        self.manager._record_task(
            server,
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=context,
            connection=self.manager._clients[server],
        )
        key = (server, task_id)
        if context is not None:
            self.model[key] = context
        elif key not in self.model:
            self.model[key] = None

    def teardown(self, kind: str) -> None:
        m = self.manager
        servers = ["beta"] if kind == "disconnect" else ["alpha", "beta"]
        owed = {
            (t.server_name, t.task_id) for s in servers for t in m.get_active_tasks(s)
        }
        call = (
            m.disconnect_server("beta", force=True)
            if kind == "disconnect"
            else m.cancel_active_tasks()
        )
        task = asyncio.ensure_future(call)
        self.running.append(task)
        self.owes.append((task, owed))

    def answer(self, method: str) -> bool:
        for i, (sent_method, task_id, future) in enumerate(self.waiting):
            if sent_method == method and not future.done():
                del self.waiting[i]
                status = "cancelled" if method == "tasks/cancel" else "working"
                future.set_result({"taskId": task_id, "status": status})
                return True
        return False

    async def settle(self) -> None:
        for _ in range(8):
            await asyncio.sleep(0)


#: The events, each a step the test takes; an ordering is a permutation.
#: T: a forced teardown; P/p: a poll on beta::b1 starts / is answered (R grows,
#: then shrinks); F: a listing floods beta with two new tasks; N: a newer call
#: supplies a context for beta::b2; S: the oldest waiting `tasks/cancel` is
#: answered. Constraints: p after P, S after T.
_EVENTS = ("T", "P", "p", "F", "N", "S")


def _orderings() -> list[tuple[str, ...]]:
    import itertools

    return [
        order
        for order in itertools.permutations(_EVENTS)
        if order.index("p") > order.index("P") and order.index("S") > order.index("T")
    ]


async def _run_ordering(order: tuple[str, ...], kind: str) -> None:
    h = _Interleaving()
    m = h.manager
    flood = 0
    for event in order:
        if event == "T":
            h.teardown(kind)
        elif event == "P":
            h.running.append(asyncio.ensure_future(m.get_task("beta", "b1")))
        elif event == "p":
            h.answer("tasks/get")
        elif event == "F":
            flood += 1
            for i in range(2):
                h.record("beta", f"f{flood}-{i}", None)
        elif event == "N":
            h.record("beta", "b2", NEW)
        elif event == "S":
            h.answer("tasks/cancel")
        await h.settle()
    for _ in range(50):  # drain: answer everything still waiting
        if all(t.done() for t in h.running):
            break
        if not (h.answer("tasks/cancel") or h.answer("tasks/get")):
            await asyncio.sleep(0)
        await h.settle()
    results = await asyncio.gather(*h.running, return_exceptions=True)
    assert m._tasks.pinned == {} and getattr(m._tasks, "watching", 0) == 0
    outcome = {id(t): r for t, r in zip(h.running, results)}
    for task, owed in h.owes:
        result = outcome[id(task)]
        if kind == "cancel-active-tasks":
            # every owed task cancelled or found finished: none silently left
            assert result == (len(owed), []), (result, owed)
        else:  # the count is of pending requests it cancelled (the poll's)
            assert isinstance(result, asyncio.CancelledError) or (
                result[0] is True and result[2] is None
            ), result
        for key in owed:
            assert key in h.sent, (key, h.sent)


@pytest.mark.parametrize("kind", ["cancel-active-tasks", "disconnect"])
@pytest.mark.asyncio
async def test_every_interleaving_cancels_what_was_owed_within_the_live_bound(
    kind: str,
) -> None:
    """Generated (rev 11, round-10 codex F001): all 180 orderings of a forced
    teardown (both servers, or a forced disconnect of beta), a poll that
    starts and later finishes (R grows, then shrinks while the teardown holds
    its custody), a flood on beta (cross-server eviction while alpha's cancel
    waits), a newer context for an owed task, and one cancel answered. For
    every ordering:
    - after every `put`, `unpin` and `watch`, rev 11's bounds hold with R
      read live, and every custody keeps its request-time size;
    - every task tracked and unfinished when the teardown was requested gets a
      `tasks/cancel` -- an eviction while it waits changes nothing;
    - every cancel carries the newest context pmcp held at that moment
      (the independent model);
    - every pin and custody is released."""
    orders = _orderings()
    assert len(orders) == 180
    for order in orders:
        try:
            await _run_ordering(order, kind)
        except AssertionError as e:
            raise AssertionError(f"{kind} {''.join(order)}: {e}") from e


# --- rev 12 (round-11 F001): an owed task is a tracked task, for every reader


@pytest.mark.asyncio
async def test_owed_task_in_custody_is_cancelled_despite_a_concurrent_disconnect() -> (
    None
):
    """The round-11 claude seat's falsifier, as written: a task evicted into a
    forced refresh's custody was invisible to `get_active_tasks`, so a
    concurrent forced disconnect of its server neither owed nor cancelled it;
    the refresh then reached it, found the server gone, and skipped it. On
    rev 11: `((1, []), [('alpha', 'a1'), ('beta', 'f0'), ('beta', 'f1')])`."""
    manager, _ = _manager("alpha", "beta")
    manager._tasks.per_server = 2
    for server, task_id in (("alpha", "a1"), ("beta", "b1")):
        manager._record_task(
            server,
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context={"tenant": server},
            connection=manager._clients[server],
        )
    gate = asyncio.get_running_loop().create_future()
    sent: list[tuple[str, str]] = []

    async def send(managed: Any, method: str, params: Any, **_: Any) -> Any:
        assert method == "tasks/cancel"
        sent.append((managed.status.name, params["taskId"]))
        if managed.status.name == "alpha":
            await gate
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    # the forced refresh: owes alpha::a1 and beta::b1 from its request
    refresh = asyncio.create_task(manager.cancel_active_tasks())
    for _ in range(5):
        await asyncio.sleep(0)
    assert sent == [("alpha", "a1")]
    # while alpha's cancel waits, two new beta tasks evict b1 (into the custody)
    for task_id in ("f0", "f1"):
        manager._record_task(
            "beta",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=None,
            connection=manager._clients["beta"],
        )
    assert manager.get_task_record("beta", "b1") is None
    # another caller force-disconnects beta: it owes only what the registry shows
    assert (await manager.disconnect_server("beta", force=True))[0] is True
    gate.set_result(None)
    result = await refresh
    assert ("beta", "b1") in sent, (result, sent)


@pytest.mark.asyncio
async def test_a_non_forced_disconnect_refuses_on_a_task_a_teardown_owes() -> None:
    """The seat's variant: with `per_server=1`, a finished beta task evicts
    `b1` into a forced refresh's custody while alpha's cancel waits. A
    non-forced disconnect of beta must still refuse -- `b1` is running
    downstream and owed a cancel -- and the refresh then cancels it, with its
    context. On rev 11 the disconnect returned `(True, 0, None)`."""
    manager, _ = _manager("alpha", "beta")
    manager._tasks.per_server = 1
    for server, task_id in (("alpha", "a1"), ("beta", "b1")):
        manager._record_task(
            server,
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context={"tenant": server},
            connection=manager._clients[server],
        )
    gate = asyncio.get_running_loop().create_future()
    sent: list[tuple[str, str, Any]] = []

    async def send(managed: Any, method: str, params: Any, **_: Any) -> Any:
        sent.append(
            (managed.status.name, params["taskId"], params["task"]["requestorContext"])
        )
        if managed.status.name == "alpha":
            await gate
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    refresh = asyncio.create_task(manager.cancel_active_tasks())
    for _ in range(5):
        await asyncio.sleep(0)
    manager._record_task(
        "beta",
        McpTaskInfo(task_id="done", status="completed"),
        requestor_context=None,
        connection=manager._clients["beta"],
    )
    assert manager.get_task_record("beta", "b1") is None  # in the custody
    assert [t.task_id for t in manager.get_active_tasks("beta")] == ["b1"]
    ok, _n, message = await manager.disconnect_server("beta")
    assert ok is False and message is not None and "refused" in message
    gate.set_result(None)
    assert await refresh == (2, [])
    assert sent == [
        ("alpha", "a1", {"tenant": "alpha"}),
        ("beta", "b1", {"tenant": "beta"}),
    ]


def test_an_owed_record_counts_only_on_its_servers_current_connection() -> None:
    """`get_tracked_tasks` adds what custodies owe only while the record's
    connection is still its server's (rev 12): a removed or replaced
    connection's task is not this server's state, and must neither refuse a
    disconnect of the replacement nor count as remaining after a refresh."""
    manager, _ = _manager("tasks")
    registry = manager._tasks
    registry.per_server = 1
    a = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=None,
        connection=a,
    )
    custody = registry.watch(list(registry.values()))
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="x", status="working"),
        requestor_context=None,
        connection=a,
    )
    assert sorted(t.task_id for t in manager.get_active_tasks("tasks")) == ["t", "x"]
    assert [t.task_id for t in manager.get_active_tasks()] == ["t", "x"]
    replacement = _task_server(manager, {})
    manager._clients["tasks"] = replacement
    assert [t.task_id for t in manager.get_active_tasks("tasks")] == ["x"]
    del manager._clients["tasks"]
    assert [t.task_id for t in manager.get_active_tasks("tasks")] == ["x"]
    registry.unwatch(custody)


def test_owed_records_are_only_what_the_registry_gave_up_once_each() -> None:
    """`owed_records` lists an owed key only while the registry does not hold
    it, and once however many custodies owe it: `get_tracked_tasks` adds it
    to the registry's records, so anything else would count a task twice."""
    registry = TaskRegistry(per_server=2)
    for task_id in ("a", "b"):
        _put(registry, "s", task_id, "working")
    first = registry.watch(list(registry.values()))
    second = registry.watch(list(registry.values()))
    assert registry.owed_records("s") == []  # both still in the registry
    _put(registry, "s", "c", "working")  # evicts a, owed by both
    assert [r.task_id for r in registry.owed_records("s")] == ["a"]
    assert [r.task_id for r in registry.owed_records()] == ["a"]
    assert registry.owed_records("other") == []
    registry.unwatch(second)
    registry.unwatch(first)
    assert registry.owed_records() == []


def _custody_holding_t(context: Any) -> tuple[ClientManager, TaskCustody]:
    """A manager whose one record `t` (context `context`) the cap has moved
    into a running teardown's custody, `x` taking its place."""
    manager, _ = _manager("tasks")
    registry = manager._tasks
    registry.per_server = 1
    a = manager._clients["tasks"]
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="t", status="working"),
        requestor_context=context,
        connection=a,
    )
    custody = registry.watch(list(registry.values()))
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="x", status="working"),
        requestor_context=None,
        connection=a,
    )
    assert ("tasks", "t") not in registry and registry.held(("tasks", "t"))
    return manager, custody


def test_a_pin_restores_a_custodys_record_and_keeps_it_through_the_caps() -> None:
    """Rev 12 (round-11 codex F001): a request that pins a key held only in a
    custody puts the record back in the registry, pinned first, so the caps
    re-applied by the restore cannot take it straight back -- even when every
    other record of the server is pinned too -- and releasing the custody
    then drops nothing the request needs."""
    manager, custody = _custody_holding_t(OLD)
    registry = manager._tasks
    registry.pin(("tasks", "x"))  # another request holds x
    with manager._pinned([("tasks", "t")]):
        assert ("tasks", "t") in registry
        registry.unwatch(custody)  # the teardown ends meanwhile
        assert manager.get_task_record("tasks", "t").requestor_context == OLD  # type: ignore[union-attr]
    registry.unpin(("tasks", "x"))


def test_a_pin_never_restores_another_connections_record() -> None:
    """The restore is for the server's current connection only (rev 5): a
    custody record from a connection that has since been replaced is not the
    replacement's state, so a request on the replacement does not see it."""
    manager, custody = _custody_holding_t(OLD)
    replacement = _task_server(manager, {})
    manager._clients["tasks"] = replacement
    with manager._pinned([("tasks", "t")]):
        assert ("tasks", "t") not in manager._tasks
        assert manager.get_task_record("tasks", "t") is None
    manager._tasks.unwatch(custody)


def test_every_reader_of_task_state_goes_through_get_tracked_tasks() -> None:
    """Structural (rev 12, round-11 F001): the registry and the custodies are
    read only by `get_tracked_tasks` (the one source of tracked tasks, custody
    included), `get_task_record` (one key, the registry's: Design decision
    5's user-facing contract) and `_record_task`'s merge. Every other reader --
    snapshots, refusal gates, refresh counts -- calls `get_tracked_tasks` or
    `get_active_tasks`, which is built on it."""

    def reads(node: ast.AST, parent: ast.AST | None) -> str | None:
        if not (isinstance(node, ast.Attribute) and node.attr == "_tasks"):
            return None
        label = (
            parent.attr if isinstance(parent, ast.Attribute) else type(parent).__name__
        )
        return label if label in _REGISTRY_READS else None

    assert _owned(reads) == {
        ("client/manager.py", "get_tracked_tasks", "items"),
        ("client/manager.py", "get_tracked_tasks", "owed_records"),
        ("client/manager.py", "get_task_record", "get"),
        ("client/manager.py", "_record_task", "get"),
        ("client/manager.py", "_record_task", "held"),
        # the pin's restore: is the key in the registry, else a custody's
        ("client/manager.py", "_pinned", "Compare"),
        ("client/manager.py", "_pinned", "held"),
        # the release's restore (rev 13): tracked nowhere else?
        ("client/manager.py", "_custody", "Compare"),
        ("client/manager.py", "_custody", "held"),
    }
    # `get_task_record` (the registry alone) is called only where the key is
    # pinned, and so restored from a custody first: the pinned requests and
    # the teardown's per-entry pin
    tree = ast.parse((SRC / "client" / "manager.py").read_text())
    callers = {
        func.name
        for func in _functions(tree)
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute) and node.attr == "get_task_record"
    }
    assert callers == {
        "get_task",
        "get_task_result_with_task",
        "cancel_task",
        "_cancel_tracked_for_teardown",
    }, callers
    assert _where("get_task_record") <= {
        ("client/manager.py", name) for name in callers
    }

    def custodies(node: ast.AST, _parent: ast.AST | None) -> str | None:
        if isinstance(node, ast.Attribute) and node.attr == "_custodies":
            return "_custodies"
        return None

    assert _owned(custodies) == {
        ("client/task_registry.py", "__init__", "_custodies"),
        ("client/task_registry.py", "watch", "_custodies"),
        ("client/task_registry.py", "unwatch", "_custodies"),
        ("client/task_registry.py", "held", "_custodies"),
        ("client/task_registry.py", "owed_records", "_custodies"),
        ("client/task_registry.py", "watching", "_custodies"),
        ("client/task_registry.py", "_evict", "_custodies"),
    }


class _Overlap:
    """Two task servers, alpha and beta (2 records each, 4 in total), whose
    `tasks/*` requests wait until the test answers them, as real
    `PendingRequest`s, so a forced disconnect cancels them. The test keeps its
    own model, never read from the registry or the custodies:
    - `active`: the tasks pmcp tracks or owes, running downstream;
    - `context`: the newest context pmcp holds for each (None once lost);
    - `owes`: each teardown's obligation, the model's `active` tasks of its
      servers when it was requested;
    - `custody`: tasks the cap evicted while a running teardown owed them.
      A request that pins one (a poll, or the teardown's own cancel) puts it
      back in the registry; a re-track or reply re-records it; when its
      teardown releases its custody -- however it ended -- it goes back to
      the registry as an ordinary record (rev 13), where the cap may evict it
      again like any record nothing owes.
    A task leaves `active`, losing its context, when the cap evicts it while
    nothing owes or pins it (Design decision 5's residual; `lost`), when its
    cancel is answered (`answered`), or when its server's connection is
    removed (`removed`);
    - `late`: tasks recorded on beta while a forced disconnect of beta was
      already running. A forced disconnect cancels what was tracked when it
      started; one recorded after that keeps running once it removes the
      connection, whoever else owes it -- the residual stated since rev 3
      (README, SECURITY). Main also leaves it running; its
      `cancel_active_tasks` reports it as an error, where rev 5 skips it.
    - `unsent_at_removal`: Consiliency/pmcp#324's residual. A forced
      disconnect whose own teardown is interrupted still disconnects, without
      the cancels it had not sent. The tasks of that server not yet sent a
      `tasks/cancel` are recorded at the moment of removal (by wrapping
      `_forget_disconnected`), with the owing teardowns still running then.
      Only those tasks, only for those teardowns, and only if that disconnect
      then ended interrupted, are exempt -- and for a teardown that had ended
      earlier, only if, when it released its custody, a running teardown
      owed the same unsent task (`handed`: the obligation passed on, R3) and
      that one is excused. A teardown that ended without sending a task's
      cancel and handed it to nobody is never excused.
    An aborted teardown is not exempt (rev 13, round-12 F001): each task it
    owed was answered, is still tracked, was lost to the cap only after its
    release (nothing owed it then), or is one of the residuals above. The
    model learns when a release happens from `unwatch` (the moment, not the
    state)."""

    def __init__(self) -> None:
        self.manager, _ = _manager("alpha", "beta")
        m = self.manager
        m._tasks.per_server = 2
        m._tasks.total = 4
        self.running: list[asyncio.Task[Any]] = []
        self.waiting: list[tuple[str, str, str, asyncio.Future[Any]]] = []
        self.owes: list[tuple[asyncio.Task[Any], set[tuple[str, str]]]] = []
        self.active: set[tuple[str, str]] = set()
        self.context: dict[tuple[str, str], Any] = {}
        self.custody: set[tuple[str, str]] = set()
        self.polls: list[tuple[str, str]] = []  # keys pinned by polls in flight
        self.sent: list[tuple[str, str]] = []
        self.refusals: list[tuple[bool, bool]] = []  # (refused, model active)
        self.forced: list[asyncio.Task[Any]] = []
        self.forced_server: dict[asyncio.Task[Any], str] = {}
        self.late: set[tuple[str, str]] = set()
        self.lost: set[tuple[str, str]] = set()
        self.answered: set[tuple[str, str]] = set()
        self.removed: set[tuple[str, str]] = set()
        self.released: set[asyncio.Task[Any]] = set()
        self.handed: dict[
            tuple[asyncio.Task[Any], tuple[str, str]], set[asyncio.Task[Any]]
        ] = {}
        self.ids = 0
        self.floods = 0
        self.bounds = _LiveBounds(
            m._tasks, lambda: sum(1 for t in self.running if not t.done()), m
        )
        registry = m._tasks
        evict = registry._evict

        def evicting(*a: Any, **k: Any) -> Any:
            victim = evict(*a, **k)
            if victim is not None:
                key = (victim.server_name, victim.task_id)
                if key in self.owed_now():
                    self.custody.add(key)
                else:
                    self.lose(key)  # evicted while nothing owed it
            return victim

        registry._evict = evicting  # type: ignore[method-assign]
        unwatch = registry.unwatch

        def releasing(custody: Any) -> Any:
            out = unwatch(custody)
            task = asyncio.current_task()
            if task is not None:
                self.released.add(task)
                # what this teardown still owed, unsent, that a running one
                # owes too: the obligation passes to it (R3), not dropped
                mine = next((o for t, o in self.owes if t is task), set())
                for t, keys in self.owes:
                    if t is task or t.done() or t in self.released:
                        continue
                    for key in (mine & keys) - set(self.sent):
                        self.handed.setdefault((task, key), set()).add(t)
            owed = self.owed_now()
            for key in list(self.custody):
                if key not in owed:
                    self.custody.discard(key)  # back to the registry, tracked
            return out

        registry.unwatch = releasing  # type: ignore[method-assign]
        # Consiliency/pmcp#324: record, at the moment a forced disconnect
        # removes its server, which of that server's tasks had not been sent a
        # `tasks/cancel` yet, and which owing teardowns were still running.
        # Only those tasks, for those teardowns, and only if that disconnect
        # then ended interrupted, are exempt at the end (round-1 N2 and
        # round-2 N1 on Consiliency/pmcp#376).
        self.unsent_at_removal: list[
            tuple[
                asyncio.Task[Any] | None,
                set[tuple[str, str]],
                set[asyncio.Task[Any]],
            ]
        ] = []
        forget = m._forget_disconnected

        def forgetting(name: str, config: Any) -> Any:
            unsent = {k for k in self.active | self.custody if k[0] == name} - set(
                self.sent
            )
            running = {task for task, _owed in self.owes if not task.done()}
            self.unsent_at_removal.append((asyncio.current_task(), unsent, running))
            return forget(name, config)

        m._forget_disconnected = forgetting  # type: ignore[method-assign]
        self.record("alpha", "a1", {"tenant": "a1"})
        self.record("beta", "b1", {"tenant": "b1"})

        async def send(
            managed: ManagedClient, method: str, params: Any, **_: Any
        ) -> Any:
            server = managed.status.name
            task_id = params.get("taskId", "")
            key = (server, task_id)
            if method != "tasks/list":
                context = params.get("task", {}).get("requestorContext")
                assert context == self.context.get(key), (method, key, context)
                self.custody.discard(key)  # pinned: back in the registry
            if method == "tasks/cancel":
                self.sent.append(key)
            self.ids += 1
            future = asyncio.get_running_loop().create_future()
            managed.pending_requests[self.ids] = PendingRequest(
                request_id=self.ids,
                server_name=server,
                tool_id="",
                started_at=0.0,
                last_heartbeat=0.0,
                timeout_ms=30000,
                future=future,
            )
            self.waiting.append((method, server, task_id, future))
            return await future

        m._send_request = send  # type: ignore[method-assign, assignment]

    def owed_now(self) -> set[tuple[str, str]]:
        return {
            k
            for task, keys in self.owes
            if not task.done() and task not in self.released
            for k in keys
        }

    def lose(self, key: tuple[str, str], why: str = "cap") -> None:
        if key in self.active:
            (self.lost if why == "cap" else self.removed).add(key)
        self.active.discard(key)
        self.custody.discard(key)
        self.context[key] = None

    def ended(self, _task: asyncio.Task[Any]) -> None:
        pass  # the release (unwatch) is where the model acts

    def record(self, server: str, task_id: str, context: Any) -> None:
        key = (server, task_id)
        if server == "beta" and any(not t.done() for t in self.forced):
            self.late.add(key)
        self.active.add(key)
        self.custody.discard(key)
        if context is not None:
            self.context[key] = context
        else:
            self.context.setdefault(key, None)
        self.manager._record_task(
            server,
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=context,
            connection=self.manager._clients[server],
        )

    def teardown(self, call: Any, servers: list[str]) -> asyncio.Task[Any]:
        owed = {k for k in self.active if k[0] in servers}
        task = asyncio.ensure_future(call)
        task.add_done_callback(self.ended)
        self.running.append(task)
        self.owes.append((task, owed))
        return task

    def poll(self, call: Any, key: tuple[str, str]) -> None:
        self.polls.append(key)
        self.custody.discard(key)  # its pin puts the record back
        task = asyncio.ensure_future(call)

        def done(_t: asyncio.Task[Any]) -> None:
            self.polls.remove(key)

        task.add_done_callback(done)
        self.running.append(task)

    def answer(self) -> bool:
        while self.waiting:
            method, server, task_id, future = self.waiting.pop(0)
            if future.done():
                continue  # a forced disconnect cancelled it
            key = (server, task_id)
            if method == "tasks/cancel":
                future.set_result({"taskId": task_id, "status": "cancelled"})
                self.active.discard(key)
                self.answered.add(key)
            elif method == "tasks/list":
                self.active.add(("beta", "b1"))
                self.custody.discard(("beta", "b1"))
                self.context.setdefault(("beta", "b1"), None)
                future.set_result({"tasks": [{"taskId": "b1", "status": "working"}]})
            elif method == "tasks/result":
                self.active.add(key)
                task = {"taskId": task_id, "status": "working"}
                future.set_result({"task": task, "result": {}})
            else:
                self.active.add(key)
                future.set_result({"taskId": task_id, "status": "working"})
            return True
        return False

    async def step(self, event: str) -> None:
        m = self.manager
        for server in ("alpha", "beta"):
            if server not in m._clients:  # removed: its tasks are dropped
                for key in [k for k in self.active | self.custody if k[0] == server]:
                    self.lose(key, "removed")
        beta = "beta" in m._clients
        alpha = "alpha" in m._clients
        if event == "T":
            self.teardown(m.cancel_active_tasks(), ["alpha", "beta"])
        elif event == "D" and beta:
            task = self.teardown(m.disconnect_server("beta", force=True), ["beta"])
            self.forced.append(task)
            self.forced_server[task] = "beta"
        elif event == "d" and beta:
            had = any(k[0] == "beta" for k in self.active)
            ok, _n, _msg = await m.disconnect_server("beta")
            self.refusals.append((not ok, had))
        elif event == "X" and alpha:
            # a forced disconnect of the *other* server: it cancels whatever
            # request a refresh has in flight on alpha (round-12 F001)
            task = self.teardown(m.disconnect_server("alpha", force=True), ["alpha"])
            self.forced_server[task] = "alpha"
        elif event == "A":
            live = [t for t, _o in self.owes if not t.done()]
            if live:
                live[-1].cancel()  # abort the newest running teardown
        elif event == "F" and beta:
            self.floods += 1
            for i in range(2):
                self.record("beta", f"f{self.floods}-{i}", None)
        elif event == "G" and beta:
            self.poll(m.get_task("beta", "b1"), ("beta", "b1"))
        elif event == "R" and beta:
            self.poll(m.get_task_result_with_task("beta", "b1"), ("beta", "b1"))
        elif event == "L" and beta:
            self.running.append(asyncio.ensure_future(m.list_tasks("beta")))
        elif event == "S":
            self.answer()
        for _ in range(8):
            await asyncio.sleep(0)


async def _run_overlap(order: tuple[str, ...]) -> None:
    h = _Overlap()
    m = h.manager
    for event in order:
        await h.step(event)
    for _ in range(200):  # drain: answer everything still waiting
        if all(t.done() for t in h.running):
            break
        if not h.answer():
            await asyncio.sleep(0)
        for _ in range(8):
            await asyncio.sleep(0)
    await asyncio.gather(*h.running, return_exceptions=True)
    assert all(t.done() for t in h.running)
    assert m._tasks.pinned == {} and getattr(m._tasks, "watching", 0) == 0
    # a non-forced disconnect never succeeds while a task runs downstream
    for refused, had_active in h.refusals:
        assert refused or not had_active, h.refusals
    # every task a teardown owed at its request: a teardown that ran to its
    # end sent it a tasks/cancel (or another teardown that owed it did); an
    # aborted one -- cancelled caller, error, or a disconnect of another
    # server -- left it answered, tracked, or lost to the cap only after its
    # release (rev 13: abort is not discharge)
    for server in ("alpha", "beta"):
        if server not in m._clients:
            for key in [k for k in h.active if k[0] == server]:
                h.lose(key, "removed")
    # Consiliency/pmcp#324 (merged after the plan's base): a forced disconnect
    # whose own teardown is interrupted still disconnects, synchronously,
    # without the cancels it had not sent yet. The same on main: its
    # `cancel_task` loop stops there too. A task of that server pmcp had not
    # cancelled by then keeps running remotely, whichever teardown owed it.
    cut_short = {
        (owner, key)
        for task, unsent, running in h.unsent_at_removal
        if task in h.forced_server
        and (task.cancelled() or task.exception() is not None)
        for key in unsent
        for owner in running
    }
    # an earlier teardown that ended while a running one owed the same unsent
    # task handed the obligation on: excused exactly when that one is
    while True:
        more = {
            (owner, key)
            for (owner, key), heirs in h.handed.items()
            if (owner, key) not in cut_short
            and any((heir, key) in cut_short for heir in heirs)
        }
        if not more:
            break
        cut_short |= more
    for task, owed in h.owes:
        aborted = task.cancelled() or task.exception() is not None
        for key in owed:
            if key in h.removed and key in h.late:
                continue  # the stated residual (see `_Overlap`)
            if key in h.removed and (task, key) in cut_short:
                continue  # pmcp#324's interrupted forced disconnect (above)
            if not aborted:
                assert key in h.sent, (key, owed, h.sent)
                continue
            assert (
                key in h.answered
                or key in h.active
                or key in h.lost
                or (key in h.removed and key in h.sent)
            ), ("aborted", key, owed, h.sent, sorted(h.active))
    # nothing pmcp still tracks lost its context, and nothing the model says
    # is tracked was dropped
    for server in ("alpha", "beta"):
        if server not in m._clients:
            continue
        tracked = {t.task_id: t for t in m.get_tracked_tasks(server)}
        for key in h.active:
            if key[0] == server:
                assert key[1] in tracked, (key, sorted(tracked))
                got = tracked[key[1]].requestor_context
                assert got == h.context.get(key), (key, got)


def _overlap_orderings(events: tuple[str, ...]) -> list[tuple[str, ...]]:
    import itertools
    import math
    from collections import Counter

    orders = sorted(set(itertools.permutations(events)))
    expected = math.factorial(len(events))
    for n in Counter(events).values():
        expected //= math.factorial(n)
    assert len(orders) == expected
    return orders


#: T: a forced refresh of both servers; D: a forced disconnect of beta;
#: d: a non-forced disconnect of beta; A: the newest running teardown is
#: aborted (its caller cancelled); X: a forced disconnect of alpha (it
#: cancels a refresh's in-flight request on alpha); F: a listing floods beta
#: with two tasks
#: (the cap evicts what a teardown owes); G and R: a `tasks/get` or
#: `tasks/result` poll on beta::b1 starts; L: a listing re-tracks beta::b1;
#: S: the oldest waiting request is answered. Each alphabet is enumerated
#: whole.
_OVERLAP_ALPHABETS = {
    "refresh+forced+plain": ("T", "D", "d", "F", "F", "S"),
    "two-refreshes+forced": ("T", "T", "D", "F", "S", "S"),
    "two-forced+plain": ("D", "D", "d", "F", "F", "S"),
    "abort+get": ("T", "D", "A", "F", "G", "S"),
    "abort+result": ("T", "T", "A", "F", "R", "S"),
    "abort+relist": ("D", "d", "A", "F", "L", "S"),
    "abort+get+cancel-later": ("T", "A", "F", "G", "S", "S"),
    # rev 13 (round-12 F001): a forced disconnect of the other server aborts
    # a refresh whose beta tasks the cap had moved into its custody
    "refresh+alpha-disconnect": ("T", "X", "F", "F", "d", "S"),
    "refresh+alpha-disconnect+get": ("T", "X", "F", "G", "D", "S"),
}


@pytest.mark.parametrize("alphabet", list(_OVERLAP_ALPHABETS))
@pytest.mark.asyncio
async def test_overlapping_teardowns_and_disconnects_never_drop_an_owed_task(
    alphabet: str,
) -> None:
    """Generated (rev 12, round-11 F001): every ordering of overlapping forced
    refreshes, forced and non-forced disconnects of beta, aborted teardowns,
    floods that evict owed tasks into custodies, get and result polls, a
    re-tracking listing, forced disconnects of the other server (rev 13) and
    answered requests (9 alphabets, 3,960 orderings). For
    each: the live bounds and the obligation bound after every operation;
    every `tasks/get`, `tasks/result` and `tasks/cancel` carries the newest
    context the model holds; a non-forced disconnect never succeeds while the
    model says a beta task runs; every task a teardown that ran to its end
    owed at its request gets a `tasks/cancel` from it or from another
    teardown that owed it too; at the end every task the model tracks is
    tracked, with its context (a custody released while a poll pinned its
    record dropped nothing); no pin or custody is left."""
    orders = _overlap_orderings(_OVERLAP_ALPHABETS[alphabet])
    for order in orders:
        try:
            await _run_overlap(order)
        except AssertionError as e:
            raise AssertionError(f"{alphabet} {''.join(order)}: {e}") from e


def test_custody_context_survives_a_concurrent_poll() -> None:
    """The round-11 codex seat's falsifier, as written: `b` is evicted into a
    forced refresh's custody; a `tasks/get` or `tasks/result` poll pins it;
    the refresh is cancelled, releasing the custody; the poll's reply and a
    later cancel must still carry `b`'s context. On rev 11 both sent and kept
    None. Rev 12: the poll's pin puts `b` back in the registry."""

    async def scenario(operation):
        manager = ClientManager()
        manager._tasks.per_server = 2
        connection = SimpleNamespace(
            connection_id=1,
            is_remote=True,
            write_stream=object(),
            status=SimpleNamespace(server_capabilities={"tasks": {}}),
        )
        manager._clients["tasks"] = connection
        context = {"tenant": "b"}

        def record(task_id, supplied=None):
            manager._record_task(
                "tasks",
                McpTaskInfo(task_id=task_id, status="working"),
                requestor_context=supplied,
                connection=connection,
            )

        record("b", context)
        record("a")
        teardown_started = asyncio.Event()
        poll_started = asyncio.Event()
        release = asyncio.Event()
        sent = []

        async def send(managed, method, params, **kwargs):
            assert managed is connection
            sent.append((method, params))
            if method == "tasks/cancel" and params["taskId"] == "a":
                teardown_started.set()
                await asyncio.Event().wait()
            if method == operation:
                poll_started.set()
                await release.wait()
            task = {"taskId": params["taskId"], "status": "working"}
            return {"task": task, "result": {}} if method == "tasks/result" else task

        manager._send_request = send
        teardown = asyncio.create_task(manager.cancel_active_tasks())
        poll = None
        try:
            await asyncio.wait_for(teardown_started.wait(), 2)
            record("x")
            assert manager.get_task_record("tasks", "b") is None
            assert manager._tasks.held(("tasks", "b")).requestor_context == context
            call = (
                manager.get_task
                if operation == "tasks/get"
                else manager.get_task_result
            )
            poll = asyncio.create_task(call("tasks", "b"))
            await asyncio.wait_for(poll_started.wait(), 2)
            assert manager._tasks.pinned[("tasks", "b")] == 1
            teardown.cancel()
            await asyncio.gather(teardown, return_exceptions=True)
            assert manager._tasks.watching == 0
            release.set()
            await asyncio.wait_for(poll, 2)
            reply_context = manager.get_task_record("tasks", "b").requestor_context
            await manager.cancel_task("tasks", "b", force=True)
            contexts = [
                params.get("task", {}).get("requestorContext")
                for _, params in sent
                if params["taskId"] == "b"
            ]
            return contexts, reply_context
        finally:
            running = [teardown] + ([poll] if poll is not None else [])
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            assert manager._tasks.pinned == {}
            assert manager._tasks.watching == 0

    observed = {
        operation: asyncio.run(scenario(operation))
        for operation in ("tasks/get", "tasks/result")
    }
    context = {"tenant": "b"}
    expected = ([context, context], context)
    assert all(result == expected for result in observed.values()), observed


# --- rev 13 (round-12 F001): releasing a custody never drops an uncancelled task


@pytest.mark.asyncio
async def test_a_disconnect_of_another_server_never_drops_an_owed_task() -> None:
    """The round-12 claude seat's falsifier, as written: a forced disconnect
    of alpha aborts a refresh waiting on alpha's cancel, after the cap moved
    the refresh's beta tasks into its custody. On rev 12 the release dropped
    them: `(('beta', 'b2'), [('alpha', 'a1'), ('alpha', 'a1')], [('beta',
    'f0'), ('beta', 'f1')])`. Rev 13: the release returns them to the
    registry, tracked and retriable."""
    h = _Interleaving()  # alpha::a1, beta::b1, beta::b2; caps 2 per server, 3 total
    m = h.manager
    h.teardown("cancel-active-tasks")  # a forced refresh owes a1, b1, b2
    await h.settle()
    owed_beta = {("beta", "b1"), ("beta", "b2")}
    h.record("beta", "f0", None)  # while a1's cancel waits, two new beta tasks
    h.record("beta", "f1", None)  # evict b1 and b2 into the refresh's custody
    await h.settle()
    # the operator force-disconnects alpha (its cancel is what the refresh waits on);
    # in h.running so the harness's live bound counts it as a request in flight
    d = asyncio.ensure_future(m.disconnect_server("alpha", force=True))
    h.running.append(d)
    for _ in range(20):
        await h.settle()
        h.answer("tasks/cancel")
    assert (await d)[0] is True
    await asyncio.gather(*h.running, return_exceptions=True)
    tracked = {("beta", t.task_id) for t in m.get_active_tasks("beta")}
    for key in owed_beta:
        assert key in h.sent or key in tracked, (key, h.sent, sorted(tracked))


@pytest.mark.asyncio
async def test_released_uncancelled_tasks_are_retried_and_refuse_a_plain_disconnect() -> (
    None
):
    """After the round-12 abort, the beta tasks the refresh owed are tracked
    again with their contexts: a non-forced disconnect of beta refuses, and a
    retried forced refresh cancels them."""
    h = _Interleaving()
    m = h.manager
    h.teardown("cancel-active-tasks")
    await h.settle()
    h.record("beta", "f0", None)
    h.record("beta", "f1", None)
    await h.settle()
    d = asyncio.ensure_future(m.disconnect_server("alpha", force=True))
    h.running.append(d)
    for _ in range(20):
        await h.settle()
        h.answer("tasks/cancel")
    await asyncio.gather(*h.running, return_exceptions=True)
    beta = {t.task_id: t.requestor_context for t in m.get_active_tasks("beta")}
    assert beta == {"b1": B1_CTX, "b2": B2_CTX}  # the newest two, back
    ok, _n, message = await m.disconnect_server("beta")
    assert ok is False and message is not None
    retry = asyncio.ensure_future(m.cancel_active_tasks())
    h.running.append(retry)
    for _ in range(20):
        await h.settle()
        h.answer("tasks/cancel")
    assert await retry == (2, [])
    assert {k for k in h.sent if k[0] == "beta"} >= {("beta", "b1"), ("beta", "b2")}


@pytest.mark.parametrize("ending", ["cancelled", "exception", "reply"])
@pytest.mark.asyncio
async def test_a_custody_release_returns_only_uncancelled_current_unfinished_tasks(
    ending: str,
) -> None:
    """The release rule, directly: what goes back is exactly the owed records
    tracked nowhere else, unfinished, on the server's current connection --
    never one whose cancel was answered, one a reply finished, or one from a
    replaced connection."""
    manager, _ = _manager("alpha", "beta")
    manager._tasks.per_server = 1
    for server, task_id in (("alpha", "a"), ("beta", "b")):
        manager._record_task(
            server,
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context={"for": task_id},
            connection=manager._clients[server],
        )
    gate = asyncio.get_running_loop().create_future()

    async def send(managed: Any, method: str, params: Any, **_: Any) -> Any:
        if managed.status.name == "alpha":
            await gate
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    refresh = asyncio.ensure_future(manager.cancel_active_tasks())
    for _ in range(5):
        await asyncio.sleep(0)
    manager._record_task(  # evicts beta::b into the custody
        "beta",
        McpTaskInfo(task_id="x", status="working"),
        requestor_context=None,
        connection=manager._clients["beta"],
    )
    assert manager.get_task_record("beta", "b") is None
    if ending == "cancelled":
        refresh.cancel()
    elif ending == "exception":
        gate.set_exception(RuntimeError("downstream failed"))
    else:
        gate.set_result(None)
    results = await asyncio.gather(refresh, return_exceptions=True)
    assert manager._tasks.watching == 0
    active = {t.task_id: t for t in manager.get_active_tasks("beta")}
    if ending == "reply":
        assert results == [(2, [])]
        assert "b" not in active  # cancelled and answered: finished
    else:
        assert active["b"].requestor_context == {"for": "b"}  # back, tracked


@pytest.mark.parametrize("replaced", [False, True])
def test_a_release_returns_a_leftover_only_on_its_current_connection(
    replaced: bool,
) -> None:
    """A leftover from a connection that has since been replaced is not the
    replacement's state (rev 5): the release leaves it out. On the same
    connection it comes back, with its context."""
    manager, custody = _custody_holding_t(OLD)
    record = custody.held(("tasks", "t"))
    manager._tasks.unwatch(custody)
    if replaced:
        manager._clients["tasks"] = _task_server(manager, {})
    with manager._custody([record]):
        pass
    back = manager._tasks.get(("tasks", "t"))
    if replaced:
        assert back is None
    else:
        assert back is not None and back.requestor_context == OLD


def test_a_release_never_returns_a_finished_task() -> None:
    """An owed task that finished, and was then evicted (finished records go
    first), is done downstream: the release does not bring it back."""
    manager, custody = _custody_holding_t(OLD)
    a = manager._clients["tasks"]
    manager._record_task(  # t comes back finished (a reply), then is evicted
        "tasks",
        McpTaskInfo(task_id="t", status="completed"),
        requestor_context=None,
        connection=a,
    )
    manager._record_task(
        "tasks",
        McpTaskInfo(task_id="y", status="working"),
        requestor_context=None,
        connection=a,
    )
    assert custody.held(("tasks", "t")).status == "completed"
    manager._tasks.unwatch(custody)
    manager._tasks.per_server = 2  # room for it, so a wrong return would show
    with manager._custody([custody.held(("tasks", "t"))]):
        pass
    assert ("tasks", "t") not in manager._tasks


def test_released_leftovers_past_the_cap_keep_the_newest() -> None:
    """Back in the registry the leftovers are ordinary records under the caps
    (rev 13). Returned oldest first, so when they alone exceed the cap the
    most recently recorded ones survive -- the cap's usual order."""
    manager, _ = _manager("tasks")
    registry = manager._tasks
    registry.per_server = 2
    a = manager._clients["tasks"]

    def record(task_id: str) -> None:
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context={"for": task_id},
            connection=a,
        )

    record("a")
    record("b")
    owed = list(registry.values())
    custody = registry.watch(owed)
    record("x")
    record("y")  # a and b evicted into the custody
    held = [custody.held(("tasks", k)) for k in ("a", "b")]
    registry.unwatch(custody)
    registry.per_server = 1
    with manager._custody(held):
        pass
    assert [k[1] for k in registry] == ["b"]


@pytest.mark.asyncio
async def test_the_refresh_gate_and_its_counts_see_a_task_held_in_a_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-12 N4: behavioural, not only structural. A task the cap moved
    into a running teardown's custody is an active task for
    `gateway.refresh`: a refresh without `force` counts and refuses on it,
    and a forced one reports it as remaining when nothing cancelled it."""
    manager, custody = _custody_holding_t(OLD)  # t in custody, x in registry
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await tools.refresh({})
    assert out.ok is False and out.mcp_tasks_seen == 2, out
    assert out.mcp_tasks_refused == 2 and out.mcp_tasks_remaining == 2

    async def nothing(server_name: str | None = None) -> tuple[int, list[str]]:
        return 0, []

    async def kept(name: str, force: bool = False) -> tuple[bool, int, None]:
        return True, 0, None

    # isolate the count: nothing cancels or disconnects during this refresh
    monkeypatch.setattr(manager, "cancel_active_tasks", nothing)
    monkeypatch.setattr(manager, "disconnect_server", kept)
    forced = await tools.refresh({"force": True})
    assert forced.mcp_tasks_seen == 2 and forced.mcp_tasks_remaining == 2, forced
    manager._tasks.unwatch(custody)


@pytest.mark.asyncio
async def test_a_chain_of_forced_refreshes_owes_its_history_and_cancels_it_all() -> (
    None
):
    """The obligation bound, on a chain that does not disconnect (the round-12
    claude seat's probe, scaled down): each round records one new task,
    starts a forced refresh, then cancels the previous refresh, whose cancel
    the downstream never answers. Each refresh inherits what the running one
    owes, so the newest custody owes every distinct task none has cancelled:
    the chain's history, while the registry stays within its live bound.
    When the downstream answers, the last refresh cancels them all, and
    nothing is left active or owed."""
    manager, _ = _manager("tasks")
    registry = manager._tasks
    registry.per_server = registry.total = 10
    a = manager._clients["tasks"]
    gate = asyncio.Event()
    peak = 0

    def record(task_id: str) -> None:
        manager._record_task(
            "tasks",
            McpTaskInfo(task_id=task_id, status="working"),
            requestor_context=None,
            connection=a,
        )

    async def send(managed: ManagedClient, method: str, params: Any, **_: Any) -> Any:
        await gate.wait()
        return {"taskId": params["taskId"], "status": "cancelled"}

    manager._send_request = send  # type: ignore[method-assign, assignment]
    for i in range(10):
        record(f"w{i}")
    previous: asyncio.Task[Any] | None = None
    for k in range(50):
        record(f"new{k}")
        current = asyncio.ensure_future(manager.cancel_active_tasks("tasks"))
        for _ in range(5):
            await asyncio.sleep(0)
        if previous is not None:
            previous.cancel()
            await asyncio.gather(previous, return_exceptions=True)
        previous = current
        peak = max(peak, len(registry))
        assert len(registry) <= max(10, sum(registry.pinned.values()) + 1)
    assert previous is not None
    # `new0` evicted `w0` before any refresh owed it (the cap's residual);
    # from then on the first refresh's 10, plus one per later round: 10 + 49
    assert [len(c) for c in registry._custodies] == [10 + 49]
    assert len(manager.get_active_tasks("tasks")) == 59
    gate.set()
    assert await previous == (59, [])
    assert manager.get_active_tasks("tasks") == [] and registry.watching == 0
    assert peak <= 11
