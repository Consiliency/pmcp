"""Shipped L2 code snippets run against the gateway's real response shapes.

`tests/test_code_snippets_valid.py` pins that every snippet parses. Parsing
is not enough: snippets used to iterate the `gateway.invoke` envelope as if it
were the downstream rows, read a `risk_hint` that `gateway.describe` does not
return, and poll `provision_status` by `server_name`/`state` instead of
`job_id`/`status`. Here every snippet is executed with a fake `mcp` whose
responses are built from the gateway's own output models (so a renamed or
removed field fails this test), and whose `gateway.invoke` returns a
downstream MCP `tools/call` result inside the real envelope.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from pmcp.config.guidance import GuidanceConfig
from pmcp.templates.code_snippets_loader import CodeSnippetsLoader
from pmcp.types import (
    CapabilityCard,
    CatalogSearchOutput,
    InvokeOutput,
    ProvisionJobStatus,
    ProvisionOutput,
    SchemaCard,
)

_LOADER = CodeSnippetsLoader()
_TOOL_IDS = sorted(k for k in _LOADER._snippets if not k.startswith("_"))
_DEFAULT = GuidanceConfig().max_snippet_lines

# One downstream row carrying every key a filtering snippet reads.
_ROW = {
    "id": 1,
    "status": "active",
    "state": "open",
    "labels": ["bug"],
    "created": "2026-10-01",
}


def _downstream_text(tool_id: str) -> str:
    if tool_id == "filesystem::list_files":
        return "main.py\nREADME.md\n"
    return json.dumps([_ROW])


def _fake_mcp() -> SimpleNamespace:
    def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "gateway.invoke":
            result = {
                "content": [
                    {"type": "text", "text": _downstream_text(arguments["tool_id"])}
                ],
                "isError": False,
            }
            out: Any = InvokeOutput(
                tool_id=arguments["tool_id"],
                ok=True,
                result=result,
                truncated=False,
                raw_size_estimate=len(json.dumps(result)),
            )
        elif name == "gateway.catalog_search":
            card = CapabilityCard(
                tool_id="s::t",
                server="s",
                tool_name="t",
                short_description="d",
                tags=[],
                availability="online",
                risk_hint="low",
            )
            out = CatalogSearchOutput(
                results=[card], total_available=1, truncated=False
            )
        elif name == "gateway.describe":
            out = SchemaCard(server="s", tool_name="t", description="d", args=[])
        elif name == "gateway.provision":
            out = ProvisionOutput(
                ok=True,
                server=arguments["server_name"],
                message="started",
                job_id="job-1",
            )
        elif name == "gateway.provision_status":
            out = ProvisionJobStatus(
                job_id=arguments["job_id"],
                server="github",
                status="complete",
                progress=100,
                message="done",
            )
        else:  # pragma: no cover - a snippet calling an unknown gateway tool
            raise AssertionError(f"snippet calls unknown gateway tool {name!r}")
        return out.model_dump(mode="json")

    return SimpleNamespace(call_tool=call_tool)


# Names snippets use without defining them: the reader supplies these.
_FREE_NAMES: dict[str, Any] = {
    "args": {},
    "file_data": {"a.txt": "x"},
    "urls": ["https://example.com"],
    "page_urls": ["https://example.com"],
    "cutoff_date": "2026-01-01",
}


@pytest.mark.parametrize("tool_id", _TOOL_IDS)
def test_every_snippet_runs_against_the_real_response_shapes(tool_id: str) -> None:
    snippet = _LOADER.get_snippet_for_tool(tool_id, max_lines=_DEFAULT)
    assert snippet is not None
    exec(compile(snippet, tool_id, "exec"), {"mcp": _fake_mcp(), **_FREE_NAMES})
