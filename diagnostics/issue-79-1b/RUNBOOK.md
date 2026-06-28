# Issue #79 symptom 1b — reproduction harness & findings

**Symptom (as reported):** driving long downstream calls through the PMCP gateway,
Claude Code reports `"MCP server pmcp session expired"`, typically on the **2nd+**
long call; `browser_run_code_unsafe`-style multi-step loops fail.

`"session expired"` is emitted by the **client** (the outer streamable-HTTP
session between Claude Code and the gateway), not by PMCP — so 1b is about the
outer session and/or downstream connection lifecycle, which needed a runtime
reproduction with logs to disambiguate. This harness provides that.

## What's here
- `slow_server.py` — synthetic stdio MCP server (raw JSON-RPC, no SDK) with three
  tools that exercise the candidate causes:
  - `slow_stream(seconds)` — long call that emits periodic progress notifications.
  - `slow_silent(seconds)` — long call with **no** output (no keepalive traffic).
  - `big_output(mib)` — returns ~N MiB in one result (exercises the stdio read limit).
- `synthetic.mcp.json` — registers `slowsrv` (path templated by `run.sh`).
- `repro_client.py` — connects over streamable-HTTP (same transport as Claude Code),
  issues the call sequence via `gateway.invoke`, records ok/elapsed/error per call.
  Client read timeout is 120s so we observe the **gateway/transport**, not a client timeout.
- `run.sh [PORT]` — starts a gateway (HTTP, DEBUG logs, **isolated `--lock-dir`** so it
  runs alongside your existing pmcp), drives the client, and greps the gateway log.
  Outputs land in `out/` (`gateway.log`, `client.log`, materialized config).

## How to run
```bash
cd diagnostics/issue-79-1b
./run.sh            # default port 3399 (NOT 3344 — avoids your running gateway)
# Make the read-limit candidate fire smaller/faster:
#   uncomment PMCP_STDIO_READ_LIMIT in run.sh, or run big_output with a larger mib.
```
Gotchas baked in (each cost a cycle to discover):
- `--log-level` choices are lowercase (`debug`), not `DEBUG`.
- PMCP enforces a single-instance lock (`~/.pmcp/gateway.lock`); use `--lock-dir`.
- Default port 3344 is the user's live gateway — use a fresh port or you'll test theirs.

## Findings (run 2026-06-28, repo @ main with #79/1a merged)
```
OK     8.2s  1: slow_stream 8s
OK     8.2s  2: slow_stream 8s   (the reported "2nd long call")
OK     8.2s  3: slow_silent 8s   (no keepalive traffic)
FAIL   0.3s  4: big_output 12MiB → E201 "Server slowsrv disconnected"
FAIL   0.2s  5: slow_stream 3s   → E302 (downstream gone, mid-reconnect)
```
Gateway log: `[slowsrv] stdout read error: Separator is not found, and chunk exceed the limit`.

### Candidate causes, resolved
1. **Outer streamable-HTTP session torn down during a long in-flight call** —
   **NOT reproduced.** All three 8s calls (including the silent one and the "2nd
   call") succeed and the session stays alive (`Session already exists, handling
   request directly` across all calls; the only `Terminating session` is the
   client's normal DELETE at teardown). The merged **#79/1a idle-timeout** fix
   mitigates this — the gateway no longer kills a long call at the old 30s wall,
   so the outer session isn't torn down mid-call. **This was the primary reported
   symptom and it is addressed.**
2. **10 MiB stdio read-limit disconnect — CONFIRMED, still a real bug.** A single
   downstream stdout line larger than `PMCP_STDIO_READ_LIMIT` (default 10 MiB)
   raises in the read loop (`_read_stdout`, manager.py ~1475-1499) and **disconnects
   the whole server** (E201 for the triggering call). Auto-reconnect is scheduled
   with 5/15/30s backoff, so the **next** call races the reconnect and fails too
   (E302) — exactly the "next call fails" pattern. Browser servers routinely emit
   >10 MiB in one result (full-page snapshots, screenshots, DOM dumps), so this is
   a highly plausible real-world trigger of the reported instability.
3. **Orphaned-future desync after an internal timeout** — not separately triggered
   in this run (no internal timeout fired, since 1a let the long calls complete).

## Recommended fix (the remaining 1b cause — not yet implemented)
Primary — **don't tear down the whole server for one oversized line.** In
`_read_stdout`, on an oversized-line read error, drain to the next newline, fail
only the in-flight request with a clear "output too large" error (suggest raising
`PMCP_STDIO_READ_LIMIT` or using output truncation), and keep the server connected
and its other pending/future requests alive. One giant tool result should not kill
the server.

Secondary/complementary:
- Catch `ValueError` ("Separator is not found, and chunk exceed the limit") next to
  the existing `asyncio.LimitOverrunError` branch — currently the oversized-line
  error falls through to the generic handler, so the actionable
  "set PMCP_STDIO_READ_LIMIT to raise" hint is lost.
- Consider a higher default limit for browser-class servers and/or document tuning
  `PMCP_STDIO_READ_LIMIT`.
- The dotfiles-side `--isolated` browser-profile work (tracked separately) reduces
  how often huge snapshots are produced but does not fix this gateway-side limit.

This is independent of #79/1a, /1c, and /2 (all merged) and warrants its own PR.
