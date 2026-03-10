"""Template-based capability summary generation.

Used as fallback when LLM summarization is unavailable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pmcp.types import ToolInfo


# Common capability keywords to extract from tool names/descriptions
CAPABILITY_KEYWORDS = {
    "navigation": [
        "navigate",
        "goto",
        "visit",
        "url",
        "page",
        "back",
        "forward",
        "reload",
    ],
    "interaction": [
        "click",
        "type",
        "input",
        "fill",
        "select",
        "submit",
        "press",
        "scroll",
    ],
    "screenshots": ["screenshot", "capture", "snapshot", "image", "visual"],
    "debugging": ["console", "network", "debug", "inspect", "devtools", "log"],
    "content": ["read", "get", "fetch", "extract", "text", "content", "html"],
    "file": ["file", "read", "write", "create", "delete", "directory", "path"],
    "search": ["search", "find", "query", "lookup", "grep"],
    "documentation": ["docs", "documentation", "api", "reference", "library"],
}


def extract_capabilities(tools: list[ToolInfo]) -> list[str]:
    """Extract capability keywords from tool names and descriptions."""
    text = " ".join(f"{t.tool_name} {t.short_description}".lower() for t in tools)

    found_capabilities = []
    for capability, keywords in CAPABILITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            found_capabilities.append(capability)

    # If no known capabilities found, extract unique verbs from tool names
    if not found_capabilities:
        verbs = set()
        for tool in tools:
            # Extract first word (usually a verb) from tool name
            parts = re.split(r"[_\-\s]", tool.tool_name.lower())
            if parts:
                verbs.add(parts[0])
        found_capabilities = list(verbs)[:5]

    return found_capabilities[:5]  # Limit to 5 capabilities


def group_by_server(tools: list[ToolInfo]) -> dict[str, list[ToolInfo]]:
    """Group tools by their server name."""
    by_server: dict[str, list[ToolInfo]] = defaultdict(list)
    for tool in tools:
        by_server[tool.server_name].append(tool)
    return dict(by_server)


def template_summary(
    tools: list[ToolInfo],
    include_code_guidance: bool = True,
    custom_instructions: str | None = None,
    provisionable_categories: str | None = None,
) -> str:
    """Generate a simple template-based capability summary.

    Output format:
    MCP Gateway: Progressive tool discovery

    <workflow guidance or custom instructions>

    Available capabilities:
    • server_name (N tools): capability1, capability2, capability3
    • other_server (M tools): capability1, capability2

    Use gateway.catalog_search to explore available tools.

    Args:
        tools: List of tool info from connected servers
        include_code_guidance: If True, include L0 workflow guidance (default: True)
        custom_instructions: If set, replaces default workflow guidance lines
        provisionable_categories: If set, appended after default L0 trigger patterns
    """
    if not tools:
        return (
            "MCP Gateway: No tools currently available.\n"
            "Use gateway.refresh to reload server configurations."
        )

    by_server = group_by_server(tools)

    lines = ["MCP Gateway: Progressive tool discovery"]

    # L0: Workflow guidance (custom or default)
    if include_code_guidance:
        lines.append("")
        if custom_instructions:
            for line in custom_instructions.strip().splitlines():
                lines.append(line.rstrip())
        else:
            lines.append("Workflow: catalog_search → describe → invoke.")
            lines.append("")
            lines.append("When to use this gateway:")
            lines.append("• Web scraping, search, or data extraction")
            lines.append("• Browser automation or testing")
            lines.append("• External APIs (GitHub, Slack, Linear, Notion, etc.)")
            lines.append("• Database queries (Postgres, SQLite, Qdrant)")
            lines.append("• Library documentation lookup")
            lines.append("• Any capability you don't have a local tool for")
            lines.append("")
            lines.append(
                'Use gateway.request_capability("<what you need>") to find or auto-provision the right server.'
            )
            if provisionable_categories:
                lines.append(provisionable_categories)

    lines.append("")
    lines.append("Available capabilities:")

    for server_name, server_tools in sorted(by_server.items()):
        capabilities = extract_capabilities(server_tools)
        cap_str = ", ".join(capabilities) if capabilities else "various tools"
        lines.append(f"• {server_name} ({len(server_tools)} tools): {cap_str}")

    lines.append("")
    lines.append("Use gateway.catalog_search to explore available tools.")

    return "\n".join(lines)
