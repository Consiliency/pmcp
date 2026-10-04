"""Shipped L2 code snippets are runnable Python at every line budget.

`gateway.describe` returns a static snippet cut to the guidance config's
`max_snippet_lines` (default 4). A cut used to leave a dangling block --
four shipped `try:`/`except` examples lost their `except` body at the
default -- so the loader now returns the longest prefix that parses, or no
snippet at all. This module pins that for every template and every budget,
and that each template still yields a snippet at the default budget.
"""

from __future__ import annotations

import ast

import pytest

from pmcp.config.guidance import GuidanceConfig
from pmcp.templates.code_snippets_loader import CodeSnippetsLoader

_LOADER = CodeSnippetsLoader()
_TOOL_IDS = sorted(k for k in _LOADER._snippets if not k.startswith("_"))
_DEFAULT = GuidanceConfig().max_snippet_lines


def test_there_are_templates_to_check() -> None:
    assert len(_TOOL_IDS) >= 10


@pytest.mark.parametrize("tool_id", _TOOL_IDS)
@pytest.mark.parametrize("max_lines", range(1, 13))
def test_every_cut_is_valid_python(tool_id: str, max_lines: int) -> None:
    snippet = _LOADER.get_snippet_for_tool(tool_id, max_lines=max_lines)
    if snippet is not None:
        assert len(snippet.split("\n")) <= max_lines
        ast.parse(snippet)


@pytest.mark.parametrize("tool_id", _TOOL_IDS)
def test_every_template_yields_a_snippet_at_the_default_budget(tool_id: str) -> None:
    snippet = _LOADER.get_snippet_for_tool(tool_id, max_lines=_DEFAULT)
    assert snippet is not None, f"{tool_id} has no valid {_DEFAULT}-line prefix"
    # More than a comment: the snippet shows an actual call.
    assert "mcp.call_tool" in snippet, snippet


def test_a_dangling_block_is_trimmed_not_shipped() -> None:
    loader = CodeSnippetsLoader()
    loader._snippets = {"x::y": "# c\ntry:\n    a = 1\nexcept Exception:\n    pass\n"}
    assert loader.get_snippet_for_tool("x::y", max_lines=4) is None
    assert loader.get_snippet_for_tool("x::y", max_lines=5) is not None
