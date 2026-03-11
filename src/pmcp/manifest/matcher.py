"""Capability matcher - keyword-based matching of requests to manifest entries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pmcp.manifest.loader import CLIAlternative, Manifest, ServerConfig

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Result of capability matching."""

    matched: bool
    entry_name: str
    entry_type: Literal["cli", "server", ""]
    confidence: float
    reasoning: str

    # Resolved config (if matched)
    cli_config: CLIAlternative | None = None
    server_config: ServerConfig | None = None


def _keyword_match_score(query: str, keywords: list[str]) -> float:
    """Simple keyword matching fallback."""
    query_lower = query.lower()
    query_norm = query_lower.replace("-", " ").replace("_", " ")
    query_words = set(query_norm.split())

    matches = 0
    for keyword in keywords:
        keyword_lower = keyword.lower()
        keyword_norm = keyword_lower.replace("-", " ").replace("_", " ")
        keyword_words = set(keyword_norm.split())
        if (
            keyword_lower in query_lower
            or keyword_norm in query_norm
            or keyword_lower in query_words
            or keyword_norm in query_words
            or (keyword_words and keyword_words.issubset(query_words))
        ):
            matches += 1

    if not keywords:
        return 0.0

    return min(matches / len(keywords), 1.0)


async def match_capability(
    query: str,
    manifest: Manifest,
    detected_clis: set[str] | None = None,
) -> MatchResult:
    """Match a capability request to a CLI or MCP server using keyword matching.

    Args:
        query: Natural language capability request
        manifest: Loaded manifest with CLIs and servers
        detected_clis: Set of CLI names detected in the environment

    Returns:
        MatchResult with matched entry or no match
    """
    detected_clis = detected_clis or set()
    return _keyword_match(query, manifest, detected_clis)


def _keyword_match(
    query: str,
    manifest: Manifest,
    detected_clis: set[str],
) -> MatchResult:
    """Fallback keyword-based matching."""
    best_match: MatchResult | None = None
    best_score = 0.0

    # Check detected CLIs first (preferred)
    for name, cli in manifest.cli_alternatives.items():
        if name in detected_clis:
            score = _keyword_match_score(query, cli.keywords)
            if score > best_score:
                best_score = score
                best_match = MatchResult(
                    matched=True,
                    entry_name=name,
                    entry_type="cli",
                    confidence=score,
                    reasoning=f"Keyword match for installed CLI: {name}",
                    cli_config=cli,
                )

    # Check servers
    for name, server in manifest.servers.items():
        score = _keyword_match_score(query, server.keywords)
        # Slight preference for CLIs, so server needs higher score
        adjusted_score = score * 0.9
        if adjusted_score > best_score:
            best_score = adjusted_score
            best_match = MatchResult(
                matched=True,
                entry_name=name,
                entry_type="server",
                confidence=score,
                reasoning=f"Keyword match for server: {name}",
                server_config=server,
            )

    if best_match and best_score >= 0.2:  # Minimum threshold
        return best_match

    return MatchResult(
        matched=False,
        entry_name="",
        entry_type="",
        confidence=0.0,
        reasoning="No matching capability found in manifest",
    )
