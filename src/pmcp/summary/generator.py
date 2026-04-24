"""Main entry point for capability summary generation.

Tries pre-built cache first, then LLM summarization, finally templates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pmcp.summary.template_fallback import template_summary

if TYPE_CHECKING:
    from pmcp.types import DescriptionsCache, ToolInfo

logger = logging.getLogger(__name__)


def get_prebuilt_summary(
    tools: list[ToolInfo],
    cache: DescriptionsCache | None = None,
    include_code_guidance: bool = True,
    custom_instructions: str | None = None,
    provisionable_categories: str | None = None,
) -> str | None:
    """Try to build summary from pre-built cache.

    Args:
        tools: List of tools (to get server names)
        cache: Pre-built descriptions cache
        include_code_guidance: Whether to include L0 workflow guidance
        custom_instructions: Custom L0 text replacing default workflow guidance

    Returns:
        Summary string if all servers found in cache, None otherwise
    """
    if not cache:
        return None

    # Get unique servers from tools
    server_names = set(t.server_name for t in tools)

    # Check if all servers have cached summaries
    missing = [s for s in server_names if s not in cache.servers]
    if missing:
        logger.debug(f"Missing cached summaries for: {missing}")
        return None

    # Build summary from cached capability_summary fields
    lines = ["MCP Gateway capabilities:"]
    for name in sorted(server_names):
        desc = cache.servers[name]
        # Add each line from capability_summary
        for line in desc.capability_summary.strip().split("\n"):
            if line.strip():
                lines.append(line.strip())

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
                'Use gateway.request_capability("<what you need>") first; PMCP may return direct CLI guidance for a local tool or an MCP server candidate to provision.'
            )
            if provisionable_categories:
                lines.append(provisionable_categories)

    lines.append("")
    lines.append(
        "Use gateway.catalog_search to explore MCP tools and matching CLI hints."
    )
    return "\n".join(lines)


async def generate_capability_summary(
    tools: list[ToolInfo],
    use_llm: bool = True,
    cache: DescriptionsCache | None = None,
    include_code_guidance: bool = True,
    custom_instructions: str | None = None,
    provisionable_categories: str | None = None,
) -> str:
    """Generate a capability summary for MCP tools.

    Priority:
    1. Pre-built cache (if available for all servers)
    2. LLM-based summarization (using BAML)
    3. Template-based fallback

    Args:
        tools: List of tools to summarize
        use_llm: Whether to attempt LLM summarization (default True)
        cache: Pre-built descriptions cache
        include_code_guidance: Whether to include L0 workflow guidance
        custom_instructions: Custom L0 text replacing default workflow guidance
        provisionable_categories: Compact category string appended after default L0 trigger patterns

    Returns:
        Human-readable capability summary for MCP instructions
    """
    if not tools:
        return (
            "MCP Gateway: No tools currently available.\n"
            "Use gateway.refresh to reload server configurations."
        )

    # 1. Try pre-built cache first
    if cache:
        prebuilt = get_prebuilt_summary(
            tools,
            cache,
            include_code_guidance=include_code_guidance,
            custom_instructions=custom_instructions,
            provisionable_categories=provisionable_categories,
        )
        if prebuilt:
            logger.info("Using pre-built capability summary from cache")
            return prebuilt

    # 2. Try LLM summarization
    if use_llm:
        try:
            from pmcp.summary.llm_summarizer import summarize_capabilities

            logger.info("Attempting LLM-based capability summary...")
            summary = await summarize_capabilities(tools)
            logger.info("LLM summary generated successfully")
            return summary

        except ImportError:
            logger.info("baml-py not available, using template fallback")
        except TimeoutError:
            logger.warning("LLM summarization timed out, using template fallback")
        except Exception as e:
            logger.warning("LLM summarization failed: %s, using template fallback", e)

    # 3. Fall back to template-based summary
    logger.info("Generating template-based capability summary")
    return template_summary(
        tools,
        include_code_guidance=include_code_guidance,
        custom_instructions=custom_instructions,
        provisionable_categories=provisionable_categories,
    )
