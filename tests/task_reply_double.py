"""Test-double support for `ClientManager.call_tool_with_task` and
`get_task_result_with_task` (Consiliency/pmcp#338, rev 6).

`gateway.invoke` and `gateway.tasks_result` take the task record the manager
built from *this* reply instead of looking it up by `(server, task_id)` after
the await. A client-manager double that already defines `call_tool`,
`get_task_result` and `get_task_record` mixes this in to answer the new
calls. Its "record built from this reply" is its own record of the task the
reply names, which is what a double's registry holds.
"""

from __future__ import annotations

from typing import Any

from pmcp.client.manager import TaskReply


class TaskReplyDouble:
    async def call_tool_with_task(
        self, tool_id: str, args: dict[str, Any], timeout_ms: int = 30000, **kwargs: Any
    ) -> TaskReply:
        result = await self.call_tool(tool_id, args, timeout_ms, **kwargs)  # type: ignore[attr-defined]
        task = None
        payload = result.get("task") if isinstance(result, dict) else None
        if isinstance(payload, dict):
            task_id = payload.get("taskId") or payload.get("task_id")
            lookup = getattr(self, "get_task_record", None)
            if isinstance(task_id, str) and lookup is not None:
                task = lookup(tool_id.split("::", 1)[0], task_id)
        return TaskReply(result, task)

    async def get_task_result_with_task(
        self, server_name: str, task_id: str, **kwargs: Any
    ) -> TaskReply:
        result = await self.get_task_result(server_name, task_id, **kwargs)  # type: ignore[attr-defined]
        return TaskReply(result, self.get_task_record(server_name, task_id))  # type: ignore[attr-defined]
