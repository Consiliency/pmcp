---
phase_loop_plan_version: 1
phase: P3B
roadmap: specs/phase-plans-v11.md
roadmap_sha256: a1bb70e3bc2c5d6c857e9750578ed7f07f2b5c0dc731981eeb07c3c8934d08b9
---

# P3B: Subscriptions and GET retirement

## Context

Every fact below was read or measured this session in the worktree
`/mnt/workspace/worktrees/pmcp-plan-p3b` at `main` `97ed73e` (v1.22.0, P2 merged), against
the `mcp` 2.0.0 / `mcp_types` 2.0.0 installed in its `.venv`. Two throwaway spikes were run
and their transcripts are quoted below rather than paraphrased. Line citations were
re-derived this session — `main` has moved through P2 and several roadmap line numbers are
stale.

### The SDK already ships the listener; pmcp's work is the wiring, not the machinery

This is the single largest correction to the roadmap's framing. `mcp` 2.0.0 ships
`mcp/server/subscriptions.py`, which contains **both** halves of `subscriptions/listen`:

- `ListenHandler(bus, *, max_subscriptions=1024, max_buffered_events=1024)` (`:157-254`) —
  an `async __call__(ctx, params) -> SubscriptionsListenResult` that already acknowledges
  first, stamps every frame with `_meta["io.modelcontextprotocol/subscriptionId"] =
  ctx.request_id` (`:190`, `:197`), filters by the honoured `SubscriptionFilter` via
  `event_matches` (`:205`), bounds concurrent streams and per-stream backlog (`:193`,
  `:211-217`), and on `close()` (`:243-254`) lets each stream drain and emit its
  `SubscriptionsListenResult` as the final frame.
- `SubscriptionBus` (a `Protocol`, `:72-91`) and `InMemorySubscriptionBus` (`:94-125`) — the
  fan-out seam. `publish(event)` is **async**; `subscribe(listener)` is **sync** and returns
  an idempotent unsubscribe.

The event vocabulary is `mcp/shared/subscriptions.py`: frozen dataclasses `ToolsListChanged`,
`PromptsListChanged`, `ResourcesListChanged`, `ResourceUpdated(uri)`, the union alias
`ServerEvent` (`:62`), the constant `SUBSCRIPTION_ID_META_KEY =
"io.modelcontextprotocol/subscriptionId"` (`:36`), and `event_to_notification` (`:66-74`)
which maps each event to its `notifications/*/list_changed` wire form.

`Server.__init__` accepts `on_subscriptions_listen` (`mcp/server/lowlevel/server.py`, in the
`_spec_requests` table alongside `("subscriptions/listen",
types.SubscriptionsListenRequestParams, on_subscriptions_listen)`), and
`ListenHandler.__call__`'s signature is exactly the constructor kwarg's declared type. **So
pmcp writes no listen-stream state machine.** What pmcp must supply is: a bus instance, a
`ListenHandler` registered on its `Server`, an event path from `ClientManager`'s catalog
mutations into `bus.publish`, an HTTP transport that does not kill the stream, and the GET
retirement.

### Measured spike 1 — stdio/duplex behaviour, end to end

Ran in-process against a lowlevel `Server(on_subscriptions_listen=ListenHandler(bus))` driven
by `Server.run` over an anyio memory-stream pair, with hand-built modern-envelope frames. The
connection opened **modern** era with no `initialize` at all (`serve_dual_era_loop` picks the
era from the first request's `_meta`, `mcp/server/runner.py:602-640`). Observed frames, in
order:

```
{"method":"notifications/subscriptions/acknowledged",
 "params":{"_meta":{"io.modelcontextprotocol/subscriptionId":7},
           "notifications":{"toolsListChanged":true,"resourcesListChanged":true}}}
{"method":"notifications/tools/list_changed","params":{"_meta":{"...subscriptionId":7}}}
{"method":"notifications/subscriptions/acknowledged",
 "params":{"_meta":{"...subscriptionId":9},"notifications":{"promptsListChanged":true}}}
{"method":"notifications/prompts/list_changed","params":{"_meta":{"...subscriptionId":9}}}
{"id":9,"result":{"_meta":{"...subscriptionId":9,
                           "io.modelcontextprotocol/serverInfo":{...}},"resultType":"complete"}}
```

Which proves, measured rather than inferred: the ack is the first frame and carries the id;
a published `ToolsListChanged` arrives stamped with the same id; a published
`PromptsListChanged` is **not** delivered to subscription 7, which did not request it; a
second concurrent subscription demultiplexes correctly by id; `notifications/cancelled`
with `requestId: 7` ends subscription 7 with **no** result frame and a later
`ToolsListChanged` publish delivers nothing to it; and `ListenHandler.close()` emits the
`SubscriptionsListenResult` for the still-open subscription 9 before the stream ends.
That covers EC-P3B-1 and both directions of EC-P3B-2 at the protocol layer.

### `subscriptions/listen` is modern-era only, and that is load-bearing

`mcp_types.methods.validate_client_request("subscriptions/listen", <version>, params)` raises
`KeyError` for `2024-11-05`, `2025-06-18`, and `2025-11-25`, and validates only at
`2026-07-28` (where `params._meta` is a **required** field). `runner.py:194-197` turns that
`KeyError` into `METHOD_NOT_FOUND`. Consequences the plan is built on:

- Every test that opens a subscription must send the full IF-0-P2-4 modern envelope. A
  handshake-era `subscriptions/listen` is correctly `METHOD_NOT_FOUND` and that is an
  assertion, not a bug.
- pmcp already serves the modern era on both transports: HTTP via
  `StreamableHTTPSessionManager.handle_request`'s header check
  (`mcp/server/streamable_http_manager.py:181-187` → `handle_modern_request`), stdio via
  `Server.run` → `serve_dual_era_loop`. No transport work is needed to *reach* the handler.

### Measured spike 2 — the HTTP landmine the roadmap does not name

`handle_mcp` wraps **all** session-manager traffic in
`asyncio.wait_for(session_manager.handle_request(...), timeout=request_timeout)`
(`src/pmcp/transport/http.py:728-734`), where `request_timeout` defaults to `60`
(`transport/http.py:265`, `server.py:99`). A `subscriptions/listen` POST is ordinary POST
traffic to that wrapper. Ran pmcp's **own** `create_http_app(server, request_timeout=3)` under
uvicorn on an allocated spare port and opened a real listen stream:

```
== POST listen == 200 text/event-stream
   frame t=0.1s  notifications/subscriptions/acknowledged  subscriptionId=42
   frame t=1.0s  notifications/tools/list_changed          subscriptionId=42
handle_mcp [c0441c79]: request timed out after 3s
ERROR:    ASGI callable returned without completing response.
== stream ended with RemoteProtocolError (incomplete chunked read) at t=3.0s
STREAM LIFETIME: 3.0s (request_timeout=3s)
```

Re-run with `request_timeout=30` and a second publish at t=5s: three frames, stream alive at
t=20s (the 20s mark is the SDK's own 15s `_SSE_PING_INTERVAL` keepalive line arriving). So
`request_timeout` is the **sole** killer, and on defaults every HTTP subscription dies after
60 seconds with a truncated body — not the graceful `SubscriptionsListenResult` EC-P3B-2
requires. This is a false-green generator: an EC-P3B-4 test that receives its notification
within 60s passes while every real client loses its stream every minute. Lane A therefore owns
a `subscriptions/listen` exemption from the timeout wrapper, and there is an acceptance item
that fails without it (V4b / SL-4.3).

### Measured spike 2b — the old GET flow hangs today, exactly as EC-P3B-3 forbids

In the same spike, `GET /mcp` with `Accept: text/event-stream` and
`MCP-Protocol-Version: 2026-07-28` did **not** return the SDK's 405. It hung until the client
gave up. The cause is pmcp's own rmcp-compat branch at `transport/http.py:600`: a GET with no
`mcp-session-id` is answered with an infinite keep-alive SSE stream (`:625-653`) **before**
the request ever reaches the session manager. `/health` and `/metrics` in the same spike
returned `200`.

The SDK would have answered correctly on its own — `handle_modern_request` rejects any
non-POST with `405` + `Allow: POST` (`mcp/server/_streamable_http_modern.py:326-330`), and the
legacy path's `_handle_get_request` (`mcp/server/streamable_http.py:687`) is only reachable at
handshake versions. The hang is entirely pmcp's compat shim.

### Retiring GET removes a channel pmcp never writes to

`grep -rn "notifications/\|send_notification\|list_changed" src/pmcp/` returns four hits and
**none** of them is a server-initiated notification: three are the rmcp-compat
`notifications/initialized` special-case in `transport/http.py:682-694`, and one is
`client/manager.py:1944`, where pmcp sends `notifications/initialized` **downstream as a
client**. pmcp has never published anything on the legacy standalone GET stream. Retirement
therefore removes a dead channel; `subscriptions/listen` is the first server→client path pmcp
has ever had. This is the evidence that makes EC-P3B-3 low-risk and it is why the roadmap's
framing of GET retirement as "the one part that changes observable client behaviour" is
right about the *contract* and overstated about the *data*: what breaks is clients that open
GET, not clients that receive anything over it.

### The publisher gap — measured, and worse than "one file"

`ClientManager` mutates exactly three indexes, in exactly four methods:

- `_index_tools` (`client/manager.py:1106`, writes `self._tools` at `:1149`)
- `_index_resources` (`:1153`)
- `_index_prompts` (`:1181`)
- `_remove_server_indexes` (`:927`), which clears all three plus `self._tasks`

`_remove_server_indexes` is called from four sites (`:859`, `:1262`, `:1431`, `:2078`), and
`_index_*` from `_index_capabilities` (`:1220-1248`). All four mutators are **sync**; the bus's
`publish` is **async**. That seam is the whole design problem, and it is why the naive
"await bus.publish() at the mutation site" does not typecheck.

**The roadmap's `:1099` is stale** — `_index_tools` is at `:1106` on `97ed73e`.

More importantly, the async entry points that reach those mutators are not the three the
roadmap names. `gateway.refresh` does **not** call `ClientManager.refresh`: `tools/handlers.py:2361-2373`
calls `disconnect_server(name, force=True)` then `connect_all(to_connect)`. The full set of
async `ClientManager` entry points that can change the catalog is `connect_all` (`:521`),
`ensure_connected` (`:689`), `connect_server` (`:741`), `disconnect_server` (`:778`),
`restart_server` (`:873`), `_reconnect_loop` (`:1683`, background), `disconnect_all` (`:1956`),
`refresh` (`:2080`), and `adopt_process` (`:2086`). **Enumerating flush call sites over that
list is exactly the failure mode P5 documented** (a requirement re-implemented per call site,
where fixing a subset looks like fixing the class). This plan therefore does not enumerate
them: IF-0-P3B-1 makes the sink self-draining, so a *new* mutation path publishes without any
new call site, and SL-5 adds an AST guard that fails if a write to `_tools`/`_resources`/
`_prompts` ever appears outside the four known mutators.

### Roadmap staleness found while planning

1. **EC-P3B-1's "IF-0-P2-2 shape" is stale two ways.** The roadmap (`specs/phase-plans-v11.md:100`)
   defines IF-0-P2-2 as `self._server.add_request_handler(<method>, <ParamsType>, <handler>)`.
   But (a) P2's executed plan defines IF-0-P2-2 as the *downstream Streamable HTTP transport
   contract* (`plans/phase-plan-v11-P2.md:323`) — the ID collides; and (b) P2's Decision 1
   (`plans/phase-plan-v11-P2.md:648-666`, confirmed in the roadmap's own Phase 2
   post-execution amendment item 6) **rejected** `add_request_handler` in favour of
   `Server.__init__(on_*=...)`, and IF-0-P2-1 states "no `add_request_handler` call is added".
   P3B honours EC-P3B-1's *intent* — register through the frozen handler-registration
   mechanism — which on `97ed73e` is the constructor kwarg `on_subscriptions_listen=`. This is
   also the only form mypy checks: the kwarg is typed
   `Callable[[ServerRequestContext[L], SubscriptionsListenRequestParams],
   Awaitable[SubscriptionsListenResult]] | None`, which `ListenHandler.__call__` satisfies
   exactly, whereas `add_request_handler` erases the result type to
   `HandlerResult = BaseModel | dict | None`. Recorded as a roadmap amendment by SL-6.
2. **`client/manager.py:1099`** → `_index_tools` is at `:1106`.
3. **The roadmap's 4-lane partition omits nothing this time, but under-scopes Lane A.** Lane A
   is described as "GET retirement, route table, protocol-version header"; the measured
   `request_timeout` finding above means Lane A must also exempt `subscriptions/listen` from
   the timeout wrapper, and must replace the DoS guard it deletes.
4. **`src/pmcp/subscriptions.py` is a new file the roadmap's Key files does not list**, and
   `CHANGELOG.md`/`pyproject.toml`/`src/pmcp/__init__.py` need an owner for the 2.0.0 release
   framing. SL-1 takes all four.

### Repo state the lanes inherit

`tests/` is flat: 50 `tests/test_*.py` plus `tests/conftest.py` and `tests/__init__.py`, plus
`tests/fixtures/**`, `tests/mcp2x/**` (P2's per-lane unit tests) and `tests/runtime/**`
(P2's deployed-wire acceptance harness). `tests/runtime/harness.py` now has a real
`alloc_port()` (`:87-95`, binds `127.0.0.1:0`), `booted_gateway()` (`:139`), the session-scoped
`gateway_on_spare_port` fixture (`:294`), `modern_envelope()` (`:306`), `decode_modern_response()`
(`:332`), `modern_post()` (`:355`), and `open_socket_fd_count()` (`:377`). `decode_modern_response`
reads a **complete** response and therefore cannot read a listen stream — SL-5 adds an
incremental reader beside it. `scripts/validate_plan_doc.py` does **not** exist in this repo;
there is no `validate-plan` subcommand. `phase_loop_runtime` imports from the **system**
`python3`, not from `.venv`. `CHANGELOG.md`'s `## [Unreleased]` block (line 8) already holds
P2's entry, written by P2's SL-1. `pyproject.toml:7` and `src/pmcp/__init__.py:3` both say
`1.22.0`. Three existing test modules assert the behaviour this phase deletes:
`tests/test_http_dos.py:56-88` (keepalive concurrency cap and deadline),
`tests/test_http_transport.py:225-250` (`/mcp` accepts GET, and `"GET" in route.methods`), and
`tests/test_transport_http.py:212` (a pre-session GET returning keep-alive SSE). A live
gateway serves real traffic on `127.0.0.1:3344` as a systemd **user** unit at 1.22.0; no step
in this plan binds, signals, or restarts it, and no pid is hardcoded anywhere.

## Interface Freeze Gates

- [ ] IF-0-P3B-1 — **Catalog event sink (new module `src/pmcp/subscriptions.py`), published
  day 1.** Two public names, both re-using the SDK's vocabulary rather than inventing a
  parallel one:

  ```python
  class CatalogEventSink(Protocol):
      def note_tools_changed(self) -> None: ...
      def note_resources_changed(self) -> None: ...
      def note_prompts_changed(self) -> None: ...
      async def flush(self) -> None: ...

  class BusCatalogEventSink:
      def __init__(self, bus: SubscriptionBus) -> None: ...
  ```

  `SubscriptionBus` and the `ServerEvent` members are imported from
  `mcp.server.subscriptions` / `mcp.shared.subscriptions`; **no pmcp event type is defined**.
  Semantics, frozen:
  - Each `note_*` is **sync**, never raises, and adds the corresponding `ServerEvent` class to
    a pending set (a *set*, so N index writes in one connect coalesce to one event per kind —
    the SDK documents these as level triggers, "this changed, refetch if you care").
  - Each `note_*` then **self-schedules a drain**: if no drain task is live and
    `asyncio.get_running_loop()` succeeds, it creates one (kept in a strong-referenced set so
    it is not GC'd mid-flight) that awaits `asyncio.sleep(0)` and then drains. If there is no
    running loop, `note_*` records and returns — a sync unit test must not raise.
  - `flush()` drains the pending set immediately: pop all, `await bus.publish(<event>())` per
    kind, in the fixed order tools → resources → prompts. It is idempotent and a no-op when
    empty.
  - **Self-scheduling is the correctness mechanism, not the flush call sites.** A future
    mutation path publishes with no new call site. SL-5's AST guard is what keeps that true.
  - A raising bus is isolated: `_drain` catches and logs, matching `InMemorySubscriptionBus.publish`'s
    own listener-isolation contract (`mcp/server/subscriptions.py:110-115`).
  Consumed by SL-2 (constructs the bus and the sink), SL-3 (calls `note_*`), SL-5 (tests).

- [ ] IF-0-P3B-2 — **Ownership and construction order.** `GatewayServer.__init__`
  (`src/pmcp/server.py:85-182`) creates, in this order and before `ClientManager`:
  `self._subscription_bus = InMemorySubscriptionBus()`, then
  `self._catalog_events = BusCatalogEventSink(self._subscription_bus)`, then
  `self._listen_handler = ListenHandler(self._subscription_bus,
  max_subscriptions=<PMCP_MAX_LISTEN_STREAMS, default 64>)`. `ClientManager.__init__`
  (`src/pmcp/client/manager.py:491-519`) gains a keyword-only
  `catalog_events: CatalogEventSink | None = None`; when `None` it stores a null sink so every
  existing construction site (tests, `cli.py`) keeps working unchanged. `GatewayServer.__init__`
  passes `catalog_events=self._catalog_events` at `server.py:142`. `_create_server`
  (`server.py:184-195`) adds `on_subscriptions_listen=self._listen_handler` to the existing
  `Server(...)` call — **the P2 IF-0-P2-1 constructor form, not `add_request_handler`** (see
  Context → Roadmap staleness 1). `GatewayServer.shutdown()` (`server.py:917`) calls
  `self._listen_handler.close()` as its **first** statement, before
  `stop_stale_indexer()`/`disconnect_all()`. This ordering is a freeze because `_create_server`
  runs after `__init__`, so a bus created in `_create_server` would be invisible to the
  `ClientManager` already built at `:142`. Consumed by SL-3 and SL-4.

- [ ] IF-0-P3B-3 — **HTTP route + timeout contract.** `create_http_app`'s `/mcp` `Route` is
  `methods=["POST", "DELETE"]` — `"GET"` removed, `DELETE` retained (legacy session
  termination is out of scope). Starlette then answers `GET /mcp` with `405` and
  `Allow: POST, DELETE` (verified this session against Starlette's own router), which is
  EC-P3B-3's "defined error rather than a hang". `/health` and `/metrics` remain separate
  `Route` objects with `methods=["GET"]` and are untouched. The entire pre-session keep-alive
  block is deleted: `transport/http.py:593-653`, plus the module-level
  `_DEFAULT_MAX_KEEPALIVE_STREAMS` (`:78`), `_DEFAULT_KEEPALIVE_MAX_SECONDS` (`:79`),
  `_KEEPALIVE_HEARTBEAT_SECONDS` (`:80`), `_keepalive_active` (`:86`), and the now-unused
  `StreamingResponse`/`AsyncIterator` imports if nothing else needs them. The
  `session_compatibility` diagnostics literal (`:328-331`) changes
  `"pre_session_get": "rmcp_keepalive"` to `"get_stream": "retired"`, and the module docstring
  (`:1-11`) is rewritten to describe POST-only. **And `handle_mcp` exempts
  `subscriptions/listen` from the `asyncio.wait_for(..., timeout=request_timeout)` wrapper at
  `:728-734`**: the already-read `body_bytes` is parsed once (guarded `try/except`) into
  `body_method`, and when `body_method == "subscriptions/listen"` the call is
  `await session_manager.handle_request(...)` with no `wait_for`. The parse replaces the
  narrower one at `:690` rather than adding a second. Rationale and evidence: measured spike 2.
  Consumed by SL-5.

- [ ] IF-0-P3B-4 — **Listen-stream wire contract** (test-facing; frozen so SL-5 cannot
  approximate it). A subscription is opened by POSTing to `<base>/mcp` the IF-0-P2-4 modern
  envelope with `method: "subscriptions/listen"` and
  `params.notifications` a `SubscriptionFilter` **in wire (camelCase) form** —
  `{"toolsListChanged": true, "promptsListChanged": true}`. `Accept` must carry **both**
  `application/json` and `text/event-stream`: `_streamable_http_modern.py:369-373` answers
  `406` for `subscriptions/listen` without SSE accept regardless of `json_response`. The
  response is always `text/event-stream` (`:403-406`); frames are `data: {...}` lines. The
  first frame is `notifications/subscriptions/acknowledged`; every frame carries
  `params._meta["io.modelcontextprotocol/subscriptionId"]` equal to the request's JSON-RPC
  `id`; the terminal frame on a server-initiated close is the JSON-RPC **response**
  `{"id": <id>, "result": {"_meta": {...}, "resultType": "complete"}}`. A `: ping` comment line
  arrives every `_SSE_PING_INTERVAL` (15s) with no event. Tests import
  `SUBSCRIPTION_ID_META_KEY` from `mcp.shared.subscriptions` rather than retyping the literal,
  and build the envelope with `tests/runtime/harness.py`'s existing `modern_envelope()`.
  **In pmcp Python source, `SubscriptionFilter` is constructed with field names
  (`tools_list_changed=`), never the camelCase alias** — P2's post-execution rule 6: the alias
  validates at runtime but fails `mypy`. camelCase appears only inside JSON test payloads.

## Lane Index & Dependencies

SL-1 — Preamble: catalog event sink + 2.0.0 release framing
  Depends on: (none)
  Blocks: SL-2, SL-3, SL-4, SL-5, SL-6
  Parallel-safe: no (preamble; no downstream lane modifies SL-1's files)

SL-2 — Listen handler registration and shutdown (roadmap Lane B)
  Depends on: SL-1
  Blocks: SL-5, SL-6
  Parallel-safe: yes

SL-3 — Production event publishers in ClientManager (roadmap Lane C)
  Depends on: SL-1
  Blocks: SL-5, SL-6
  Parallel-safe: yes

SL-4 — GET retirement and stream-safe HTTP transport (roadmap Lane A)
  Depends on: SL-1
  Blocks: SL-5, SL-6
  Parallel-safe: yes

SL-5 — Test repair and end-to-end subscription acceptance (roadmap Lane D)
  Depends on: SL-1, SL-2, SL-3, SL-4
  Blocks: SL-6
  Parallel-safe: no

SL-6 — Documentation & spec reconciliation (author-facing alias: SL-docs)
  Depends on: SL-1, SL-2, SL-3, SL-4, SL-5
  Parallel-safe: no

## Lanes

### SL-1 — Preamble: catalog event sink + 2.0.0 release framing

- **Scope**: Publish `src/pmcp/subscriptions.py` (IF-0-P3B-1) on day 1 so SL-2, SL-3 and SL-5
  all develop against a real type rather than a reading of one, and carry the single-writer
  release framing: promote `## [Unreleased]` into `## [2.0.0]` and bump the version string.
- **Owned files**: `src/pmcp/subscriptions.py`, `pyproject.toml`, `src/pmcp/__init__.py`, `CHANGELOG.md`, `tests/mcp2x/test_subscription_contract.py`
- **Interfaces provided**: IF-0-P3B-1
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (preamble, terminal in preamble position)

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/mcp2x/test_subscription_contract.py` | `CatalogEventSink` is a `runtime_checkable` `Protocol` with exactly the four IF-0-P3B-1 members; `BusCatalogEventSink` satisfies it (`isinstance` under `runtime_checkable`); `note_tools_changed()` called **outside** any running loop does not raise and leaves the event pending, and a later `await flush()` publishes exactly one `ToolsListChanged` to a recording bus; three `note_tools_changed()` calls coalesce to **one** published event (the set semantics); `note_*` for all three kinds then `flush()` publishes in tools→resources→prompts order; inside a running loop, `note_tools_changed()` alone (no `flush`) results in a published event after `await asyncio.sleep(0.05)` — this is the self-scheduling assertion and the one that makes IF-0-P3B-1's correctness mechanism testable; a bus whose `publish` raises is caught and does not propagate, and a subsequent `note_*`/`flush` still works; the module imports `ToolsListChanged` etc. from `mcp.shared.subscriptions` and defines **no** event class of its own (`ast` walk: zero `ClassDef` whose bases are empty and whose name ends `Changed`) | `uv run pytest tests/mcp2x/test_subscription_contract.py -q` |
| SL-1.2 | impl | SL-1.1 | `src/pmcp/subscriptions.py` | — | — |
| SL-1.3 | impl | SL-1.2 | `CHANGELOG.md`, `pyproject.toml`, `src/pmcp/__init__.py` | — | — |
| SL-1.4 | verify | SL-1.3 | `src/pmcp/subscriptions.py`, `pyproject.toml`, `src/pmcp/__init__.py`, `CHANGELOG.md`, `tests/mcp2x/test_subscription_contract.py` | all SL-1 tests | `uv run pytest tests/mcp2x/test_subscription_contract.py -q && uv run ruff check src/pmcp/subscriptions.py && uv run mypy src/pmcp --exclude baml_client && rg -n '^## \[2\.0\.0\]' CHANGELOG.md && rg -n '^version = "2\.0\.0"' pyproject.toml && rg -n '^__version__ = "2\.0\.0"' src/pmcp/__init__.py` |

SL-1.3 is the release framing, and it deviates from the roadmap deliberately — see
`## Execution Notes > Decision 4`. It renames the existing `## [Unreleased]` heading (line 8,
which already contains P2's `### Changed` entry) to `## [2.0.0] - <ISO date>`, inserts a fresh
empty `## [Unreleased]` above it, and **prepends** a `### Removed` block inside `[2.0.0]` so
the GET retirement is the first thing a reader sees, ahead of P2's mcp-2.x paragraph. The
`### Removed` text must state: `GET /mcp` is retired and now answers `405 Method Not Allowed`
with `Allow: POST, DELETE`; the rmcp pre-session keep-alive SSE workaround is gone with it,
along with `PMCP_MAX_KEEPALIVE_STREAMS` and `PMCP_KEEPALIVE_MAX_SECONDS`; `/health` and
`/metrics` are unaffected; and the replacement is `subscriptions/listen`, which is reachable
**only** at protocol version `2026-07-28`. It must not claim any existing client loses
delivered data — pmcp never wrote to the GET stream (Context, "Retiring GET removes a channel
pmcp never writes to"). The version-string bump to `2.0.0` lands here rather than in the
release task so that `/health`'s `version` field, `pyproject.toml`, and the CHANGELOG heading
cannot disagree on a merged `main`. **This was raised with the operator as a droppable task
and the answer was to keep it** — see `## Execution Notes > Decision 4`. It is not optional.

### SL-2 — Listen handler registration and shutdown (roadmap Lane B)

- **Scope**: Own the bus, the sink, and the `ListenHandler` on `GatewayServer`; register
  `subscriptions/listen` through the P2 constructor form; close open streams on shutdown.
- **Owned files**: `src/pmcp/server.py`, `tests/mcp2x/test_listen_registration.py`
- **Interfaces provided**: IF-0-P3B-2
- **Interfaces consumed**: IF-0-P3B-1 (`BusCatalogEventSink`, `CatalogEventSink`)
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/mcp2x/test_listen_registration.py` | `GatewayServer(...)._create_server()` then `server.get_request_handler("subscriptions/listen")` is not `None` and its `params_type is SubscriptionsListenRequestParams`, and its `handler` **is** the `GatewayServer._listen_handler` instance (identity, not truthiness — a `getattr` default would pass a truthiness check forever, P1's false-green bug); `GatewayServer.__init__` built the bus and sink **before** `ClientManager`, asserted by `gw._client_manager` holding the same `CatalogEventSink` object as `gw._catalog_events` (identity); the full IF-0-P3B-4 duplex flow driven in-process over anyio memory streams via `Server.run` — ack first with `SUBSCRIPTION_ID_META_KEY == request id`, a `bus.publish(ToolsListChanged())` delivered, a `PromptsListChanged` **not** delivered to a tools-only filter, two concurrent subscriptions demultiplexed by distinct ids, `notifications/cancelled` ending one with **no** result frame and no later delivery, and `GatewayServer.shutdown()` emitting the `SubscriptionsListenResult` with `resultType == "complete"` as the final frame before the stream ends; a handshake-era `subscriptions/listen` (no modern `_meta`) returns `METHOD_NOT_FOUND`; `PMCP_MAX_LISTEN_STREAMS=1` makes a second concurrent listen fail before its ack. **Required test-function names, because the acceptance commands select on them with `-k` and a filter matching zero tests is not evidence**: `test_cancelled_notification_ends_subscription` and `test_shutdown_sends_listen_result_before_close` | `uv run pytest tests/mcp2x/test_listen_registration.py -q` |
| SL-2.2 | impl | SL-2.1 | `src/pmcp/server.py` | — | — |
| SL-2.3 | verify | SL-2.2 | `src/pmcp/server.py`, `tests/mcp2x/test_listen_registration.py` | all SL-2 tests | `uv run pytest tests/mcp2x/test_listen_registration.py -q && uv run ruff check src/pmcp/server.py && uv run mypy src/pmcp --exclude baml_client && ! rg -n 'add_request_handler' src/pmcp/` |

SL-2.2 implements IF-0-P3B-2 exactly. Three details that are easy to get wrong and are
therefore stated rather than left to judgement:

1. **`shutdown()` must call `close()` first.** `_run_stdio` (`server.py:836-865`) and `_run_http`
   (`:867-915`) both call `await self.shutdown()` from a `finally` that runs **after** the
   transport has already ended, so on a real process exit the graceful frame may not reach the
   wire. That is spec-legal — `SubscriptionsListenResult`'s own docstring says an abrupt
   transport close carries no response — and EC-P3B-2's server-close half is proven
   deterministically in-process by SL-2.1 instead. Putting `close()` first in `shutdown()` is
   still correct and is what makes the in-process proof meaningful; do **not** invent a
   signal handler or a uvicorn lifespan hook to chase a wire-level graceful drain, which is
   nondeterministic and which EC-P3B-2 does not require.
2. `ListenHandler.close()` is **sync** and only closes memory object streams owned by the
   handler task. It does not touch `ClientManager`, does not `gather`, and does not cross a
   cancel scope — so it does not widen the anyio debt (`## Execution Notes > Decision 3`).
3. `max_subscriptions` is read from `PMCP_MAX_LISTEN_STREAMS` with default `64`, reusing the
   `_env_int`-style bounds pattern. This is the deliberate replacement for the
   `PMCP_MAX_KEEPALIVE_STREAMS` DoS guard SL-4 deletes: the phase must not remove a
   concurrency cap on unauthenticated long-lived streams without putting one back.

### SL-3 — Production event publishers in ClientManager (roadmap Lane C)

- **Scope**: Wire the four catalog mutators to the frozen sink so that a real
  `connect_server` / `disconnect_server` / `refresh` publishes, and accept the sink through
  the constructor without breaking any existing construction site.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/mcp2x/test_catalog_publishers.py`
- **Interfaces provided**: `ClientManager(catalog_events=...)`
- **Interfaces consumed**: IF-0-P3B-1 (`CatalogEventSink`)
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-3.1 | test | — | `tests/mcp2x/test_catalog_publishers.py` | `ClientManager()` with **no** `catalog_events` constructs and every existing call still works (the null-sink default); `ClientManager(catalog_events=<recorder>)` then `_index_tools("s", [<one tool dict>])` records exactly one `note_tools_changed`, `_index_resources` one `note_resources_changed`, `_index_prompts` one `note_prompts_changed`; `_index_tools("s", [])` with nothing indexed records **nothing** (no spurious event for a no-op); `_remove_server_indexes("s")` after indexing all three kinds records all three, and `_remove_server_indexes("never-connected")` records **nothing**; and — the integration half — a real `ClientManager` wired to a real `BusCatalogEventSink` over an `InMemorySubscriptionBus` with a recording listener, driven through `connect_server(<the tests/runtime stdio fixture config>)`, publishes at least `ToolsListChanged`, and through `disconnect_server(...)` publishes `ToolsListChanged` again — **without any test calling `note_*` or `flush` itself** | `uv run pytest tests/mcp2x/test_catalog_publishers.py -q` |
| SL-3.2 | impl | SL-3.1 | `src/pmcp/client/manager.py` | — | — |
| SL-3.3 | verify | SL-3.2 | `src/pmcp/client/manager.py`, `tests/mcp2x/test_catalog_publishers.py` | all SL-3 tests | `uv run pytest tests/mcp2x/test_catalog_publishers.py -q && uv run ruff check src/pmcp/client/manager.py && uv run mypy src/pmcp --exclude baml_client && rg -n 'note_tools_changed|note_resources_changed|note_prompts_changed' src/pmcp/client/manager.py` |

SL-3.2's edits, precisely:

- `__init__` (`:491-519`) gains keyword-only `catalog_events: CatalogEventSink | None = None`
  and stores `self._catalog_events = catalog_events or _NullCatalogEventSink()`. The null sink
  is private to this module and its `note_*` are no-ops; `flush()` is a no-op coroutine. Every
  existing `ClientManager(...)` construction site — `server.py:142`, `cli.py`, and ~20 test
  modules — keeps working with zero edits, which is what keeps this lane's blast radius to
  one file.
- `_index_tools` (`:1106`), `_index_resources` (`:1153`), `_index_prompts` (`:1181`): each
  calls its `note_*` **once, at the end, only if it indexed at least one entry** (each already
  returns the count). A no-op index must not publish — a level trigger that fires on nothing
  trains clients to ignore it.
- `_remove_server_indexes` (`:927`): tracks whether it removed anything per kind (it already
  iterates each dict) and calls the matching `note_*` for each kind that actually shrank.
- `_index_capabilities` (`:1220-1248`) ends with `await self._catalog_events.flush()` and
  `disconnect_server` (`:778`) ends with the same, so a connect or disconnect delivers in one
  coalesced burst rather than in three scheduled drains. **These two calls are an ordering
  nicety, not the correctness mechanism** — IF-0-P3B-1's self-scheduling is. Deleting them
  must not make SL-3.1's integration assertions fail, and SL-3.1 is written so that it would
  not.
- The two `# TODO(P3B)` markers P2 left at the JSON-RPC error `code`/`data` discard sites
  (`_handle_stdout_line` and `_read_sse`; re-grep for the current line numbers, they have moved
  twice) are **retargeted to `# TODO(post-P3B)`, not actioned** — see
  `## Execution Notes > Decision 5`.

### SL-4 — GET retirement and stream-safe HTTP transport (roadmap Lane A)

- **Scope**: Retire `GET /mcp` and the rmcp keep-alive shim, keep `/health` and `/metrics`
  alive, and stop `request_timeout` from truncating listen streams.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/mcp2x/test_get_retirement.py`, `tests/mcp2x/test_listen_over_http.py`
- **Interfaces provided**: IF-0-P3B-3
- **Interfaces consumed**: IF-0-P3B-1 (via `pmcp.server` construction in the HTTP-level test only)
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-4.1 | test | — | `tests/mcp2x/test_get_retirement.py` | Against `create_http_app(<stub Server>)` driven by Starlette's `TestClient`: `GET /mcp` returns `405` and the `Allow` header contains `POST` and `DELETE` and **not** `GET`; the `/mcp` `Route.methods` set is exactly `{"POST", "DELETE"}` — **not** `{"POST", "DELETE", "HEAD"}`: Starlette synthesises `HEAD` only alongside `GET`, so once `GET` is removed `HEAD` goes with it, which the Starlette spike run this session confirmed (`Allow: POST, DELETE`, no `HEAD`). Asserting the exact set is what proves `GET` is gone rather than merely absent from a list; `GET /health` is `200` and its body still carries `ok`, `version`, `transport`, `gateway_diagnostics`; `GET /metrics` is `200` with the Prometheus content type; `app.state.gateway_diagnostics.session_compatibility` has no `pre_session_get` key and does have `get_stream == "retired"`; module-level `_keepalive_active`, `_DEFAULT_MAX_KEEPALIVE_STREAMS`, `_DEFAULT_KEEPALIVE_MAX_SECONDS` and `_KEEPALIVE_HEARTBEAT_SECONDS` no longer exist (`hasattr` is `False` on the module) | `uv run pytest tests/mcp2x/test_get_retirement.py -q` |
| SL-4.2 | test | SL-4.1 | `tests/mcp2x/test_listen_over_http.py` | Against pmcp's own `create_http_app` under uvicorn on `alloc_port()` (never `3344`), with a real `ListenHandler` + `InMemorySubscriptionBus` the test also holds a reference to: an IF-0-P3B-4 listen POST returns `200 text/event-stream` whose first `data:` frame is the ack; a `bus.publish(ToolsListChanged())` arrives as a stamped `notifications/tools/list_changed`; **the app is constructed with `request_timeout=3` and the stream is still alive and still delivering at t > 8s** — the regression test for measured spike 2, which fails on today's code; the same request with `Accept: application/json` only returns `406`; and closing the client stream ends the subscription server-side, asserted by `len(listen_handler._streams) == 0` within 2s of the close (EC-P3B-2's HTTP client-close half). **Required test-function names, selected by `-k` in the acceptance commands**: `test_timeout_exemption_keeps_stream_alive` and `test_client_close_ends_subscription` | `uv run pytest tests/mcp2x/test_listen_over_http.py -q` |
| SL-4.3 | impl | SL-4.2 | `src/pmcp/transport/http.py` | — | — |
| SL-4.4 | verify | SL-4.3 | `src/pmcp/transport/http.py`, `tests/mcp2x/test_get_retirement.py`, `tests/mcp2x/test_listen_over_http.py` | all SL-4 tests | `uv run pytest tests/mcp2x/test_get_retirement.py tests/mcp2x/test_listen_over_http.py -q && uv run ruff check src/pmcp/transport/http.py && uv run mypy src/pmcp --exclude baml_client && ! rg -n 'keepalive|KEEPALIVE|pre_session_get' src/pmcp/transport/http.py && ! rg -n 'methods=\["GET", "POST", "DELETE"\]' src/pmcp/transport/http.py` |

SL-4.4's two `! rg` checks are **paired greps, not standalone** — SL-4.1's `hasattr` and
`Route.methods` assertions are the test half, so renaming a symbol past the regex cannot make
this green. Two syntax traps are already resolved in the command above and must not be
"fixed" back: `rg` takes a Rust regex, so alternation is `keepalive|KEEPALIVE|pre_session_get`
with a **bare** `|` — an escaped `\|` is a literal pipe character and the negative grep
would then pass unconditionally, which is a false green. And the second grep targets the
`/mcp` route's exact `methods=` literal rather than a bare `"GET"`, because `/health`,
`/metrics` and the protected-resource-metadata route legitimately keep `methods=["GET"]` and a
bare `"GET"` grep would false-red forever.

SL-4.3 also updates the module docstring (`:1-11`), which currently sells "no persistent GET
/sse connection is required" as a *feature of streamable HTTP* — after this lane it is a hard
property of the endpoint and the docstring must say GET is not served and point at
`subscriptions/listen`. The `_metrics` counters (`:53-60`) are unchanged.

### SL-5 — Test repair and end-to-end subscription acceptance (roadmap Lane D)

- **Scope**: Repair the three pre-existing modules that assert the deleted GET behaviour,
  add the incremental listen-stream reader the runtime harness lacks, and build the
  EC-P3B-4 end-to-end acceptance that only a real catalog mutation can satisfy.
- **Owned files**: `tests/test_*.py`, `tests/conftest.py`, `tests/__init__.py`, `tests/fixtures/**`, `tests/runtime/**`
- **Interfaces provided**: `tests/runtime/harness.py` (`listen_stream` context manager)
- **Interfaces consumed**: IF-0-P3B-1 (sink), IF-0-P3B-2 (`ClientManager(catalog_events=)`, `GatewayServer._listen_handler`), IF-0-P3B-3 (route table, timeout exemption), IF-0-P3B-4 (wire literals)
- **Parallel-safe**: no (terminal integration lane)

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-5.1 | test | — | `tests/runtime/harness.py`, `tests/runtime/test_harness.py` | The new `listen_stream(base_url, *, notifications, timeout)` context manager: yields an object with `next_frame(timeout)` returning the next decoded `data:` payload (skipping `: ping` comment lines) and `close()`; asserted against the harness's own `gateway_on_spare_port` fixture by opening a subscription and receiving the ack. **Plus a new `booted_gateway(..., request_timeout: int | None = None)` keyword** that appends `--request-timeout <n>` to the boot command — the flag exists (`src/pmcp/cli.py:232`, also honoured as `PMCP_REQUEST_TIMEOUT` at `:2177-2179`) — asserted by a test that boots with `request_timeout=5` and greps the recorded `BootedGateway.command` for it. `alloc_port()` is reused, never a literal; the fixture never binds `3344`; `booted_gateway`'s existing no-live-gateway tolerance (`live_pid is None` proceeds) is preserved — **assert it, because a hard live-gateway requirement would fail this module on CI** | `uv run pytest tests/runtime/test_harness.py -q` |
| SL-5.2 | test | SL-5.1 | `tests/runtime/test_subscriptions_e2e.py` | **EC-P3B-4.** Against a `booted_gateway(request_timeout=5)` (spare port, isolated `HOME`/`XDG_CONFIG_HOME`/`--config`/`--project`/`--policy`/`--lock-dir`, cwd inside the throwaway dir): open a listen stream filtered to tools+prompts; then over the *same* deployed `/mcp`, `modern_post` a `tools/call` of `gateway.connect_server` for a second fixture downstream and assert a `notifications/tools/list_changed` arrives on the listen stream within 10s carrying the correct `subscriptionId`; then `gateway.disconnect_server` and assert another arrives; then `gateway.refresh` and assert another — **and the last of these must land more than 12s after the subscription was opened**, so the whole module doubles as the deployed-wire regression for the `request_timeout` truncation (it fails on today's code, where the stream dies at 5s). Assert throughout that **no** `notifications/resources/list_changed` frame ever arrives (it was not requested). No test in this module calls `note_*`, `flush`, or `bus.publish` — the mutation is the only trigger | `uv run pytest tests/runtime/test_subscriptions_e2e.py -q` |
| SL-5.3 | test | SL-5.1 | `tests/runtime/test_get_retired.py` | **EC-P3B-3 over the deployed wire.** Against the same booted gateway: `GET /mcp` (with and without `Accept: text/event-stream`, with and without an `mcp-session-id` header) returns `405` within 5s — the timeout is the assertion that it does not hang, which is what today's code does; the `Allow` response header contains `POST`; `GET /health` returns `200` with a JSON body whose `version` matches `pmcp.__version__`; `GET /metrics` returns `200` with `text/plain` and a `pmcp_requests_total` line; and a POST `tools/call` still succeeds on the same process, so the route change did not break the live method path | `uv run pytest tests/runtime/test_get_retired.py -q` |
| SL-5.4 | test | SL-5.1 | `tests/runtime/test_publisher_coverage.py` | The AST honesty guard: parse `src/pmcp/client/manager.py`, find every `ast.Assign`/`ast.AugAssign`/`ast.Subscript`-store and every `.pop(`/`.clear(` call whose target resolves to `self._tools`, `self._resources` or `self._prompts`, and assert the enclosing `FunctionDef` name is in `{"_index_tools", "_index_resources", "_index_prompts", "_remove_server_indexes"}` — and that each of those four bodies contains a call to a `note_*` attribute of `self._catalog_events`. A new mutation path added anywhere else fails this test with the name of the offending function. This is the mechanism that keeps EC-P3B-4 true after the phase, and it is modelled on P5's `test_credential_predicate_guard.py` | `uv run pytest tests/runtime/test_publisher_coverage.py -q` |
| SL-5.5 | impl | SL-5.4 | `tests/test_http_dos.py`, `tests/test_http_transport.py`, `tests/test_transport_http.py` | — | — |
| SL-5.6 | impl | SL-5.5 | `tests/test_*.py`, `tests/conftest.py`, `tests/__init__.py`, `tests/fixtures/**` | — | — |
| SL-5.7 | verify | SL-5.6 | `tests/**` | full suite | `uv run pytest tests/ -q`, then `uv run pytest tests/runtime/ -q -rs > /tmp/p3brt.txt; ! grep -qE '^SKIPPED' /tmp/p3brt.txt`, then `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/pmcp --exclude baml_client` |

SL-5.5 repairs the three modules located this session rather than discovered by running:
`tests/test_http_dos.py:56-88` (the keep-alive concurrency cap and deadline tests) is
**re-pointed at `PMCP_MAX_LISTEN_STREAMS`** rather than deleted — the DoS property being
asserted (an unauthenticated client cannot open unbounded long-lived streams) survives the
phase, only its mechanism changes, and deleting the module would quietly drop a security
assertion. `tests/test_http_transport.py:225-250` asserts `/mcp` accepts GET and that
`"GET" in route.methods`; both invert. `tests/test_transport_http.py:212` skips a case with
the comment "GET with no session ID returns keep-alive SSE"; the case now asserts `405`.
SL-5.6 is the sweep for anything else the three impl lanes broke; if it finds nothing beyond
those three modules it records "no further repairs needed" in its commit message rather than
inventing a diff.

**Every fixture in this lane uses `pytest.fail`, never `pytest.skip`.** A skipped test exits
`0`, so for an acceptance gate a skip *is* a silent pass — the exact false-green this repo has
been burned by. SL-5.7's `-rs` gate trips on any `SKIPPED` line in `tests/runtime/`.

**Everything in `tests/runtime/` must pass on a CI runner with no live gateway.**
`tests/runtime/` is selected by path, runs unmarked in the default `uv run pytest tests/` on
every matrix leg, and GitHub runners carry no pmcp systemd unit. `booted_gateway()`'s existing
`live_pid is None` tolerance is preserved and SL-5.1 asserts it. No new module may
hard-require `:3344`.

### SL-docs — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, update cross-cutting documentation touched or
  invalidated by this phase's impl lanes, and append post-execution amendments to the phase
  specs whose interface freezes turned out wrong.
- **Owned files**: `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SPEC_COMPLIANCE.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `MIGRATION.md`, `ARCHITECTURE.md`, `llm.txt`, `llms.txt`, `llms-full.txt`, `docs/**`, `rfcs/**`, `adrs/**`, `.claude/docs-catalog.json`, `specs/phase-plans-v11.md`, `plans/phase-plan-v11-P2.md`, `plans/phase-plan-v11-P3B.md`
- **Interfaces provided**: (none)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-1, SL-2, SL-3, SL-4, SL-5

**`CHANGELOG.md` is deliberately excluded from this lane's owned files** — SL-1 is its single
writer, exactly as in P2, because two lanes cannot share the release block. This overrides the
docs-sweep template's default list for this phase only. `docs/`, `rfcs/` and `adrs/` do not
exist in this repo today; they are listed so that a file created by an impl lane lands in this
lane's ownership rather than nowhere.

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | — | `.claude/docs-catalog.json` | Rescan: `python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/scaffold_docs_catalog.py" --rescan`. Preserves `touched_by_phases`. If the helper is absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and proceed. |
| SL-docs.2 | docs | SL-docs.1 | per catalog | For each catalogued file, decide whether this phase changes it; if yes update it and append `P3B` to `touched_by_phases`. `README.md` and `SPEC_COMPLIANCE.md` are known-yes: README's HTTP-transport section must stop implying GET is served, and SPEC_COMPLIANCE.md must record `subscriptions/listen` as implemented at `2026-07-28` and the GET stream as retired. Record intentionally-skipped files in the commit message. |
| SL-docs.3 | docs | SL-docs.2 | `specs/phase-plans-v11.md`, `plans/phase-plan-v11-P2.md` | Append `### Post-execution amendments` to the Phase 3B section recording, with citations: (a) **EC-P3B-1's "IF-0-P2-2 shape" was stale twice** — the roadmap's `:100` prescribes `add_request_handler`, which P2's Decision 1 rejected, and the ID collides with P2's plan-level IF-0-P2-2 (the downstream transport contract); P3B registered through `Server.__init__(on_subscriptions_listen=...)` and the roadmap line should be corrected to name IF-0-P2-1. (b) **`request_timeout` truncated every HTTP listen stream** at 60s — measured, not inferred — which the roadmap's Lane A scope ("GET retirement, route table, protocol-version header") did not cover. (c) `client/manager.py:1099` → `:1106`. (d) `gateway.refresh` does not call `ClientManager.refresh`, so a flush-call-site enumeration would have missed it; the sink self-drains instead and `test_publisher_coverage.py` is the honesty guard. (e) The SDK ships `ListenHandler`/`SubscriptionBus`, so the phase wrote no listen state machine — the roadmap's framing implies otherwise. (f) The anyio `disconnect_all` debt (Phase 2 amendment 8) was **deferred again**, with the reasons in `## Execution Notes > Decision 3`. (g) The release deviation from the roadmap's "P2 is a minor" cadence (Decision 4). |
| SL-docs.4 | verify | SL-docs.3 | — | Run any repo doc linters (`markdownlint`, `vale`, `prettier --check`, Mermaid/PlantUML render check). If none configured, no-op. |

## Execution Notes

- **Decision 1 — register through `Server.__init__(on_subscriptions_listen=...)`, not
  `add_request_handler`.** EC-P3B-1 says "through the IF-0-P2-2 shape". The roadmap's
  IF-0-P2-2 (`specs/phase-plans-v11.md:100`) names `add_request_handler`; P2's executed plan
  named the constructor form as IF-0-P2-1 and explicitly recorded "no `add_request_handler`
  call is added" (`plans/phase-plan-v11-P2.md:320`, Decision 1 at `:648-666`, confirmed by the
  roadmap's own Phase 2 post-execution amendment item 6). Following the roadmap's literal text
  would contradict the interface P2 actually froze and shipped, would put the only
  `add_request_handler` call in the codebase next to six constructor registrations, and would
  erase mypy's per-handler result-type check. The constructor kwarg's declared type is exactly
  `ListenHandler.__call__`'s signature. This plan follows P2's frozen mechanism and reports the
  roadmap drift rather than silently weakening or silently diverging.
- **Decision 2 — the sink self-drains; flush call sites are not the correctness mechanism.**
  The roadmap's own scope note is right that "a listener with no publishers is the failure
  mode", but the obvious fix — publish from every async lifecycle entry point — is the P5
  shape: nine entry points (`connect_all`, `ensure_connected`, `connect_server`,
  `disconnect_server`, `restart_server`, `_reconnect_loop`, `disconnect_all`, `refresh`,
  `adopt_process`), where patching eight of nine looks exactly like patching the class, and
  where `gateway.refresh` — which routes through `disconnect_server` + `connect_all` rather
  than `ClientManager.refresh` — is precisely the one an enumeration written from the roadmap
  would miss. Instead, the four sync mutators call `note_*`, the sink schedules its own drain,
  and `tests/runtime/test_publisher_coverage.py` fails if a write to `_tools`/`_resources`/
  `_prompts` ever appears outside those four. Coverage becomes structural rather than
  enumerated.
- **Decision 3 — the anyio `disconnect_all` debt is DEFERRED again, deliberately.** The
  roadmap points at P3B as where this gets addressed; this plan declines, with reasons rather
  than silence. (1) **No production exposure.** The reproduction (Phase 2 amendment 8, recipe
  in `tests/runtime/test_downstream_remote.py`'s module docstring) requires a fully
  connected-then-torn-down streamable-HTTP client sharing one event loop with another
  anyio-driven server — a test's own uvicorn fixture. The systemd deployment owns its loop;
  nothing else drives anyio in it. (2) **A real fix is a concurrency refactor** of
  `ClientManager`'s per-client task ownership, which is precisely the kind of change the
  roadmap's own scope note forbids in this phase ("Highest-risk phase in the roadmap … Ship it
  alone"). Landing a cancel-scope restructuring in the same PR as the only client-visible
  behaviour change in the migration maximises the blast radius of both. (3) **The workaround
  holds.** P2's `test_downstream_remote.py` shares one `run_fake_remote` lifecycle and uses
  per-name `disconnect_server()`; SL-5's new runtime modules follow the same pattern and none
  of them needs `disconnect_all()`. (4) **This phase does not widen it.** The only shutdown
  code P3B adds is `ListenHandler.close()`, which is sync, closes only memory object streams
  owned by their own handler tasks, and never crosses a cancel scope. Recommendation carried
  forward: give it its own bounded phase after the 2.0.0 release, where a `TaskGroup`-per-client
  restructuring can be verified against the existing runtime suite in isolation.
- **Decision 4 — one `2.0.0` covering P2 and P3B, deviating from the roadmap's cadence.**
  The roadmap treats P2 as a minor and P3B as the major. The operator has decided to ship a
  single `2.0.0` carrying both rather than cutting an interim `1.23.0`. Recorded as a
  deviation because it is one: P2's work is currently unreleased in `## [Unreleased]`, and
  folding it into the major means the mcp-2.x port never ships under its own version number.
  The justification is that P2 and P3B are one migration — P2 raised the SDK floor to `2.0.0`
  and P3B is the only part of it that clients can observe — and that shipping a minor whose
  entire content is "the SDK underneath changed" invites operators to take it and then take
  the major separately, doubling the upgrade events for one migration. SL-1.3 is the single
  writer; the GET retirement leads the entry because it is the breaking change and a reader
  scanning a major's notes must hit it first. **`pyproject.toml` and `src/pmcp/__init__.py`
  are bumped in the same task**, so `/health`'s reported version cannot disagree with the
  CHANGELOG heading on a merged `main`. **Raised with the operator as the one droppable task
  in the phase and explicitly decided: keep it, do not defer to tag time.** The reason given,
  recorded here because it is not obvious: P1's drift test pins `pyproject.toml` against
  `src/pmcp/__init__.py` only, so a CHANGELOG that says `[2.0.0]` while both files still say
  `1.22.0` would leave `main` self-inconsistent at every commit until release with **nothing
  in CI flagging it**, and `/health` would report a version its own changelog contradicts.
  Bumping here keeps `main` coherent at all times and reduces the release task to
  tag-and-verify. A lane must not "simplify" this away.
- **Decision 5 — the P2-deferred error `code`/`data` discard is NOT actioned here.** P2's SL-3
  left `# TODO(P3B)` markers at the two sites that collapse a JSON-RPC error to
  `Exception(payload["error"].get("message", "Unknown error"))` (`_handle_stdout_line` and
  `_read_sse` in `client/manager.py`; re-grep for the line numbers, they have moved twice).
  No P3B exit criterion mentions typed downstream errors, and P3B is the phase that changes
  observable client behaviour — changing downstream error semantics inside it would put two
  unrelated behaviour changes in one breaking release. SL-3.2 retargets the markers to
  `# TODO(post-P3B)` so they do not read as owed-by-this-phase, and SL-docs.3 records the
  second deferral. Naming a phase in a TODO that then does not do it is how debt becomes
  invisible.
- **The lane partition was verified with `phase_loop_runtime.lane_scheduler._patterns_overlap`,
  not by eye.** All 6 lanes × every glob pair (15 lane pairs) were passed through that function
  from the system `python3` this session; the result was `NONE`. The globs matter because
  `_patterns_overlap` compares with `fnmatchcase`, whose `*` crosses `/` — the trap that made
  P2's first draft wrong. Confirmations recorded so a future edit re-checks rather than
  reasons: `_patterns_overlap('tests/*.py', 'tests/mcp2x/test_listen_registration.py')` is
  **`True`** (which is why no lane uses `tests/*.py`);
  `_patterns_overlap('tests/test_*.py', 'tests/mcp2x/test_get_retirement.py')` is `False`;
  `_patterns_overlap('tests/test_*.py', 'tests/conftest.py')` is `False` and
  `_patterns_overlap('tests/test_*.py', 'tests/__init__.py')` is `False` — which is why SL-5
  lists `tests/conftest.py` and `tests/__init__.py` explicitly or they would be silently
  unowned; `_patterns_overlap('tests/runtime/**', 'tests/mcp2x/test_listen_registration.py')`
  is `False`; `_patterns_overlap('plans/phase-plan-v11-P2.md', 'plans/phase-plan-v11-P3B.md')`
  is `False`. Any future edit to these globs must be re-checked with that function.
- **Single-writer files**: `CHANGELOG.md`, `pyproject.toml`, `src/pmcp/__init__.py`,
  `src/pmcp/subscriptions.py` — SL-1 only. `src/pmcp/server.py` — SL-2 only.
  `src/pmcp/client/manager.py` — SL-3 only. `src/pmcp/transport/http.py` — SL-4 only. Every
  pre-existing test file, plus `tests/runtime/**` — SL-5 only; SL-1/2/3/4 put their new unit
  tests under distinct `tests/mcp2x/test_*.py` filenames. `.claude/docs-catalog.json` and
  `specs/phase-plans-v11.md` — SL-6 only.
- **Cross-phase file collision.** `CHANGELOG.md` and `.claude/docs-catalog.json` are written by
  every concurrent phase in this repo and are not lane-partitionable. Writes serialize on merge
  order; whichever phase merges second rebases and re-applies rather than resolving in place.
  The orchestrator should expect an add/add or content conflict on exactly these two paths
  across phases and must not read it as a stale-base signal. This phase additionally rewrites
  the `## [Unreleased]` heading itself, so a concurrent phase that appended to that block will
  conflict there specifically — resolve by keeping P3B's `[2.0.0]` heading and moving the
  other phase's entry into the fresh `## [Unreleased]` above it.
- **Known destructive changes**: `src/pmcp/transport/http.py` — SL-4 deletes the pre-session
  keep-alive branch (`:593-653`), the module constants `_DEFAULT_MAX_KEEPALIVE_STREAMS` (`:78`),
  `_DEFAULT_KEEPALIVE_MAX_SECONDS` (`:79`), `_KEEPALIVE_HEARTBEAT_SECONDS` (`:80`) and
  `_keepalive_active` (`:86`), `"GET"` from the `/mcp` route's `methods`, and the
  `pre_session_get` diagnostics key. `CHANGELOG.md` — SL-1 renames the `## [Unreleased]`
  heading (its content is preserved under `## [2.0.0]`, not deleted). `tests/test_http_dos.py`,
  `tests/test_http_transport.py`, `tests/test_transport_http.py` — SL-5 inverts three
  assertions and re-points two DoS tests at `PMCP_MAX_LISTEN_STREAMS`; **no test module is
  deleted**, because deleting the DoS module would silently drop a security assertion. No file
  is deleted anywhere in this phase.
- **Expected add/add conflicts**: none. SL-1 creates `src/pmcp/subscriptions.py` and no other
  lane writes it. `tests/mcp2x/` already exists from P2 and the four new files in it have
  distinct names.
- **SL-0 re-exports**: not applicable — SL-1 adds a new module but no `__init__.py` symbol.
  `src/pmcp/__init__.py` is touched only for the version string, and consumers import
  `pmcp.subscriptions` directly, so there is no eager re-export to break.
- **A skipped test is a failed test.** Every acceptance fixture uses `pytest.fail`, never
  `pytest.skip`; SL-5.7 runs `uv run pytest tests/runtime/ -q -rs` and fails on any `SKIPPED`
  line. `uv sync --all-extras` (never bare `uv sync`, which prunes `pytest` and lets a system
  `pytest` report a misleading pass) is a precondition of every command in this plan.
- **`getattr(obj, "camelCaseName", default)` is banned in this phase's tests.** It returns the
  default forever after a rename and generates false greens; the P1 CI probe had this bug
  twice. SL-2.1's registration assertions are **identity** checks against the handler object,
  and IF-0-P3B-4's `_meta` key is imported as `SUBSCRIPTION_ID_META_KEY` rather than typed as a
  string literal.
- **`/health` is not acceptance.** `transport/http.py:432-441` returns `"ok": True` as a
  hardcoded literal computed from nothing, and imports are not acceptance for a gateway. It is
  used in this plan only as a boot-readiness poll and, in SL-5.3, as a *regression* check that
  the route still exists after GET retirement — never as evidence that a subscription works.
  Every acceptance item below terminates in a real request in / validated typed frame out.
- **Runtime-step safety is a PROCEDURE, no pid is hardcoded.** A live gateway serves real
  traffic on `127.0.0.1:3344` as a systemd **user** unit (currently 1.22.0). Every runtime step
  must: resolve the pid from the socket with `ss -ltnpH 'sport = :3344'` (regex `pid=(\d+)`) and
  **never** `pgrep -f`, which self-matches the invoking shell; snapshot `pgrep -P <pid>`
  children before and assert the same set after; assert `/health` OK before and after; and
  never bind `3344`. Ports `38344`/`38345` are claimed by `tests/test_credential_boot.py` and
  its P6CLEAN sibling — reuse `tests/runtime/harness.py`'s `alloc_port()` (`:87-95`) rather
  than picking a number. `booted_gateway()` already implements this comparison and treats an
  absent live gateway as "nothing to protect, proceed", which is what makes the module CI-safe.
- **Never isolate by moving or deleting files outside the worktree.** For any "no overlay"
  condition, change where the code looks (`HOME=/tmp/… XDG_CONFIG_HOME=/tmp/…`). Boot isolation
  needs all six controls together — `--config`, `--project`, `--policy`, `--lock-dir`, redirected
  `HOME`, redirected `XDG_CONFIG_HOME` — and a cwd inside the throwaway directory. No lane and
  no verification step touches anything under the operator's real `$HOME` or `~/.pmcp`.
- **Intra-phase CI-red window is expected.** SL-1's day-1 freeze publishes
  `src/pmcp/subscriptions.py` and the `2.0.0` version strings; between SL-1 merging into the
  phase integration branch and SL-4/SL-5 landing, that branch's `test` job is red because
  `tests/test_http_transport.py` still asserts GET is served. That is the freeze working.
  **`main` only ever sees the combined, green PR** — no lane merges to `main` alone.
- **Stale-base guidance** (copy verbatim): Lane teammates working in isolated worktrees do not
  see sibling-lane merges automatically. If a lane finds its worktree base is pre-SL-1, it MUST
  stop and report rather than committing — the orchestrator will re-spawn or rebase. Silent
  `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree produces commits that
  destroy peer-lane work on `--no-ff` merge. SL-5 additionally must not start before SL-2,
  SL-3 and SL-4 have merged, since its acceptance tests exercise all three lanes' symbols at
  once.
- **Worktree naming**: `claude-execute-phase` allocates unique worktree names via
  `scripts/allocate_worktree_name.sh`. On this host, worktrees belong under
  `/mnt/workspace/worktrees/`.
- **Plan-level validation, since this repo has no `scripts/validate_plan_doc.py`.** `scripts/`
  contains only `pipeline-bootstrap`. The checks are `phase-loop validate-roadmap --repo .
  --roadmap specs/phase-plans-v11.md`, `phase_loop_runtime.planner_validation.validate_plan_dispatch_hints`
  against this file, and the Lane validation checklist walked by hand. `phase_loop_runtime` is
  importable from the **system** `python3` and from `phase-loop` on `PATH`, but **not** from
  this worktree's `.venv` — a venv-scoped import check reports it absent and is the wrong
  check. There is no `validate-plan` subcommand.

## Execution Policy
- execute: effort=medium
- repair: effort=medium
- SL-1: effort=low, reason=one small module plus a changelog promotion and two version strings
- SL-2: effort=medium, reason=the SDK supplies the handler so this is wiring but the construction order is load-bearing
- SL-3: effort=high, reason=a missed mutation path fails silently as a listener with no publisher
- SL-4: effort=high, reason=deleting a DoS guard and exempting one method from the timeout wrapper both regress silently rather than failing loudly
- SL-5: effort=high, reason=the end-to-end listen-stream acceptance is greenfield and a synthetic publisher call is disqualified as evidence
- SL-6: effort=low, reason=docs sweep plus roadmap amendments

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `src/pmcp/transport/http.py`, `src/pmcp/server.py`, `CHANGELOG.md`
- evidence paths: `plans/phase-plan-v11-P3B.md`, `specs/phase-plans-v11.md`, `SPEC_COMPLIANCE.md`
- redaction posture: `metadata_only`
- downstream handling: roadmap amendment recording that EC-P3B-1's `IF-0-P2-2` reference is stale twice over (ID collision with P2's plan-level IF-0-P2-2, and a mechanism P2's Decision 1 rejected), the unnamed `request_timeout` truncation of every HTTP listen stream, the corrected `client/manager.py:1106`, `gateway.refresh` not routing through `ClientManager.refresh`, the SDK already shipping `ListenHandler`/`SubscriptionBus`, the second deferral of the anyio `disconnect_all` debt with reasons, and the single-`2.0.0` release deviation from the roadmap's stated P2-is-a-minor cadence.

## Acceptance Criteria

- [ ] EC-P3B-1 — proven by `uv run pytest tests/mcp2x/test_listen_registration.py -q`
      (registration identity against `GatewayServer._listen_handler`, `params_type is
      SubscriptionsListenRequestParams`, ack-first with `SUBSCRIPTION_ID_META_KEY` equal to the
      request id, an unrequested notification kind never delivered, and two concurrent
      subscriptions demultiplexed by distinct ids) and by V4a, which opens a real subscription
      over a booted gateway's `/mcp` from the shell and asserts the first frame is
      `notifications/subscriptions/acknowledged` carrying the id. Paired grep, not standalone:
      `! rg -n 'add_request_handler' src/pmcp/` — its test half is the registration-identity
      assertion above.
- [ ] EC-P3B-2 — proven by `uv run pytest tests/mcp2x/test_listen_registration.py -q -k
      "cancelled_notification or shutdown_sends_listen_result"` for the two deterministic
      halves (a `notifications/cancelled`
      carrying the listen request id ends the subscription with **no** result frame and no
      later delivery; `GatewayServer.shutdown()` emits `SubscriptionsListenResult` with
      `resultType == "complete"` as the final frame before the stream ends) and by
      `uv run pytest tests/mcp2x/test_listen_over_http.py -q -k client_close` for the HTTP
      half (closing the client stream drops the server-side subscription within 2s, asserted
      on the handler's own stream set).
- [ ] EC-P3B-3 — proven by `uv run pytest tests/runtime/test_get_retired.py -q`: against a
      booted gateway on a spare port, `GET /mcp` returns `405` with `Allow` containing `POST`
      **within a 5s bound** — the bound is the assertion that it no longer hangs, which is what
      today's code does (measured spike 2b) — while `GET /health` returns `200` with a
      `version` matching `pmcp.__version__` and `GET /metrics` returns `200` with a
      `pmcp_requests_total` line; and by V2/V3, which reproduce all three from the shell with
      `curl --max-time`. Unit-level route-table coverage is
      `uv run pytest tests/mcp2x/test_get_retirement.py -q`.
- [ ] EC-P3B-4 — proven by `uv run pytest tests/runtime/test_subscriptions_e2e.py -q`: with a
      subscription open on a booted gateway's `/mcp`, a modern-envelope `tools/call` of
      `gateway.connect_server`, then `gateway.disconnect_server`, then `gateway.refresh`, each
      delivers a `notifications/tools/list_changed` on that stream stamped with the correct
      `subscriptionId`, while a `notifications/resources/list_changed` never arrives because it
      was not requested. No test in that module calls `note_*`, `flush`, or `bus.publish` — the
      catalog mutation is the only trigger, which is what the criterion requires. Kept honest
      after the phase by `uv run pytest tests/runtime/test_publisher_coverage.py -q`, whose AST
      guard fails if a write to `_tools`/`_resources`/`_prompts` appears outside the four
      mutators that publish. **And the stream must be proven un-truncated, at two levels**:
      SL-5.2 boots via `booted_gateway(request_timeout=5)` (the flag exists at
      `src/pmcp/cli.py:232`) and lands its last mutation-driven notification more than 12s
      into the subscription — that is V4b; and `uv run pytest
      tests/mcp2x/test_listen_over_http.py -q -k timeout_exemption` is the direct in-process
      regression for measured spike 2 (`request_timeout=3`, stream alive and delivering at
      t > 8s). Both fail on today's code.
- [ ] EC-P3B-5 — proven by V1 (boot on a spare port, `Gateway initialized: <n>/1 servers
      online` in the log, no `Fatal error` line) plus `uv run pytest tests/runtime/
      test_wire_handshake_era.py tests/runtime/test_wire_modern_era.py -q` unchanged from P2
      (a downstream tool call still succeeds and a client with no subscription is unaffected),
      and by V5: `uv run pytest tests/ -q`, `uv run ruff check src/ tests/`,
      `uv run ruff format --check src/ tests/` and `uv run mypy src/pmcp --exclude baml_client`
      all green.
- [ ] EC-P3B-6 — proven by V6: `rg -n '^## \[2\.0\.0\]' CHANGELOG.md` matching, the first
      `###` block under it being `### Removed`, `rg -n '405' CHANGELOG.md` and
      `rg -n 'subscriptions/listen' CHANGELOG.md` both matching inside that block, and
      `rg -n '^version = "2\.0\.0"' pyproject.toml` plus
      `rg -n '^__version__ = "2\.0\.0"' src/pmcp/__init__.py` agreeing with it. Test half (so
      the greps are paired, not standalone): `tests/mcp2x/test_subscription_contract.py`
      locates the `## [2.0.0]` block **wherever it sits in the file** and asserts that
      *that block's* first subsection is `### Removed` and mentions both `GET` and `405`.
      Deliberately not "the topmost released heading": that phrasing would break on the first
      post-2.0.0 release and force a future PR to edit a P3B acceptance test to go green.

## Verification

Run from the merged branch. `/tmp/p3b*` are throwaway paths. **No step binds `3344`**; the
fixture port is allocated, never a literal. Read `## Execution Notes > Runtime-step safety`
first.

```bash
# V0 — dependencies present (never bare `uv sync`)
uv sync --all-extras

# V0a — record the live gateway pid; V0b re-asserts it. An empty value on both
#       sides is a passing comparison, not an abort.
LIVE_PID=$(ss -ltnpH 'sport = :3344' 2>/dev/null | grep -o 'pid=[0-9]*' | head -1)
echo "live gateway before: $LIVE_PID"

# V1 — EC-P3B-5: boot an isolated gateway on an allocated spare port.
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
test "$PORT" != "3344"
rm -rf /tmp/p3bhome && mkdir -p /tmp/p3bhome/lock /tmp/p3bhome/xdg
REPO_ROOT=$(git rev-parse --show-toplevel)
PMCP_BIN="$REPO_ROOT/.venv/bin/pmcp"
# The stdio downstream is the harness's own `RT_FIXTURE_SRC` (a real 2.0.0
# `MCPServer`), imported rather than re-typed so there is one source of truth.
uv run python - "$REPO_ROOT" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from tests.runtime.harness import RT_FIXTURE_SRC
root = pathlib.Path(sys.argv[1])
pathlib.Path("/tmp/p3bhome/rt_fixture.py").write_text(RT_FIXTURE_SRC)
pathlib.Path("/tmp/p3bhome/mcp.json").write_text(json.dumps({"mcpServers": {"rt-fixture": {
    "command": str(root / ".venv" / "bin" / "python"),
    "args": ["/tmp/p3bhome/rt_fixture.py"]}}}))
PY
printf 'servers:\n  allowlist: ["rt-fixture"]\n' > /tmp/p3bhome/policy.yaml
( cd /tmp/p3bhome && HOME=/tmp/p3bhome XDG_CONFIG_HOME=/tmp/p3bhome/xdg \
    "$PMCP_BIN" --transport http --host 127.0.0.1 --port "$PORT" \
    --config /tmp/p3bhome/mcp.json --project /tmp/p3bhome \
    --policy /tmp/p3bhome/policy.yaml --lock-dir /tmp/p3bhome/lock -l info \
    > /tmp/p3bboot.log 2>&1 & echo $! > /tmp/p3bhome/pid )
for _ in $(seq 1 30); do curl -sf --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
grep -E 'Gateway initialized: [0-9]+/1 servers online' /tmp/p3bboot.log
! grep -q 'Fatal error' /tmp/p3bboot.log

# V2 — EC-P3B-3: the retired GET answers 405 promptly and does NOT hang.
#      --max-time 5 is the assertion: today's code holds this open indefinitely.
GET_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
  -H 'Accept: text/event-stream' -H 'MCP-Protocol-Version: 2026-07-28' \
  "http://127.0.0.1:$PORT/mcp")
test "$GET_CODE" = "405"
curl -sS -D- -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/mcp" | grep -i '^allow:.*POST'

# V3 — EC-P3B-3: /health and /metrics survive the retirement.
curl -sf --max-time 5 "http://127.0.0.1:$PORT/health" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); assert d['ok'] is True, d; assert d['transport']=='http', d"
curl -sf --max-time 5 "http://127.0.0.1:$PORT/metrics" | grep -q '^pmcp_requests_total'

# V4a — EC-P3B-1: open a real subscription and read the ack frame.
cat > /tmp/p3blisten.py <<'PY'
import json, sys, time, httpx
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
base, want_kind, budget = sys.argv[1], sys.argv[2], float(sys.argv[3])
body = {"jsonrpc": "2.0", "id": 77, "method": "subscriptions/listen",
        "params": {"notifications": {"toolsListChanged": True},
                   "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                             "io.modelcontextprotocol/clientCapabilities": {}}}}
headers = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream",
           "MCP-Protocol-Version": "2026-07-28",
           "Mcp-Method": "subscriptions/listen"}
seen, deadline = [], time.monotonic() + budget
with httpx.stream("POST", f"{base}/mcp", json=body, headers=headers, timeout=budget + 5) as r:
    assert r.status_code == 200, r.status_code
    assert "text/event-stream" in r.headers["content-type"], r.headers["content-type"]
    for line in r.iter_lines():
        if line.startswith("data:"):
            msg = json.loads(line[5:].strip())
            assert msg["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 77, msg
            seen.append(msg.get("method"))
            if msg.get("method") == want_kind:
                break
        if time.monotonic() > deadline:
            break
print(json.dumps(seen))
assert seen and seen[0] == "notifications/subscriptions/acknowledged", seen
assert want_kind in seen, (want_kind, seen)
PY
uv run python /tmp/p3blisten.py "http://127.0.0.1:$PORT" \
  notifications/subscriptions/acknowledged 5

kill "$(cat /tmp/p3bhome/pid)" 2>/dev/null || true

# V4b — EC-P3B-4 + the timeout exemption: a gateway booted with a 5s request
#       timeout must still deliver a mutation-driven notification 12s into the
#       subscription. Fails on today's code (measured: stream dies at exactly
#       request_timeout). Driven by the pytest module because it needs to issue
#       a gateway.connect_server on the same wire while holding the stream open.
uv run pytest tests/runtime/test_subscriptions_e2e.py -q

# V0b — the live gateway is untouched
test "$(ss -ltnpH 'sport = :3344' 2>/dev/null | grep -o 'pid=[0-9]*' | head -1)" = "$LIVE_PID"

# V5 — EC-P3B-5: full suite, lint, types. A skip in tests/runtime/ is a failure.
uv run pytest tests/ -q
uv run pytest tests/runtime/ -q -rs > /tmp/p3brt.txt; ! grep -qE '^SKIPPED' /tmp/p3brt.txt
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client

# V6 — EC-P3B-6: the release entry leads with the breaking change and the
#      version strings agree with it. Paired with the CHANGELOG-parsing test in
#      tests/mcp2x/test_subscription_contract.py, so a grep-only rename cannot
#      make this green.
rg -n '^## \[2\.0\.0\]' CHANGELOG.md
rg -n '^version = "2\.0\.0"' pyproject.toml
rg -n '^__version__ = "2\.0\.0"' src/pmcp/__init__.py
python3 - <<'PY'
import pathlib, re
text = pathlib.Path("CHANGELOG.md").read_text()
body = text.split("## [2.0.0]", 1)[1].split("\n## [", 1)[0]
first = re.search(r"^### (\w+)", body, re.M).group(1)
assert first == "Removed", f"first subsection of [2.0.0] is {first!r}, expected 'Removed'"
head = body.split("### ", 2)[1]
assert "405" in head and "GET" in head, head
assert "subscriptions/listen" in body, "the replacement must be named"
print("CHANGELOG OK")
PY

# V7 — paired greps (each has a test half; never run alone as evidence)
! rg -n 'add_request_handler' src/pmcp/
! rg -n 'keepalive|KEEPALIVE|pre_session_get' src/pmcp/transport/http.py
! rg -n 'methods=\["GET", "POST", "DELETE"\]' src/pmcp/transport/http.py
rg -n 'note_tools_changed|note_resources_changed|note_prompts_changed' src/pmcp/client/manager.py
rg -n 'on_subscriptions_listen' src/pmcp/server.py
```
