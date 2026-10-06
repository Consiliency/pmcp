"""Capability matcher - keyword-based matching of requests to manifest entries."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pmcp.manifest.environment import CLIInfo
from pmcp.manifest.loader import (
    CLIAlternative,
    Manifest,
    ServerConfig,
    _cli_label,
    _server_label,
    cli_hint_fields,
    keyword_weights,
)
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
    """Keyword weights for discovery scoring.

    A manifest from ``load_manifest`` carries the weights of its base alone, so
    an overlay server never lowers another server's score (Consiliency/pmcp#342).
    A keyword only an overlay declares is absent and scores at the default 1.0.
    A hand-built Manifest has no base and is weighted by its own servers.
    """
    if manifest.base_keyword_weights is not None:
        return dict(manifest.base_keyword_weights)
    return keyword_weights(manifest.servers.values())


def _rank_one_cli(
    query: str,
    query_norm: str,
    query_words: set[str],
    name: str,
    cli: CLIAlternative,
    *,
    is_available: bool,
    detected_infos: Mapping[str, CLIInfo],
    include_suppressed: bool,
    min_score: float,
) -> CLIHintMatch | None:
    """Score one CLI alternative; ``None`` when it does not qualify."""
    score = 0.0
    score = max(score, _text_match_score(query_norm, query_words, cli.name))
    score = max(
        score,
        _text_match_score(query_norm, query_words, cli.description) * 0.7,
    )
    score = max(score, _keyword_match_score(query, cli.keywords))
    for example in cli.examples:
        score = max(score, _text_match_score(query_norm, query_words, example) * 0.8)

    matched_prefer_mcp_phrase = None
    for phrase in cli.prefer_mcp_for:
        if _text_match_score(query_norm, query_words, phrase) >= 1.0:
            matched_prefer_mcp_phrase = phrase
            score = max(score, 1.0)
            break

    if score < min_score:
        return None

    suppressed = matched_prefer_mcp_phrase is not None
    if suppressed and not include_suppressed:
        return None

    path = detected_infos[name].path if name in detected_infos else None
    reason = "Available on PATH" if is_available else "CLI is not detected"
    if suppressed:
        reason = (
            "MCP server preferred for "
            f"'{matched_prefer_mcp_phrase}' despite matching CLI '{name}'."
        )

    hint = CLIHint(
        available=is_available,
        path=path,
        reason=reason,
        **cli_hint_fields(cli),
    )
    return CLIHintMatch(
        hint=hint,
        score=score,
        suppressed_by_prefer_mcp=suppressed,
        matched_prefer_mcp_phrase=matched_prefer_mcp_phrase,
    )


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
        # One CLI entry must never take down every query (Consiliency/pmcp#342);
        # the loader already skips an overlay entry a consumer cannot use.
        try:
            match = _rank_one_cli(
                query,
                query_norm,
                query_words,
                name,
                cli,
                is_available=is_available,
                detected_infos=detected_infos,
                include_suppressed=include_suppressed,
                min_score=min_score,
            )
        except Exception as exc:
            logger.warning(
                f"rank_cli_hints: skipping an unusable entry ({_cli_label(name)}): "
                f"{type(exc).__name__}"
            )
            continue
        if match is not None:
            matches.append(match)

    # One rule for every list of CLI hints (Consiliency/pmcp#342 rev 7): a CLI
    # an overlay added or replaced ranks after every shipped CLI, whatever its
    # score, so `catalog_search`'s `cli_hints` and `request_capability`'s CLI
    # tiers read the same order. Within each origin: score, then name (main).
    overlay_cli_names: frozenset[str] = getattr(
        manifest, "overlay_cli_names", frozenset()
    )
    return sorted(
        matches,
        key=lambda match: (
            match.hint.name in overlay_cli_names,
            -match.score,
            match.hint.name,
        ),
    )


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

    # Check detected CLIs first (preferred). An overlay CLI ranks below every
    # server (Consiliency/pmcp#342 rev 6): it is considered only if nothing
    # else reaches the threshold.
    overlay_cli_names: frozenset[str] = getattr(
        manifest, "overlay_cli_names", frozenset()
    )
    ranked = rank_cli_hints(query, manifest, available_clis=detected_clis)
    overlay_ranked = [m for m in ranked if m.hint.name in overlay_cli_names]
    for match in ranked:
        if match.hint.name in overlay_cli_names:
            continue
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
        try:
            score = _keyword_match_score(query, server.keywords, keyword_weights)
        except Exception as exc:  # Consiliency/pmcp#342: one entry, not all
            logger.warning(
                f"match_capability: skipping an unusable entry "
                f"({_server_label(name)}): {type(exc).__name__}"
            )
            continue
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

    for match in overlay_ranked:
        if match.score >= 0.2:
            return MatchResult(
                matched=True,
                entry_name=match.hint.name,
                entry_type="cli",
                confidence=match.score,
                reasoning=f"Keyword match for installed CLI: {match.hint.name}",
                cli_config=manifest.cli_alternatives[match.hint.name],
            )

    return MatchResult(
        matched=False,
        entry_name="",
        entry_type="",
        confidence=0.0,
        reasoning="No matching capability found in manifest",
    )
