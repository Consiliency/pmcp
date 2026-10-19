"""FULL-only gate reference for the bootstrap and emergency recovery contract.

This file cannot authorize an asset exemption. The bootstrap workflow embeds its
small evaluator so recovery never depends on a policy checkout/import/API.
Normal pinned policy and asset validation are a later rollout after main passes.
"""

from __future__ import annotations

FULL_JOBS = (
    "ci-scope",
    "test",
    "install-smoke",
    "min-version-smoke",
    "lint",
    "typecheck",
    "workflows",
    "audit",
    "changelog",
    "release-diff-ack",
)
PR_ONLY = frozenset({"changelog", "release-diff-ack"})
EVENTS = frozenset({"pull_request", "push", "schedule", "workflow_dispatch"})


def validate_results(event: str, needs: dict[str, dict[str, str]]) -> bool:
    """P3-A requires literal success; only explicit non-PR N/A jobs may skip."""
    if event not in EVENTS or set(needs) != set(FULL_JOBS):
        return False
    return all(
        needs[job].get("result")
        == ("skipped" if event != "pull_request" and job in PR_ONLY else "success")
        for job in FULL_JOBS
    )
