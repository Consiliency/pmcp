# PMCP Specification Index

This directory contains both current source-of-truth specifications and
historical phase roadmaps. Use this index when onboarding new development work.

## Current Source Of Truth

- `tenant-code-mode-host-contract.md` - PMCP/companion-server boundary for
  hosted tenant code-mode execution. PMCP is the broker; the companion server is
  the execution authority.
- `phase-plans-v6.md` - completed tenant code-mode host-readiness roadmap. All
  phase exit criteria are reconciled; no further v6 phase is pending.
- `../README.md` - user/operator documentation, setup flows, gateway tools, task
  lifecycle, tenant code-mode registration, and policy examples.
- `../SECURITY.md` - production hardening checklist, threat model, and explicit
  limits for shared-service HTTP and tenant code-mode hosting.
- `../CHANGELOG.md` - release notes and unreleased changes.

## Historical Roadmaps

The older `phase-plans-v1.md` through `phase-plans-v5.md` files are retained as
implementation history. They may contain unchecked planning checkboxes from
their original roadmap shape; prefer the matching `plans/phase-plan-*` closeout
files and `CHANGELOG.md` when checking what actually shipped.

`active/pmcp-gateway-orchestration-plan.md` is also historical despite living
under `active/`; it is marked implemented in the file header and should not be
used as the current development backlog.

## Team Cleanup Candidates

- Split large tests such as `tests/test_tools.py` and
  `tests/test_client_manager.py` by feature area.
- Promote durable release gates into a short developer guide once the next
  release branch is cut.
- Keep future roadmap files reconciled with phase closeout artifacts before
  handoff.
