"""Capability matcher - keyword-based matching of requests to manifest entries."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pmcp.manifest.environment import CLIInfo
from pmcp.manifest.loader import CLIAlternative, Manifest, ServerConfig
from pmcp.types import CLIHint

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


@dataclass
class CLIHintMatch:
    """Ranked CLI hint match for internal discovery plumbing."""

    hint: CLIHint
    score: float
    suppressed_by_prefer_mcp: bool = False
    matched_prefer_mcp_phrase: str | None = None


def _normalize_text(value: str) -> str:
    return value.lower().replace("-", " ").replace("_", " ")


def _text_match_score(query_norm: str, query_words: set[str], value: str) -> float:
    value_norm = _normalize_text(value)
    value_words = set(value_norm.split())
    if not value_words:
        return 0.0
    if value_norm in query_norm or value_words.issubset(query_words):
        return 1.0
    overlap = len(value_words & query_words)
    if overlap == 0:
        return 0.0
    return min(overlap / len(value_words), 0.8)


def _keyword_matches_query(
    query_lower: str, query_norm: str, query_words: set[str], keyword: str
) -> bool:
    del query_lower, query_norm
    keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
    keyword_words = set(keyword_norm.split())
    return bool(keyword_words) and keyword_words.issubset(query_words)


def _keyword_match_score(
    query: str, keywords: list[str], keyword_weights: Mapping[str, float] | None = None
) -> float:
    """Score by absolute matched keyword evidence."""
    query_lower = query.lower()
    query_norm = query_lower.replace("-", " ").replace("_", " ")
    query_words = set(query_norm.split())

    matched_weight = 0.0
    for keyword in keywords:
        if _keyword_matches_query(query_lower, query_norm, query_words, keyword):
            keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
            matched_weight += (
                keyword_weights.get(keyword_norm, 1.0) if keyword_weights else 1.0
            )

    if not keywords:
        return 0.0

    return min(matched_weight / 3.0, 1.0)


def _manifest_keyword_weights(manifest: Manifest) -> dict[str, float]:
    frequencies: dict[str, int] = {}
    for server in manifest.servers.values():
        for keyword in set(server.keywords):
            keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
            frequencies[keyword_norm] = frequencies.get(keyword_norm, 0) + 1

    return {keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()}


def rank_cli_hints(
    query: str,
    manifest: Manifest,
    *,
    available_clis: set[str] | list[str] | tuple[str, ...] | None = None,
    detected_cli_infos: Mapping[str, CLIInfo] | None = None,
    include_unavailable: bool = False,
    include_suppressed: bool = False,
    min_score: float = 0.2,
) -> list[CLIHintMatch]:
    """Rank CLI alternatives using only local manifest and environment data."""
    available = set(available_clis or ())
    detected_infos = dict(detected_cli_infos or {})
    available.update(detected_infos)

    query_norm = _normalize_text(query)
    query_words = set(query_norm.split())
    matches: list[CLIHintMatch] = []

    for name, cli in manifest.cli_alternatives.items():
        is_available = name in available
        if not include_unavailable and not is_available:
            continue

        score = 0.0
        score = max(score, _text_match_score(query_norm, query_words, cli.name))
        score = max(
            score,
            _text_match_score(query_norm, query_words, cli.description) * 0.7,
        )
        score = max(score, _keyword_match_score(query, cli.keywords))
        for example in cli.examples:
            score = max(
                score, _text_match_score(query_norm, query_words, example) * 0.8
            )

        matched_prefer_mcp_phrase = None
        for phrase in cli.prefer_mcp_for:
            if _text_match_score(query_norm, query_words, phrase) >= 1.0:
                matched_prefer_mcp_phrase = phrase
                score = max(score, 1.0)
                break

        if score < min_score:
            continue

        suppressed = matched_prefer_mcp_phrase is not None
        if suppressed and not include_suppressed:
            continue

        path = detected_infos[name].path if name in detected_infos else None
        reason = "Available on PATH" if is_available else "CLI is not detected"
        if suppressed:
            reason = (
                "MCP server preferred for "
                f"'{matched_prefer_mcp_phrase}' despite matching CLI '{name}'."
            )

        hint = CLIHint(
            name=cli.name,
            description=cli.description,
            available=is_available,
            path=path,
            check_command=cli.check_command,
            help_command=cli.help_command,
            examples=cli.examples,
            prefer_mcp_for=cli.prefer_mcp_for,
            reason=reason,
        )
        matches.append(
            CLIHintMatch(
                hint=hint,
                score=score,
                suppressed_by_prefer_mcp=suppressed,
                matched_prefer_mcp_phrase=matched_prefer_mcp_phrase,
            )
        )

    return sorted(matches, key=lambda match: (-match.score, match.hint.name))


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
    for match in rank_cli_hints(query, manifest, available_clis=detected_clis):
        cli = manifest.cli_alternatives[match.hint.name]
        if match.score > best_score:
            best_score = match.score
            best_match = MatchResult(
                matched=True,
                entry_name=match.hint.name,
                entry_type="cli",
                confidence=match.score,
                reasoning=f"Keyword match for installed CLI: {match.hint.name}",
                cli_config=cli,
            )

    # Check servers
    keyword_weights = _manifest_keyword_weights(manifest)
    for name, server in manifest.servers.items():
        score = _keyword_match_score(query, server.keywords, keyword_weights)
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
