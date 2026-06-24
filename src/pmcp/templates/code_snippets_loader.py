"""Code snippet template loader.

This module loads code snippet templates from YAML for L2 guidance.
Returns a static template for the tool, or None when none exists.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pmcp.types import ToolInfo

logger = logging.getLogger(__name__)


class CodeSnippetsLoader:
    """Loads code snippet templates for tools."""

    def __init__(self, templates_path: Path | None = None):
        """Initialize the code snippets loader.

        Args:
            templates_path: Path to code_examples.yaml file. If None, uses default.
        """
        if templates_path is None:
            templates_path = Path(__file__).parent / "code_examples.yaml"

        self._templates_path = templates_path
        self._snippets: dict[str, str] = {}
        self._generic_fallback: str | None = None

        self._load_snippets()

    def _load_snippets(self) -> None:
        """Load code snippets from YAML file."""
        if not self._templates_path.exists():
            # No templates file, use empty defaults
            return

        try:
            with open(self._templates_path) as f:
                data = yaml.safe_load(f)

            if not data:
                return

            # Load tool-specific snippets
            for tool_id, template_data in data.items():
                if tool_id == "_generic_fallback":
                    self._generic_fallback = template_data.get("snippet", "").strip()
                else:
                    snippet = template_data.get("snippet", "").strip()
                    if snippet:
                        self._snippets[tool_id] = snippet

        except Exception as e:
            # If loading fails, log warning but continue with empty snippets
            print(
                f"Warning: Failed to load code snippets from {self._templates_path}: {e}"
            )

    def get_snippet_for_tool(
        self,
        tool_id: str,
        max_lines: int = 4,
        tool_info: ToolInfo | None = None,
    ) -> str | None:
        """Get code snippet for a tool.

        Args:
            tool_id: Full tool ID (e.g., "playwright::browser_navigate")
            max_lines: Maximum number of lines to return
            tool_info: Optional ToolInfo (reserved; unused by static templates)

        Returns:
            Code snippet string or None if no template exists
        """
        # Check for exact match in static templates
        snippet = self._snippets.get(tool_id)

        if snippet:
            # Trim static template to max lines
            lines = snippet.split("\n")
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                snippet = "\n".join(lines)
            return snippet

        # No static template available
        return None


# Global instance (lazy-loaded)
_code_snippets_loader: CodeSnippetsLoader | None = None


def get_code_snippets_loader() -> CodeSnippetsLoader:
    """Get the global code snippets loader instance."""
    global _code_snippets_loader
    if _code_snippets_loader is None:
        _code_snippets_loader = CodeSnippetsLoader()
    return _code_snippets_loader


def get_code_snippet(
    tool_id: str,
    max_lines: int = 4,
    tool_info: ToolInfo | None = None,
) -> str | None:
    """Get code snippet for a tool (convenience function).

    Args:
        tool_id: Full tool ID (e.g., "playwright::browser_navigate")
        max_lines: Maximum number of lines to return
        tool_info: Optional ToolInfo (reserved; unused by static templates)

    Returns:
        Code snippet string or None if no template exists
    """
    loader = get_code_snippets_loader()
    return loader.get_snippet_for_tool(tool_id, max_lines, tool_info)
