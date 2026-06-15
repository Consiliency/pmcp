---
phase_loop_plan_version: 1
phase: RELEASE
roadmap: specs/phase-plans-v8.md
roadmap_sha256: 3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7
---

# RELEASE: Release Gate v1.14.0

## Context

Phase RELEASE implements Phase 5 of `specs/phase-plans-v8.md`: cut v1.14.0 by bumping package version metadata, writing the release changelog, reconciling the v7/v8 issue trackers, checking README operator docs, and passing the full release gate.

The roadmap hash was verified from `specs/phase-plans-v8.md` as `3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7`. Canonical `.phase-loop/` state is authoritative for this plan and marks COMPLETE, AUTHFIX, MATCHFIX, and REGFIX complete; RELEASE is unplanned. The latest REGFIX closeout is commit `5b3448c`, with verification passed and no dirty paths. Legacy `.codex/phase-loop/` state is compatibility-only and must not supersede this canonical state.

RELEASE introduces no new runtime behavior and does not dispatch publishing. It prepares the release candidate for human review by updating release metadata/docs and running the local gate only; no `git push`, PyPI publish, or external release workflow belongs in this phase.

## Interface Freeze Gates

- [ ] IF-0-RELEASE-1 - `src/pmcp/__init__.py`, `pyproject.toml`, and the `pmcp` package entry in `uv.lock` agree on version `1.14.0`; CHANGELOG has a `[1.14.0] - 2026-06-15` entry covering v7 tenant code-mode host integration, OAuth 2.1 resource-server auth with canonical audience binding, MCP Registry remote-aware discovery, and the v8 redaction/concurrency/matcher/registry remediations while preserving precise "PMCP brokers, does not execute" wording; `plans/v7-issue-tracker.md` and `plans/v7-code-review-findings.md` mark R1-R11, v7 tracker items, and cleanup findings resolved with non-secret commit evidence; README operator docs mention opt-in fail-closed resource-server auth and registry-backed discovery; and the full release gate passes: `ruff check`, `ruff format --check`, `mypy src/pmcp --exclude baml_client`, `pytest -q` with `TMPDIR=/var/tmp`, `uv build`, and `git diff --check`.

## Lane Index & Dependencies

- SL-0 - Version, changelog, and tracker reconciliation; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-1 - README operator docs sweep; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-2 - Full release-gate verification and closeout evidence; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Version, Changelog, and Tracker Reconciliation

- **Scope**: Bump PMCP to v1.14.0, turn the accumulated Unreleased release notes into a dated changelog entry, and reconcile v7/v8 tracker evidence against the completed phase commits.
- **Owned files**: `src/pmcp/__init__.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `plans/v7-issue-tracker.md`, `plans/v7-code-review-findings.md`
- **Interfaces provided**: IF-0-RELEASE-1 version metadata; IF-0-RELEASE-1 changelog entry; IF-0-RELEASE-1 tracker resolution evidence for R1-R11, v7 tracker items, registry remotes/dedup/pagination gaps, and cleanup findings
- **Interfaces consumed**: canonical `.phase-loop/` completion state; git commits `f80acc1` for COMPLETE, `8d572f1` for AUTHFIX, `cad2d97` for MATCHFIX, and `5b3448c` for REGFIX; existing `CHANGELOG.md` Unreleased bullets; `plans/v7-issue-tracker.md`; `plans/v7-code-review-findings.md`; roadmap RELEASE exit criteria
- **Parallel-safe**: yes
- **Tasks**:
  - test: Check current version sources with `rg -n "1\.13\.1|__version__|version =" src/pmcp/__init__.py pyproject.toml uv.lock` so the executor can prove the bump target before editing.
  - test: Check tracker status markers and review IDs with `rg -n "R[1-9]|R10|R11|C[1-3]|H[1-5]|M[1-8]|REG-|AUTH-|☐|resolved|done|Complete|AUTHFIX|MATCHFIX|REGFIX" plans/v7-issue-tracker.md plans/v7-code-review-findings.md`.
  - impl: Bump `src/pmcp/__init__.py` and `pyproject.toml` from `1.13.1` to `1.14.0`, then run `uv lock` so the editable `pmcp` package entry in `uv.lock` is synced without dependency churn.
  - impl: Convert the current `CHANGELOG.md` Unreleased material into `## [1.14.0] - 2026-06-15`, preserving the tenant code-mode wording that PMCP brokers discovery/invocation/task lifecycle/policy/redaction/operator guidance and does not run scripts itself.
  - impl: Add concise release notes for v8 fixes: complete task redaction, reconnect lifecycle locking without retry-backoff starvation, resource-server canonical audience/JWKS/alg/error mapping, matcher real-manifest query recovery, remote-aware latest-only paginated registry consumption, stable registry cache path, and project-root remote auth consistency.
  - impl: Reconcile `plans/v7-issue-tracker.md` and `plans/v7-code-review-findings.md` so R1-R11, the registry remotes/dedup/pagination gaps, and the cleanup findings are marked resolved with non-secret commit references or phase names from the completed v8 DAG.
  - verify: `python - <<'PY'\nimport re, pathlib\nexpected = '1.14.0'\nassert pathlib.Path('src/pmcp/__init__.py').read_text().count(f'__version__ = "{expected}"') == 1\nassert re.search(r'^version = "1\\.14\\.0"$', pathlib.Path('pyproject.toml').read_text(), re.M)\nlock = pathlib.Path('uv.lock').read_text()\nassert re.search(r'\\[\\[package\\]\\]\\nname = "pmcp"\\nversion = "1\\.14\\.0"', lock)\nPY`
  - verify: `rg -n "\[1\.14\.0\] - 2026-06-15|brokers|does not run scripts|R1|R11|resolved|5b3448c|f80acc1|8d572f1|cad2d97" CHANGELOG.md plans/v7-issue-tracker.md plans/v7-code-review-findings.md`
  - verify: `git diff --check -- src/pmcp/__init__.py pyproject.toml uv.lock CHANGELOG.md plans/v7-issue-tracker.md plans/v7-code-review-findings.md`

### SL-1 - README Operator Docs Sweep

- **Scope**: Verify and adjust README operator documentation so the release docs accurately describe opt-in resource-server auth, registry-backed discovery, and the host/broker boundary without adding new behavior.
- **Owned files**: `README.md`
- **Interfaces provided**: IF-0-RELEASE-1 README operator documentation for resource-server auth, registry-backed discovery, and PMCP's broker-only tenant code-mode boundary
- **Interfaces consumed**: existing README resource-server auth section; existing README registry discovery sections; existing README tenant code-mode host section; SL-0 changelog wording for release consistency; AUTHFIX and REGFIX completed behavior
- **Parallel-safe**: yes
- **Tasks**:
  - test: Inspect the existing docs with `rg -n "resource-server|resource server|registry-backed|Registry-backed|MCP Registry|tenant code-mode|brokers|does not run scripts|cache" README.md` before editing.
  - impl: Ensure the resource-server section states that mode is opt-in and fail-closed without configured issuer, HTTPS public JWKS URL, canonical resource audience, allowed algorithms, and scopes; keep shared-secret mode documented as the backward-compatible single-tenant guard.
  - impl: Ensure registry-backed discovery docs describe candidates as read-only metadata that can include remote transport/auth metadata and that PMCP does not install, connect, or pass credentials until the operator explicitly registers/provisions a result.
  - impl: Correct any stale registry cache wording after REGFIX so README does not imply the default registry cache is cwd-relative `.mcp-gateway` when the implementation now uses a stable PMCP cache base.
  - impl: Preserve the tenant code-mode boundary wording: PMCP brokers discovery, invocation, downstream task lifecycle, policy, redaction, and operator guidance; the companion tenant server owns sandbox execution, tenant authorization, logs, and artifacts.
  - verify: `rg -n "resource-server|canonical|JWKS|registry-backed|Registry-backed|read-only discovery metadata|brokers|does not run scripts|stable" README.md`
  - verify: `git diff --check -- README.md`

### SL-2 - Full Release-Gate Verification and Closeout Evidence

- **Scope**: Run the full v1.14.0 release gate, prove IF-0-RELEASE-1 is complete, and inventory phase-owned dirty paths for runner closeout.
- **Owned files**: none
- **Interfaces provided**: IF-0-RELEASE-1 verification evidence; release-gate command results; phase-owned dirty-path inventory for runner closeout
- **Interfaces consumed**: IF-0-RELEASE-1; SL-0 version/changelog/tracker updates; SL-1 README docs updates; roadmap RELEASE exit criteria; canonical `.phase-loop/` dependency completion state
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm lane ownership remains disjoint: SL-0 owns version/changelog/tracker files, SL-1 owns `README.md`, and SL-2 is read-only.
  - test: Confirm no external release dispatch occurred: no `git push`, PyPI publish, or `gh workflow run` is part of this phase.
  - test: Confirm every dirty path is either `src/pmcp/__init__.py`, `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `plans/v7-issue-tracker.md`, `plans/v7-code-review-findings.md`, or `README.md` before closeout.
  - verify: `TMPDIR=/var/tmp uv run ruff check src/ tests/`
  - verify: `TMPDIR=/var/tmp uv run ruff format --check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `TMPDIR=/var/tmp uv run pytest -q`
  - verify: `uv build`
  - verify: `git diff --check`
  - verify: `git status --short`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-2: work-unit=`phase_verify`, effort=`high`, unsupported=`inherit_default`, inherit-default=`true`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
python - <<'PY'
import re, pathlib
expected = '1.14.0'
assert pathlib.Path('src/pmcp/__init__.py').read_text().count(f'__version__ = "{expected}"') == 1
assert re.search(r'^version = "1\.14\.0"$', pathlib.Path('pyproject.toml').read_text(), re.M)
lock = pathlib.Path('uv.lock').read_text()
assert re.search(r'\[\[package\]\]\nname = "pmcp"\nversion = "1\.14\.0"', lock)
PY
rg -n "\[1\.14\.0\] - 2026-06-15|brokers|does not run scripts|R1|R11|resolved|5b3448c|f80acc1|8d572f1|cad2d97" CHANGELOG.md plans/v7-issue-tracker.md plans/v7-code-review-findings.md
rg -n "resource-server|canonical|JWKS|registry-backed|Registry-backed|read-only discovery metadata|brokers|does not run scripts|stable" README.md
TMPDIR=/var/tmp uv run ruff check src/ tests/
TMPDIR=/var/tmp uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
TMPDIR=/var/tmp uv run pytest -q
uv build
git diff --check
git status --short
```

## Acceptance Criteria

- [ ] `src/pmcp/__init__.py`, `pyproject.toml`, and the `pmcp` package entry in `uv.lock` all report `1.14.0`.
- [ ] `CHANGELOG.md` contains `## [1.14.0] - 2026-06-15` and covers the v7 tenant code-mode host integration, OAuth 2.1 resource-server auth with canonical audience binding, MCP Registry remote-aware discovery, and all v8 remediation categories.
- [ ] Changelog and README wording preserve that PMCP brokers/discovers/invokes/monitors/redacts/guides but does not execute scripts itself; sandbox execution remains owned by the companion tenant server.
- [ ] `plans/v7-issue-tracker.md` and `plans/v7-code-review-findings.md` reconcile R1-R11, v7 tracker items, registry remotes/dedup/pagination gaps, and cleanup findings as resolved with non-secret phase or commit evidence.
- [ ] README documents opt-in fail-closed resource-server auth and registry-backed discovery as read-only metadata until explicit operator registration/provisioning.
- [ ] No release dispatch, push, PyPI publish, or external workflow run occurs in this phase.
- [ ] Full release gate passes: `ruff check`, `ruff format --check`, `mypy src/pmcp --exclude baml_client`, `pytest -q` with `TMPDIR=/var/tmp`, `uv build`, and `git diff --check`.
