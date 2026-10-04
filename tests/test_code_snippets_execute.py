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


def _fake_mcp(variant: str = "plain") -> SimpleNamespace:
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
            risky = variant == "risky"
            out = SchemaCard(
                server="s",
                tool_name="t",
                description="d",
                args=[],
                annotations=None,
                safety_notes=["High-risk tool"] if risky else None,
            )
        elif name == "gateway.provision":
            # The handler pairs a job_id only with status="started"; an
            # already-running, remote or refused server returns no job_id.
            if variant == "no_job":
                out = ProvisionOutput(
                    ok=True,
                    server=arguments["server_name"],
                    message="already running",
                    status="already_running",
                )
            else:
                out = ProvisionOutput(
                    ok=True,
                    server=arguments["server_name"],
                    message="started",
                    job_id="job-1",
                    status="started",
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


@pytest.mark.parametrize("variant", ["plain", "risky", "no_job"])
@pytest.mark.parametrize("tool_id", _TOOL_IDS)
def test_every_snippet_runs_against_the_real_response_shapes(
    tool_id: str, variant: str
) -> None:
    """`risky`: describe returns safety notes and no annotations; `no_job`:
    provision returns no job_id (already running, remote or refused)."""
    snippet = _LOADER.get_snippet_for_tool(tool_id, max_lines=_DEFAULT)
    assert snippet is not None
    exec(compile(snippet, tool_id, "exec"), {"mcp": _fake_mcp(variant), **_FREE_NAMES})


def test_the_describe_snippet_warns_on_safety_notes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    snippet = _LOADER.get_snippet_for_tool("gateway::describe", max_lines=_DEFAULT)
    assert snippet is not None
    exec(snippet, {"mcp": _fake_mcp("risky"), **_FREE_NAMES})
    assert "Warning" in capsys.readouterr().out


@pytest.mark.parametrize("variant", ["plain", "no_job"])
def test_the_provision_snippet_reports_the_outcome(
    variant: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Grok, #358 round 3: the loop ended on complete/failed/timeout alike and
    the snippet said nothing. It now prints the terminal status (or that the
    provision finished without a job)."""
    snippet = _LOADER.get_snippet_for_tool(
        "gateway::provision_status", max_lines=_DEFAULT
    )
    assert snippet is not None
    exec(snippet, {"mcp": _fake_mcp(variant), **_FREE_NAMES})
    out = capsys.readouterr().out
    assert ("complete" in out) if variant == "plain" else ("without a job" in out)
