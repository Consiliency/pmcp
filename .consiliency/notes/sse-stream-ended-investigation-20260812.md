# Investigation: intermittent "SSE stream ended without a response"

Status: **DONE — reproduced reliably, root-caused, no fix shipped.** The
exact reported error string is reproduced by killing the downstream peer
process mid-response, in the post-headers/pre-completion window: 40%
(16/40) when the kill lands during a follow-up tool call, 6% (3/50) when it
lands during the initial `connect_server()`/`initialize` handshake — the
exact call site named in the symptom. The message is accurate in every
case; there is no pmcp defect to fix. See "Conclusion" below.

## Symptom

`Failed to connect to <name>: SSE stream ended without a response`,
intermittent, load-correlated: several downstream servers connecting or
disconnecting around the same time, not a single quiet connect.

## Step 1 — upstream trigger conditions

Source: `mcp/client/streamable_http.py` (mcp 2.0.0, this worktree's
`.venv/lib/python3.11/site-packages/mcp/client/streamable_http.py`),
`StreamableHTTPTransport._handle_sse_response` (lines ~411-459) and its
sibling `_handle_reconnection` (lines ~475-534).

`_handle_sse_response` runs once per outbound JSON-RPC *request* whose POST
got back a `text/event-stream` response (the normal case here: `fake_remote`
/ any real `mcp.server.MCPServer` defaults `json_response=False`, so every
request — `initialize`, every `tools/call` — answers over SSE, not plain
JSON). The exact sequence that reaches `_resolve_abandoned_request(...,
"SSE stream ended without a response")` (line 458):

1. The POST got a `200` with `content-type: text/event-stream`, so control
   entered `_handle_sse_response`.
2. The `async for sse in event_source:` loop over that stream terminates —
   either by running out of events (server closed the stream) or by an
   exception raised while reading it — **before** `_handle_sse_event`
   ever returns `True` (i.e., before the actual JSON-RPC response/error for
   this request arrived). Any exception here is swallowed by a bare
   `except Exception: logger.debug("SSE stream ended", exc_info=True)`
   (line 447-448) — so a `RemoteProtocolError`, a `ReadError`, a connection
   reset, an `httpx2.StreamClosed` — all look identical from this point on.
3. `last_event_id` is still `None` — i.e., **no SSE event with an `id:`
   field was ever received on this stream** before it ended. (If at least
   one `id`-bearing event *had* arrived, control instead goes to
   `_handle_reconnection`, whose own give-up path after
   `MAX_RECONNECTION_ATTEMPTS` (=2) produces the sibling message "SSE
   stream ended and reconnection attempts were exhausted" instead — a
   different message for a related but distinguishable failure.)

So the message specifically means: *a request got an SSE-framed response,
and that stream was torn down (cleanly or via exception) before sending
either the actual response or a single resumption-capable event.* Nothing
in this function is pmcp-specific; it is purely a client-side reaction to
what the peer connection did.

Two concrete real-world shapes that satisfy this, both purely at the
HTTP/TCP layer (no protocol violation needed):

- **Stale pooled connection**: `httpx2.AsyncClient`'s connection pool
  (`DEFAULT_LIMITS = Limits(max_connections=100, max_keepalive_connections=20,
  keepalive_expiry=5.0)`, `.venv/lib/python3.11/site-packages/httpx2/_config.py:248`)
  hands out a persistent connection that the *server* has since closed
  (idle timeout, worker recycle, load-triggered connection churn). The
  request write may partially succeed but the server never answers on that
  socket; the client sees a reset/EOF with zero SSE bytes received —
  `last_event_id` stays `None` by construction.
- **Server closes an SSE response stream before its first byte** under its
  own load (e.g. many sessions initializing/tearing down concurrently
  causing the server's own anyio task group / session manager to cancel or
  reap a handler before it ever calls `send()` for the first chunk).

Both are consistent with "load-correlated, several servers connect/disconnect
around the same time" — neither requires a bug in pmcp's own code; the
question this investigation had to answer was whether one is real and
reachable in-process, or whether pmcp's own transport-ownership /
lifecycle-lock code contributes a third way to reach the same message.

## Ruled out before this investigation started (do not re-litigate)

`sse_starlette.sse.AppStatus.should_exit` is a **process-global class
attribute**, latched `True` the first time any uvicorn server shuts down in
the process and never reset — every SSE stream afterwards, in any event
loop, against any server, terminates immediately with exactly this message.
Diagnosed and fixed in `tests/runtime/fake_remote.py`'s `run_fake_remote`
`finally:` block (resets `AppStatus.should_exit = False`) and documented in
`.consiliency/plans/detailed-anyio-transport-task-ownership-20260811-0204.md`
("Finding 2"). This investigation's probe (see below) is deliberately
structured to avoid rediscovering it: every trial runs in its own fresh
subprocess, and within a trial no server is torn down until the very end
(after the manager's own `disconnect_all()`), so the process-global latch
is never tripped mid-trial.

## Step 2 — reproduction attempt

Probe: `scripts/probes/sse_flake_probe.py` (single trial, one process) +
`scripts/probes/run_sse_flake_probe.py` (multi-trial driver, spawns one
fresh subprocess per trial, aggregates).

Design: N real in-process `fake_remote` Streamable HTTP servers (same
harness as `tests/runtime/test_downstream_remote.py`), one real
`ClientManager`, per cycle:
  1. `manager.connect_all(configs, retry=False)` — true concurrent connect
     across all N servers (the one call where pmcp's own `_lifecycle_lock`
     does *not* serialize the N servers' network I/O against each other —
     see manager.py:587-611 — matching the real-world "several servers
     connect around the same time" trigger, e.g. gateway startup / refresh).
  2. A burst of concurrent `gateway.invoke()` tool calls spread across the
     connected servers — every one is a POST whose response is SSE-framed
     (see Step 1), so this is what actually exercises
     `_handle_sse_response` at volume.
  3. Concurrent `disconnect_server(..., force=True)` on half the servers
     while the other half's connections/pooled sockets are still warm,
     interleaving teardown network activity with (the next cycle's)
     connect activity in time.
  4. `manager.disconnect_all()` after all cycles.

Every string in `connect_all()`'s returned errors, every
`gateway.invoke()` result's `errors`, and any escaping exception is
classified into: target hit (`"SSE stream ended without a response"`),
sibling hit (`"...reconnection attempts were exhausted"`), or other.

### Result — organic-load runs

| run | servers | cycles | calls/cycle | trials | connect_all batches | tool calls | target hits | sibling hits | crashes | wall |
|---|---|---|---|---|---|---|---|---|---|---|
| smoke | 3 | 2 | 5 | 1 | 2 | 10 | 0 | 0 | 0 | 2.8s |
| smoke2 | 8 | 5 | 20 | 10 | 50 | 1,000 | 0 | 0 | 0 | 67.9s |
| **run1** | **10** | **6** | **30** | **40** | **240** | **7,200** | **0** | **0** | **0** | 200.1s |

**0/40 trials hit the target string, across 240 concurrent-connect batches
and 7,200 SSE-response tool calls, 1,200 disconnects.** No hint of the
sibling message either. Organic load through the real code paths pmcp
exercises (`connect_all`, concurrent `gateway.invoke`, concurrent
`disconnect_server`) did not reproduce it in-process at this scale.

### Result — deliberate-race variant

Rationale for a second probe: Step 1 established that `fake_remote`'s
`MCPServer` is built with no `event_store`
(`build_fake_remote_app` in `tests/runtime/fake_remote.py` calls
`MCPServer("fake-remote")` and `mcp.streamable_http_app()` with no
`event_store=`), so `mcp.server.streamable_http`'s `event_id` is `None` for
every SSE event it ever sends (`.venv/.../mcp/server/streamable_http.py:1047-1049`).
That means the client's `last_event_id` never becomes non-`None`
(`mcp/client/streamable_http.py:429-430`), so **any** interruption of an
in-flight SSE response — not only one before its first byte — lands on the
target message rather than the reconnect-exhausted sibling. Rather than keep
betting on organic timing to land in that window, `sse_flake_probe_interrupt.py`
forces it directly: each cycle starts a `gateway.invoke()` tool call, then
concurrently starts `disconnect_server(name, force=True)` for the *same*
server after a random 0–20ms delay, racing teardown against the still-open
SSE read.

**0/40 trials, 1,200 race attempts, 0 target hits, 0 sibling hits.** But not
"clean" — 5-12 of each trial's 30 races (roughly a third overall) produced a
bare `CancelledError`. Root cause of *that*: `disconnect_server` calls
`cancel_pending_requests(name)` (manager.py:826-... , cancelling
`pending.future`) **before** it closes the remote transport
(`_close_remote_transport`). The pending future — the thing
`gateway.invoke()` is awaiting via `send_request` /
`_await_with_idle_timeout` (manager.py:2039-2134) — is a pmcp-level object,
entirely separate from the mcp SDK's own `read_stream`/`_handle_sse_response`
machinery. Cancelling it wins the race every time it fires: `gateway.invoke()`
sees `CancelledError` immediately, well before the mcp SDK's transport
owner task has even started unwinding, let alone before `_handle_sse_response`
could notice the connection dying and synthesize its own error. **A
disconnect that pmcp itself initiates against a server with an in-flight
request can therefore never surface this exact message through
`gateway.invoke()`** — pmcp's own cancellation always gets there first.  This
rules out "concurrent `disconnect_server` racing an in-flight request to the
same server" as a source, on top of Run 1 already having found no evidence
for "healthy peer, high organic connect/disconnect/invoke concurrency" as a
source.

### Dead end, documented so it isn't retried: in-process task-cancel

Before building the SIGKILL variant below, first tried
`sse_flake_probe_serverkill.py`: cancel the peer's own `uvicorn.Server.serve()`
asyncio task (`task.cancel()`, not a graceful `should_exit = True`) while a
client request is in flight, in the *same* process as the client (no
subprocess). Result: **0/10 races even touched** — every tool call
completed successfully regardless of the kill. Cancelling the outer
`serve()` task does not reliably tear down already-accepted connection
handlers (they run as separate tasks/transports uvicorn's cancellation
doesn't reach), so the client-visible TCP connection kept working. Confirmed
via `appstatus_before`/`appstatus_after` in its JSON output that this
approach also never trips the process-global `AppStatus.should_exit` latch
(good for safety, useless for reproduction). Not pursued further — recorded
here so a future attempt doesn't waste time on the same approach.

### Result — peer-process SIGKILL variant (reproduces the exact symptom)

`sse_flake_probe_sigkill.py` runs the peer in its own OS process
(`_serverkill_runner.py`) and sends it `SIGKILL` at a random delay after
starting a `gateway.invoke()` tool call — the kernel then force-closes every
fd of that process, including the client's live socket, which an in-process
`task.cancel()` could not do. This is **not** routed through any pmcp
disconnect code (`disconnect_server`/`cancel_pending_requests` are never
called until the `finally:` cleanup, after the race already resolved), so it
tests the third hypothesis: a peer that dies for reasons entirely outside
pmcp, mid-response.

| variant | delay window | trials | target hits | rate |
|---|---|---|---|---|
| mid-tool-call kill, trial 0 | 0-5ms | 10 kills | 4 | 40% |
| mid-tool-call kill, trial 2 | 0-6ms | 30 kills | 12 | 40% |
| **mid-tool-call kill, combined** | 0-6ms | **40 kills** | **16** | **40%** |

Sample hit (via `gateway.invoke()`'s own error envelope):
```
{"code":"E302","message":"SSE stream ended without a response",
 "details":{"tool_id":"probe-sigkill::fr_echo"},
 "suggestion":"Check tool arguments and server status","retryable":false}
```
The other ~60% surfaced as `E201 Server probe-sigkill disconnected`
(pmcp's own dispatcher noticing the read stream broke), not a crash and not
the sibling message — consistent with the kill landing outside the narrow
post-headers window (see below).

**This reliably, mechanistically reproduces the target failure mode.** It
is real and reachable through pmcp's actual client code, not a probe
artifact — see the exact-symptom confirmation below.

### Result — SIGKILL during the INITIAL connect (matches the reported symptom's exact call site)

The originally reported text is specifically `Failed to connect to <name>:
SSE stream ended without a response` — the format `connect_all()` /
`connect_server()` wrap around a failed `_connect_singleflight` (manager.py).
`initialize` is a `JSONRPCRequest` answered over SSE exactly like
`tools/call` (Step 1), so `sse_flake_probe_sigkill_connect.py` races the
same `SIGKILL` against `manager.connect_server()` itself instead of a
follow-up tool call.

| delay window | trials | target hits | rate |
|---|---|---|---|
| 0-8ms | 15 kills | 0 | 0% |
| 1-6ms, trial 1 | 20 kills | 2 | 10% |
| 1-6ms, trial 2 | 30 kills | 1 | 3% |
| **1-6ms, combined** | | **50 kills** | **3** | **6%** |

Sample hit, verbatim, matching the reported symptom exactly:
```
Failed to connect to probe-connect-kill: SSE stream ended without a response
```
The 0-8ms window (no hits) and the bulk of the 1-6ms window's misses
(`unhandled errors in a TaskGroup (1 sub-exception)`, wrapping an
`httpx2.ReadError`) show *why* the rate is lower here than for the
tool-call variant: a kill early enough to land before the peer has even
sent SSE response *headers* raises inside `client.stream()`'s `__aenter__`
(`_handle_post_request`, manager's underlying `httpx2.AsyncClient.stream`)
-- **outside** `_handle_sse_response`'s bare `except Exception: pass`
(streamable_http.py:447-448), so it propagates as a raw connection error
through the anyio task group instead of being caught and turned into the
target message. Only a kill that lands *after* headers arrive but *before*
the response completes reaches the swallow-and-resolve path that produces
this exact string -- correspondingly narrower window, correspondingly lower
hit rate, for a `initialize` round-trip that's fast in-process versus a
`tools/call`.

## Conclusion

**Root cause identified, but it is not a pmcp defect.** Across every
variant tested:

- Healthy peers, arbitrarily high organic connect/disconnect/invoke
  concurrency (Run 1: 240 `connect_all()` batches, 7,200 SSE-response tool
  calls, 1,200 disconnects) — **zero** reproductions. No evidence of a
  client-side race, `httpx2` connection-pool staleness
  (`keepalive_expiry=5.0`), or pmcp lifecycle-lock interaction producing
  this message while the peer stays up. This isn't just unobserved at this
  probe's scale — the connect-time SIGKILL boundary finding *structurally
  excludes* the stale-pooled-connection hypothesis specifically: a
  connection the server already closed cannot deliver fresh SSE response
  headers on reuse, so that failure mode surfaces in `client.stream()`'s
  `__aenter__` (the `TaskGroup`/`ReadError` flavor from the connect-time
  results table above), never inside `_handle_sse_response`'s swallow-and-
  resolve path that produces the target string.
- pmcp's own `disconnect_server` racing an in-flight request to the same
  server (1,200 attempts) — **zero** reproductions of the target message;
  pmcp's own `cancel_pending_requests` always wins that race first, so this
  path structurally cannot be the source either.
- The **only** thing that reliably reproduces the exact reported string,
  at both the tool-call site (40% hit rate in the right timing window) and
  the connect-time site named in the original symptom (6% hit rate, exact
  string match), is **the peer process dying while it is mid-response** to
  an SSE-framed request, specifically after it has sent response headers
  but before it finishes streaming the JSON-RPC response.

Given Step 1's finding that `mcp.server.MCPServer` built without an
`event_store` (true of every downstream this repo tests against, and
plausibly true of most real ones too, since resumability is opt-in) never
attaches an SSE event `id`, **any** such mid-response peer death is
permanently unresumable and unconditionally produces this exact message —
there is no code defect here to fix; this is the mcp SDK correctly
reporting that it cannot get a response from a peer that is no longer
there. The "load-correlated, several servers connect/disconnect around the
same time" framing is consistent with this: busy periods are exactly when a
downstream server process is more likely to be OOM-killed, restarted, or
recycled by its own supervisor, and precisely mid-response is an ordinary
place for a killed process to be caught.

**No fix is included in this PR.** There is nothing incorrect in
`src/pmcp/client/manager.py` or in how it uses the mcp SDK's transport to
change; the message is accurate. Per the task's own instructions, a
speculative fix for a hypothesis this note could not confirm (client-side
race / connection-pool staleness) is deliberately not being shipped.

## What would be tried next with more time

- **Confirm the downstream-server-death hypothesis against the real fleet**:
  correlate actual `SSE stream ended without a response` occurrences in
  production/CI logs against downstream server process restarts, OOM
  kills, or supervisor-triggered recycles in the same time window. If they
  line up, this closes the loop with the connect-time-SIGKILL finding above
  and confirms there is nothing left to chase on the pmcp side.
- **Observability improvement (not a bug fix, not attempted here)**:
  `connect_server`'s error message could distinguish "the peer died
  mid-response" (this failure mode -- likely retryable moments later once
  the peer restarts) from other SSE-transport failures, so an operator
  reading `Failed to connect to <name>: SSE stream ended without a
  response` has a more actionable signal than the raw upstream string.
  Speculative; would need product input on desired wording/behavior (e.g.
  whether `connect_server`/`connect_all` should auto-retry once for this
  specific error class) before implementing.
- **Real TCP-level interruption** (a proxy/load-balancer dropping the
  connection rather than the peer process dying) was not separately tested;
  SIGKILL of the peer process was used as the closest available local
  proxy for "the connection dies mid-response for reasons outside pmcp".
  If the real fleet's downstream servers sit behind a proxy/LB, that is a
  distinct mechanism worth its own probe (e.g. an `iptables` REJECT/DROP
  rule toggled mid-response, or a deliberately misbehaving intermediary)
  before ruling it out.

## Probe scripts (committed alongside this note)

- `scripts/probes/sse_flake_probe.py` + `run_sse_flake_probe.py` — organic
  load (Run 1).
- `scripts/probes/sse_flake_probe_interrupt.py` +
  `run_sse_flake_probe_interrupt.py` — pmcp-side disconnect/invoke race.
- `scripts/probes/sse_flake_probe_serverkill.py` — in-process task-cancel
  (dead end, kept for the record).
- `scripts/probes/sse_flake_probe_sigkill.py` /
  `sse_flake_probe_sigkill_connect.py` + `_serverkill_runner.py` —
  peer-process `SIGKILL`, mid-tool-call and mid-connect. These are the ones
  that reproduce the reported symptom; run e.g.:
  ```
  uv run python scripts/probes/sse_flake_probe_sigkill_connect.py \
      --kills 50 --min-delay-ms 1 --max-delay-ms 6 --trial-id 0
  ```
