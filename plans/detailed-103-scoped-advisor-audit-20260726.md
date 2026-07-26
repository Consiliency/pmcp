# Detailed plan: scoped advisor policy and privacy-safe audit

## Task

Implement and release the PMCP prerequisite for Consiliency/agent-harness#310
under Consiliency/pmcp#103. Scoped advisor sessions must fail closed on policy or
audit failure, expose only approved research controls and downstream tools, and
produce complete privacy-safe provenance.

## Changes

- Make explicitly requested policy files fatal on missing, unreadable, malformed,
  or schema-invalid input while retaining best-effort default discovery.
- Add case-sensitive `gateway_tools` allow/deny policy and enforce it in both
  discovery and dispatch.
- Add typed atomic run/seat/evidence correlations to `gateway.invoke`.
- Add an explicit `--audit-jsonl`/`PMCP_AUDIT_JSONL` sink with started,
  per-invocation, and exactly one fsynced completed record.
- Store only tool/status/correlation/digests and hashed public source references;
  never raw URLs, queries, arguments, credentials, or results.
- Advertise `scoped_advisor_audit.v1` only for the exact explicit advisor policy
  with an active audit sink; expose install support through `pmcp capabilities`.
- Prove four concurrent stdio instances operate with unique lock and audit dirs.
- Document and release the capability as PMCP v1.20.0.

## Verification

```bash
uv run ruff check src tests/test_scoped_advisor_audit.py
uv run pytest tests/test_policy.py tests/test_server.py tests/test_tools.py \
  tests/test_cli.py tests/test_singleton_lock_scope.py \
  tests/test_scoped_advisor_audit.py -q
uv run pytest -q
```

## Acceptance criteria

- [x] Explicit policy failure exits non-zero without permissive fallback.
- [x] Gateway controls are filtered in discovery and dispatch.
- [x] Invoke reaches only approved Firecrawl/Bright Data research tools.
- [x] Four concurrent stdio instances use unique lock and audit directories.
- [x] Audit proves tool/status/policy/run/seat correlation and hashed source evidence.
- [x] JSONL has typed correlations, contiguous sequence/count, and one fsynced completion.
- [x] Audit contains no raw URL, query, arguments, credentials, or result body.
- [x] Released capability/version probing lets consumers reject older PMCP.
