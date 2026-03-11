"""LLM-powered capability summary generation — retired.

Outbound LLM calls have been removed. generate_capability_summary() in
generator.py falls through to the template path when this raises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pmcp.types import ToolInfo


async def summarize_capabilities(tools: list[ToolInfo]) -> str:
    """Retired: raises so callers fall through to the template path."""
    raise NotImplementedError("LLM summarization removed; using template fallback")
