# PMCP Specification Index

This directory contains both current source-of-truth specifications and
historical phase roadmaps. Use this index when onboarding new development work.

## Current Source Of Truth

- `tenant-code-mode-host-contract.md` - PMCP/companion-server boundary for
  hosted tenant code-mode execution. PMCP is the broker; the companion server is
  the execution authority.
- `../README.md` - user/operator documentation, setup flows, gateway tools, task
  lifecycle, tenant code-mode registration, and policy examples.
- `../SECURITY.md` - production hardening checklist, threat model, the v13 trust
  boundary claim-ledger, and explicit limits for shared-service HTTP and tenant
  code-mode hosting.
- `../SPEC_COMPLIANCE.md` - MCP specification compliance notes.
- `../CHANGELOG.md` - release notes and unreleased changes.

## Historical Roadmaps

Every `phase-plans-v1.md` through `phase-plans-v13.md` file is implementation
history; none is a pending backlog. `phase-plans-v13.md` (trust boundaries) is
the most recent: its TRUST, CONSENT, PKGID, EGRESS and SEAL phases have all
merged. `phase-plans-v10.md` is marked CLOSED in its header, and
`phase-plans-v6.md` is the completed tenant code-mode host-readiness roadmap
behind the contract above. Older files may contain unchecked planning
checkboxes from their original roadmap shape; prefer the matching
`plans/phase-plan-*` files (where they exist) and `CHANGELOG.md` when checking
what actually shipped.

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
