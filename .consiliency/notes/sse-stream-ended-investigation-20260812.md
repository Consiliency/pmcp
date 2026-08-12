# Investigation: intermittent "SSE stream ended without a response"

Status: IN PROGRESS — draft written while probe trials run; final numbers
filled in below once the batch completes.

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

<!-- INTERRUPT_RESULTS_PLACEHOLDER -->

## What would be tried next with more time

(filled in after the reproduction attempt, whichever branch it lands on)
