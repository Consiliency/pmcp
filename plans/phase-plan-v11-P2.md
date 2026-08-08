---
phase_loop_plan_version: 1
phase: P2
roadmap: specs/phase-plans-v11.md
roadmap_sha256: d782a5a408ecfb6e749333986d8a06ba6921be50ebd94ad5a4082537f6998289
---

# P2: Gateway runtime parity on mcp 2.x

## Context

Every fact below was read this session from source: the worktree at `main` `51b9806`
(v1.22.0), the unpacked `mcp` 2.0.0 wheel, the unpacked `mcp_types` 2.0.0 wheel, the
unpacked `httpx2` 2.9.1 wheel, and `mcp` 1.25.0 as installed in this worktree's venv.
Line citations are to files read this session; where the roadmap named a line that has
moved, the corrected number is given.

### The two eras are the design, and the roadmap is right about them

`mcp_types/version.py` (in the `mcp-types` 2.0.0 distribution, mirrored at
`mcp/types/version.py`) declares exactly what the roadmap says:
`HANDSHAKE_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")`,
`MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)`, `LATEST_HANDSHAKE_VERSION` = the newest
revision "reachable via the `initialize` handshake", `LATEST_MODERN_VERSION` = the
"`server/discover` probe default". `2026-07-28` is unreachable through `initialize` by
construction. **`PREFERRED_PROTOCOL_VERSION` (`client/manager.py:187`, currently
`"2025-11-25"`) governs the DOWNSTREAM handshake ladder and its ceiling is correct — no
lane raises it.** Upstream (what pmcp serves to clients) and downstream (what pmcp
speaks to servers) are different eras and are owned by different lanes here.

### Trap 1 — the six handlers are a rewrite, not a re-registration

`server.py:174` builds `Server("mcp-gateway", instructions=...)`; `server.py:198`
`_setup_handlers()` registers six closures with the 1.x decorators, at exactly the lines
the roadmap names: `:203` `list_tools`, `:215` `call_tool`, `:369` `list_resources`,
`:407` `read_resource`, `:466` `list_prompts`, `:495` `get_prompt`. Today's bodies take
zero or domain arguments and return bare lists (`list[Tool]`, `list[TextContent]`,
`list[Resource]`, `list[TextResourceContents]`, `list[Prompt]`) or a bare
`GetPromptResult`.

In `mcp` 2.0.0 the decorators are gone. `mcp/server/lowlevel/server.py:84` defines
`RequestHandler = Callable[[ServerRequestContext[LifespanResultT], _ParamsT], Awaitable[HandlerResult]]`
and `mcp/server/context.py:132` defines `HandlerResult = BaseModel | dict[str, Any] | None`.
The registered `params_type` is validated before the handler is invoked, and the returned
model is serialized by `ServerRunner`.

**Four behaviours the removed decorators supplied must be restored, not three.** The 1.x
`call_tool` decorator (`.venv/lib/python3.11/site-packages/mcp/server/lowlevel/server.py:485-560`)
did all of:

1. **argument adaptation** — unpacked `req.params.name` / `req.params.arguments or {}`
   into the domain-shaped body (`:516-517`);
2. **tool input-schema validation** — `jsonschema.validate(instance=arguments, schema=tool.inputSchema)`,
   returning `_make_error_result(f"Input validation error: {e.message}")` on failure
   (`:521-525`). **`mcp` 2.0.0's low-level `Server` does none of this.** `mcp/server/validation.py`
   contains only sampling/elicitation validation; `jsonschema` appears server-side only under
   `mcp/server/mcpserver/tools/`, which pmcp does not use. This is the behaviour EC-P2-3's
   invalid-arguments case tests, and it must be written by hand;
3. **result wrapping** — bare list → `CallToolResult` (`:538-548`);
4. **exception → JSON-RPC error mapping** — which the plan must restore for the five
   handlers that raise. `call_tool` itself never raises (it catches `Exception` at
   `server.py:332` and returns a JSON `TextContent` at `:360-366`), but `read_resource`
   raises `ValueError` at `server.py:418`, `:420`, `:427`, `:445`, `:449`; `get_prompt`
   raises at `:503`; and all six can raise through `_require_scoped_audit()`
   (`server.py:194`).

Output-schema validation is a no-op here: `src/pmcp/tools/handlers.py` declares 26 tools,
all with `inputSchema` and none with `outputSchema`.

### Trap 1a — the roadmap's registration question is answered by source, and the answer is the constructor callbacks

`mcp/server/lowlevel/server.py:446-462` builds `_spec_requests` from the `on_*` constructor
kwargs and writes `self._request_handlers.update({m: HandlerEntry(pt, h) for m, pt, h in _spec_requests if h is not None})`
at `:462`. `add_request_handler` (`:472-496`) writes `self._request_handlers[method] = HandlerEntry(params_type, handler)`
at `:496`. **They produce the identical `HandlerEntry`** — there is no capability the one
has and the other lacks. The only difference is who supplies `params_type`: the constructor
binds the SDK's canonical model per method (`tools/list` → `PaginatedRequestParams`,
`tools/call` → `CallToolRequestParams`, `resources/read` → `ReadResourceRequestParams`,
`prompts/get` → `GetPromptRequestParams`), whereas `add_request_handler` takes it from the
caller — where a wrong choice silently weakens params validation with no error. The
constructor kwargs are also individually typed (`Awaitable[types.ListToolsResult]`,
`Awaitable[types.CallToolResult | types.InputRequiredResult]`, …), so mypy checks each
handler's exact result type; `add_request_handler` only checks the erased `RequestHandler`
alias. **Decision: SL-2 registers all six through `Server.__init__(on_...=...)`.** See
`## Execution Notes > Decision 1`.

`server/discover` needs no work: `mcp/server/lowlevel/server.py:448` registers
`self._handle_discover` unconditionally, and `:660-667` documents that it derives
`supported_versions` / `capabilities` "from server state at call time, so capabilities
reflect whatever has been registered". Registering the six handlers is what makes
EC-P2-5 true.

### Trap 1b — pmcp keeps its own Starlette app, and that is fine

`transport/http.py:289-293` constructs `StreamableHTTPSessionManager(app=mcp_server,
json_response=False, stateless=False)`. In 2.0.0, `mcp/server/streamable_http_manager.py:181-187`
reads the `mcp-protocol-version` header **before** any stateful/stateless branch and routes
anything that is not a `HANDSHAKE_PROTOCOL_VERSIONS` member to `handle_modern_request`.
pmcp's own app therefore serves the modern era with no change. `transport/http.py:436`
still returns `"ok": True` as a hardcoded literal — `/health` remains a precondition, never
acceptance.

### Trap 2 — the downstream Streamable HTTP client is a rebuild

`client/manager.py:22` imports `streamablehttp_client`. 1.25.0's signature
(`.venv/.../mcp/client/streamable_http.py:686-693`) is
`streamablehttp_client(url, headers=None, timeout=30, sse_read_timeout=300, terminate_on_close=True, httpx_client_factory=create_mcp_http_client, auth=None)`,
and `:709-713` shows it building `create_mcp_http_client(headers=headers, timeout=httpx.Timeout(30, read=300), auth=None)`,
then owning it (`:716 async with client:`). 2.0.0's
(`mcp/client/streamable_http.py:640-644`) is
`streamable_http_client(url, *, http_client: httpx2.AsyncClient | None = None, terminate_on_close: bool = True)`.
`:664` sets `client_provided = http_client is not None` and `:677-678` enters the client
into its exit stack **only** `if not client_provided`. Every keyword pmcp relies on is
gone; `manager.py:1382` passes `headers=headers`, so the naive rename is a `TypeError` and
breaks every authenticated remote downstream.

The exact 1.x behaviour is reproducible: `mcp/shared/_httpx_utils.py` `create_mcp_http_client`
sets `kwargs = {"follow_redirects": True}` and `httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT)`
where those constants are `30.0` and `300.0`. So the 2.x client pmcp must build is
`httpx2.AsyncClient(follow_redirects=True, timeout=httpx2.Timeout(30.0, read=300.0), headers=<resolved headers>)`
— and pmcp must own it, because 2.x will not close it.

The ownership hook already exists: `manager.py:1416` creates `remote_stack = AsyncExitStack()`
and `:1417` enters the transport context into it, storing it as `ManagedClient.sse_exit_stack`.
Entering the `httpx2.AsyncClient` into that same stack, before the transport, gives
close-on-cleanup for free.

Two adjacent facts that mean less churn than feared: 2.0.0's `streamable_http_client` yields
a **2-tuple** `(read_stream, write_stream)` (`mcp/client/streamable_http.py:705`) where 1.x
yielded a 3-tuple, and `manager.py:1418` already does `transport[:2]`. And `sse_client` is
genuinely unchanged: 2.0.0's `mcp/client/sse.py:31-39` has the same
`(url, headers, timeout, sse_read_timeout, httpx_client_factory, auth, on_session_created)`
signature as 1.25.0's, differing only in the `httpx.Auth` → `httpx2.Auth` annotation.
`manager.py:1366` `sse_client(url, headers=headers)` is untouched.

`mcp.shared.message.SessionMessage` survives with the same `message` / `metadata` fields
(`mcp/shared/message.py:59-63`), so `manager.py:1701`'s
`message.message.model_dump(by_alias=True, mode="json", exclude_none=True)` and the
`SessionMessage(msg)` sends at `:1785` and `:1903` still hold — but this is an assumption a
lane must prove, not assert.

### Trap 3 — the `httpx` fallout, and it is bigger than cli.py

`src/pmcp/cli.py` does a bare `import httpx` inside two functions:
`_probe_sse_endpoint` (defined `:1854`, import `:1856`) and `_probe_http_health`
(defined `:1873`, import `:1879`). The roadmap's `~:1844` / `~:1867` have moved. These are
the only two `httpx` references in the entire repo — `grep -rn httpx src/ tests/ .github/`
returns nothing else. `httpx` is **not** declared in `pyproject.toml` (`:33-49`); it rides
`mcp`'s transitive dependency.

`mcp` 2.0.0's `METADATA:26` declares `Requires-Dist: httpx2>=2.5.0` and declares **no**
`httpx` at all. So after the bump, `import httpx` raises `ModuleNotFoundError` in both
diagnostics paths. See `## Execution Notes > Decision 2` for the resolution.

### Trap 4 — three more files break, and the roadmap names none of them

- **`src/pmcp/manifest/refresher.py:207-208`** does `from mcp import ClientSession, StdioServerParameters`
  and `from mcp.client.stdio import stdio_client`, using `async with ClientSession(read, write)`
  at `:245`. All three symbols **still exist** in 2.0.0 (`mcp/__init__.py:66-68`;
  `ClientSession.__init__(read_stream, write_stream, ...)` at `mcp/client/session.py:371-389`;
  `initialize()` at `:613`; `call_tool` overloads from `:955`; `list_tools` at `:1234`), so
  this is very likely a no-op — but "very likely" is not evidence, and this file is in no
  roadmap lane. SL-3 owns proving it.
- **`.github/probe/p1_probe_server.py`** does `from mcp.server.fastmcp import FastMCP`.
  **`mcp.server.fastmcp` does not exist in 2.0.0** — the package is `mcp/server/mcpserver/`
  and the class is `MCPServer` (`mcp/server/__init__.py` exports it;
  `mcp/server/mcpserver/server.py:147-152`, `.tool()` at `:621`, `.run()` at `:357`).
- **`.github/probe/p1_probe_client.py`** does `from mcp import ClientSession` and
  `from mcp.client.streamable_http import streamablehttp_client`.

  Those last two are load-bearing for **EC-P2-1**, not optional polish. The
  `min-version-smoke` job parses the floor with
  `re.search(r'"mcp>=([0-9.]+),<', text)` (`.github/workflows/test.yml:113`) — which still
  matches `"mcp>=2.0.0,<3.0.0"`, so the workflow itself needs no edit — installs
  `mcp==${FLOOR}`, and then at `:136-166` boots the gateway on port `3399` and runs both
  probe scripts. With the floor at `2.0.0` those scripts execute against `mcp` 2.0.0. If
  they are not ported, `min-version-smoke` is red and EC-P2-1 cannot pass.

- `tests/test_credential_boot.py:43,45` builds a `FastMCP("p5-fixture")` fixture and
  `:258,263` uses `ClientSession`; the former breaks for the same reason.

### PR #112, decided by its own CI

`gh pr view 112`: branch `dependabot/pip/mcp-gte-1.0.0-and-lt-3.0.0`, title
"deps: update mcp requirement from <2.0.0,>=1.8.0 to >=1.8.0,<3.0.0", state OPEN,
MERGEABLE. Eight checks pass and one fails — `install-smoke`, whose log (run
`31195645987`, job `92923056918`) is:

```
File "/tmp/fresh/lib/python3.12/site-packages/pmcp/client/manager.py", line 22, in <module>
    from mcp.client.streamable_http import streamablehttp_client
ImportError: cannot import name 'streamablehttp_client' from 'mcp.client.streamable_http'
  (/tmp/fresh/lib/python3.12/site-packages/mcp/client/streamable_http.py).
  Did you mean: 'streamable_http_client'?
```

That is proof the bare cap raise ships a dead install, and it is also the *guard doing its
job*. #112 is therefore **superseded, not merged** — see `## Execution Notes > PR #112`.
Its bound is wrong twice over: `>=1.8.0` contradicts the floor this phase must declare.

### Repo state the lanes inherit

`uv.lock` is committed (`:777` mcp 1.25.0, `:1066` `{ name = "mcp", specifier = ">=1.8.0,<2.0.0" }`),
so any `pyproject.toml` edit dirties it and both belong to one lane. `CHANGELOG.md`'s
`## [Unreleased]` block (line 8) is empty. `tests/` is flat — 50 `tests/*.py` plus
`tests/fixtures/**`, one `tests/conftest.py`. `scripts/validate_plan_doc.py` does **not**
exist in this repo, so the Lane validation checklist is walked by hand and
`validate_plan_dispatch_hints` (importable from the system `python3`, not from `.venv`) is
the machine check. A live gateway is listening on `127.0.0.1:3344`; its pid **this session**
was `1119829`, not the `~1861700` recorded earlier — it restarts, so every runtime step
records the pid at run time and never hardcodes one.

## Interface Freeze Gates

- [ ] IF-0-P2-1 — **Upstream server-side handler contract.** `PMCPServer._create_server`
  constructs `Server("mcp-gateway", instructions=..., on_list_tools=self._handle_list_tools,
  on_call_tool=self._handle_call_tool, on_list_resources=self._handle_list_resources,
  on_read_resource=self._handle_read_resource, on_list_prompts=self._handle_list_prompts,
  on_get_prompt=self._handle_get_prompt)`. Each `_handle_*` is an `async` bound method with
  signature `(self, ctx: ServerRequestContext, params: <the SDK's canonical params model for
  that method>) -> <the SDK's canonical result model>`, i.e. `PaginatedRequestParams | None
  -> ListToolsResult`, `CallToolRequestParams -> CallToolResult`, `PaginatedRequestParams |
  None -> ListResourcesResult`, `ReadResourceRequestParams -> ReadResourceResult`,
  `PaginatedRequestParams | None -> ListPromptsResult`, `GetPromptRequestParams ->
  GetPromptResult`. `_handle_call_tool` validates `params.arguments or {}` against the
  matching tool's `inputSchema` with `jsonschema.validate` **before** dispatch and returns
  `CallToolResult(isError=True, content=[TextContent(type="text", text="Input validation
  error: <msg>")])` on `jsonschema.ValidationError`. The existing domain closures are
  preserved as private helpers; adapters do argument unpacking, schema validation, and result
  wrapping only. `_setup_handlers()` is removed and no `add_request_handler` call is added.
  Consumed by SL-4's wire tests.

- [ ] IF-0-P2-2 — **Downstream Streamable HTTP transport contract.**
  `ClientManager._connect_streamable_http` builds
  `httpx2.AsyncClient(follow_redirects=True, timeout=httpx2.Timeout(30.0, read=300.0), headers=<_remote_headers(...)>)`
  (the `headers` kwarg omitted when `_remote_headers` returns `None`, matching 1.x's
  `create_mcp_http_client`), enters it into the managed connection's `AsyncExitStack`
  (`manager.py:1416`) **before** the transport context, and passes it as
  `streamable_http_client(url, http_client=<that client>)`. The client is exposed on
  `ManagedClient` as a new field `remote_http_client: httpx2.AsyncClient | None = None` so a
  test can assert `.is_closed` after cleanup. `_connect_sse` and
  `PREFERRED_PROTOCOL_VERSION` are unchanged. Consumed by SL-4's EC-P2-7 tests.

- [ ] IF-0-P2-3 — **Dependency contract, published day 1.** `pyproject.toml` declares
  exactly `"mcp>=2.0.0,<3.0.0"`, `"httpx2>=2.5.0,<3.0.0"`, and `"jsonschema>=4.20.0,<5.0.0"`,
  each with the existing load-bearing-bound comment style rewritten (not deleted) to say why
  each bound is load-bearing; `uv.lock` is regenerated in the same commit. `httpx2`'s floor
  matches `mcp` 2.0.0's own `Requires-Dist: httpx2>=2.5.0`; `jsonschema`'s matches its
  `Requires-Dist: jsonschema>=4.20.0`. Consumed by every other lane — nothing else compiles
  until this merges.

- [ ] IF-0-P2-4 — **Modern-envelope wire contract** (test-facing; frozen so SL-4 cannot
  approximate it). A modern request POSTed to the gateway's `/mcp` carries HTTP headers
  `MCP-Protocol-Version: 2026-07-28` (the value that makes
  `streamable_http_manager.py:181-187` route to `handle_modern_request`), `Mcp-Method: <the
  body's method>`, and — for `tools/call`, `prompts/get`, `resources/read` only — `Mcp-Name:
  <the body's name/uri param>`; and a body whose `params._meta` carries both
  `"io.modelcontextprotocol/protocolVersion": "2026-07-28"` and
  `"io.modelcontextprotocol/clientCapabilities": {}`. Key literals are
  `PROTOCOL_VERSION_META_KEY` and `CLIENT_CAPABILITIES_META_KEY` from
  `mcp_types/_types.py:53` and `:62`; the header names are `MCP_PROTOCOL_VERSION_HEADER`,
  `MCP_METHOD_HEADER`, `MCP_NAME_HEADER` from `mcp/shared/inbound.py:59`, `:62`, `:65`; the
  ladder that enforces all of it is `classify_inbound_request` at `mcp/shared/inbound.py:368-450`.
  Tests import the constants rather than retyping the literals. **The request must also send
  `Accept: application/json, text/event-stream`** — pmcp constructs the session manager with
  `json_response=False` (`transport/http.py:290`), and `mcp/server/_streamable_http_modern.py:336-339`
  answers `406` unless the `Accept` header carries *both* media types in that configuration.
  **And the response framing is not fixed**: `:403` takes the plain-JSON path only when
  `json_response` is true, so on pmcp's setting the handler runs under a `15.0`-second
  deferral (`_SSE_PING_INTERVAL`, `:141`); if it finishes inside that window having emitted
  no notification the body is plain `application/json` (`:445-448`), and otherwise the
  response commits to `text/event-stream` and the result arrives as one `event: message` /
  `data: {...}` frame (`:449-462`). Every reader of a modern response — test helper and
  shell command alike — must accept **both** framings; asserting plain JSON would make a
  correct gateway look broken whenever a call is slow or emits progress.

## Lane Index & Dependencies

SL-1 — Dependency + changelog freeze (roadmap Lane C)
  Depends on: (none)
  Blocks: SL-2, SL-3, SL-4, SL-5
  Parallel-safe: yes

SL-2 — Upstream server on mcp 2.x (roadmap Lane A)
  Depends on: SL-1
  Blocks: SL-4, SL-5
  Parallel-safe: yes

SL-3 — Downstream client on mcp 2.x (roadmap Lane B)
  Depends on: SL-1
  Blocks: SL-4, SL-5
  Parallel-safe: yes

SL-4 — Test repair + runtime acceptance harness (roadmap Lane D)
  Depends on: SL-1, SL-2, SL-3
  Blocks: SL-5
  Parallel-safe: no

SL-5 — Documentation & spec reconciliation (author-facing alias: SL-docs)
  Depends on: SL-1, SL-2, SL-3, SL-4
  Parallel-safe: no

## Lanes

### SL-1 — Dependency + changelog freeze (roadmap Lane C)

- **Scope**: Raise the `mcp` bound to 2.x and declare the two dependencies pmcp will now
  import directly, on day 1, so every other lane develops and tests against `mcp` 2.0.0
  immediately.
- **Owned files**: `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `.github/workflows/test.yml`, `tests/mcp2x/test_dependency_bounds.py`
- **Interfaces provided**: IF-0-P2-3
- **Interfaces consumed**: (none)
- **Parallel-safe**: yes

`.github/workflows/test.yml` is listed as owned to remove ambiguity, and is **expected to be
unchanged**: the floor regex at `:113` already matches `"mcp>=2.0.0,<3.0.0"`, and
`install-smoke` is byte-frozen by P1's IF-0-P1-1. If the lane finds it must edit the file, it
must say so in its commit message and re-derive the `install-smoke` byte-freeze.

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/mcp2x/test_dependency_bounds.py` | Declared bounds are two-sided and match what is installed: parse `pyproject.toml` for `mcp`, `httpx2`, `jsonschema`, assert each has both `>=` and `<`; assert `importlib.metadata.version("mcp")` starts with `2.`; assert the P1 floor regex `r'"mcp>=([0-9.]+),<'` still matches and yields `2.0.0`; assert `import httpx2` and `import jsonschema` both succeed | `uv run pytest tests/mcp2x/test_dependency_bounds.py -q` |
| SL-1.2 | impl | SL-1.1 | `pyproject.toml`, `uv.lock` | — | — |
| SL-1.3 | impl | SL-1.2 | `CHANGELOG.md` | — | — |
| SL-1.4 | verify | SL-1.3 | `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, `.github/workflows/test.yml`, `tests/mcp2x/test_dependency_bounds.py` | all SL-1 tests | `uv sync --all-extras && uv run pytest tests/mcp2x/test_dependency_bounds.py -q && git diff --exit-code -- .github/workflows/test.yml` |

SL-1.2 replaces the `mcp` specifier with `"mcp>=2.0.0,<3.0.0"`, **rewriting** the comment
block at `pyproject.toml:34-47` rather than deleting it (the ceiling rationale changes from
"2.0.0 renamed `streamablehttp_client`" to "3.0.0 is unreleased and unaudited"; the floor
rationale changes from "1.8.0 is where `mcp.client.streamable_http` first exists" to "2.0.0
is the only stable 2.x release and the first with `streamable_http_client`,
`Server(on_*=...)`, and `server/discover`"), adds `"httpx2>=2.5.0,<3.0.0"` and
`"jsonschema>=4.20.0,<5.0.0"` each with a one-line comment naming the importing module
(`cli.py` and `server.py` respectively), then runs `uv lock`. SL-1.3 writes the
`## [Unreleased]` entry described in `## Execution Notes > CHANGELOG`.

### SL-2 — Upstream server on mcp 2.x (roadmap Lane A)

- **Scope**: Re-register the six proxied handlers through `Server.__init__`'s typed `on_*`
  callbacks and write the adapters that restore argument adaptation, tool input-schema
  validation, result wrapping, and exception-to-error mapping.
- **Owned files**: `src/pmcp/server.py`, `src/pmcp/transport/http.py`, `tests/mcp2x/test_server_handlers.py`
- **Interfaces provided**: IF-0-P2-1
- **Interfaces consumed**: IF-0-P2-3 (`mcp>=2.0.0`, `jsonschema` declared)
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/mcp2x/test_server_handlers.py` | For each of the six methods: invoke the registered `HandlerEntry.handler` obtained via `server.get_request_handler("<method>")` with a real `ServerRequestContext` and the entry's own `params_type` model, and assert the return value is an instance of the SDK result model (`ListToolsResult`, `CallToolResult`, `ListResourcesResult`, `ReadResourceResult`, `ListPromptsResult`, `GetPromptResult`) with the expected payload. Plus: `get_request_handler("<method>").params_type is <canonical model>` for all six; `_handle_call_tool` with arguments violating a tool's `inputSchema` returns `CallToolResult(isError=True)` whose text starts `Input validation error:`; `_handle_read_resource` on an unknown URI and `_handle_get_prompt` on a policy-blocked prompt each raise so the runner maps them to a JSON-RPC error; `server.get_request_handler("server/discover")` is not `None` | `uv run pytest tests/mcp2x/test_server_handlers.py -q` |
| SL-2.2 | impl | SL-2.1 | `src/pmcp/server.py` | — | — |
| SL-2.3 | impl | SL-2.2 | `src/pmcp/transport/http.py` | — | — |
| SL-2.4 | verify | SL-2.3 | `src/pmcp/server.py`, `src/pmcp/transport/http.py`, `tests/mcp2x/test_server_handlers.py` | all SL-2 tests | `uv run pytest tests/mcp2x/test_server_handlers.py -q && uv run ruff check src/pmcp/server.py src/pmcp/transport/http.py && uv run mypy src/pmcp --exclude baml_client` |

SL-2.2 converts the six closures in `_setup_handlers` (`server.py:198-520`) into six private
`async` methods per IF-0-P2-1, keeping each existing body verbatim as an inner helper so the
policy, audit, and proxy logic is not rewritten alongside the plumbing. `_setup_handlers` is
deleted and `_create_server` (`server.py:172-175`) passes the six bound methods to
`Server.__init__`. The two other `mcp` touchpoints in this file were checked against the
2.0.0 wheel this session and are **expected to need no change**: `mcp/server/stdio.py:162`
still defines `stdio_server(stdin=None, stdout=None)`, so `server.py:744`'s import holds;
and `Server.run` (`mcp/server/lowlevel/server.py:691-701`) is
`run(read_stream, write_stream, initialization_options, raise_exceptions=False)`, which
`server.py:765-769`'s three-positional call already satisfies, with
`create_initialization_options` still present at `:527`. If either turns out to differ at
runtime the lane fixes it and says so in its commit message. SL-2.3 re-verifies `StreamableHTTPSessionManager(app=..., json_response=..., stateless=...)`
against 2.0.0 and is **expected to be a no-op**; if it is, the lane records "verified no
change needed" in its commit message rather than inventing a diff.

### SL-3 — Downstream client on mcp 2.x (roadmap Lane B)

- **Scope**: Rebuild the Streamable HTTP downstream transport around a pmcp-owned
  `httpx2.AsyncClient`, and clear the `httpx` fallout in `cli.py` and the `ClientSession`
  exposure in `manifest/refresher.py`.
- **Owned files**: `src/pmcp/client/manager.py`, `src/pmcp/manifest/refresher.py`, `src/pmcp/cli.py`, `tests/mcp2x/test_client_transport.py`
- **Interfaces provided**: IF-0-P2-2
- **Interfaces consumed**: IF-0-P2-3 (`mcp>=2.0.0`, `httpx2` declared)
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-3.1 | test | — | `tests/mcp2x/test_client_transport.py` | `_connect_streamable_http` passes an `httpx2.AsyncClient` whose `.follow_redirects is True`, whose `.timeout` is `httpx2.Timeout(30.0, read=300.0)`, and whose `.headers` contain every key `_remote_headers` resolved, into `streamable_http_client(url, http_client=...)` — asserted by monkeypatching `streamable_http_client` and capturing the kwargs; `PREFERRED_PROTOCOL_VERSION == "2025-11-25"` and is a `HANDSHAKE_PROTOCOL_VERSIONS` member and **not** in `MODERN_PROTOCOL_VERSIONS`; `_connect_sse` still calls `sse_client(url, headers=...)` unchanged; `SessionMessage` still exposes `.message` so `_read_sse`'s `model_dump` path holds; `manifest.refresher` imports and its `ClientSession` round trip works against an in-process 2.x fixture; `cli._probe_http_health` and `cli._probe_sse_endpoint` resolve `httpx2` and return the documented tuples against a local stub server | `uv run pytest tests/mcp2x/test_client_transport.py -q` |
| SL-3.2 | impl | SL-3.1 | `src/pmcp/client/manager.py` | — | — |
| SL-3.3 | impl | SL-3.2 | `src/pmcp/cli.py`, `src/pmcp/manifest/refresher.py` | — | — |
| SL-3.4 | verify | SL-3.3 | `src/pmcp/client/manager.py`, `src/pmcp/manifest/refresher.py`, `src/pmcp/cli.py`, `tests/mcp2x/test_client_transport.py` | all SL-3 tests | `uv run pytest tests/mcp2x/test_client_transport.py -q && uv run ruff check src/pmcp && uv run mypy src/pmcp --exclude baml_client && rg -n 'follow_redirects=True' src/pmcp/client/manager.py` |

SL-3.2 changes the import at `manager.py:22` to `from mcp.client.streamable_http import
streamable_http_client`, adds `import httpx2`, rewrites `_connect_streamable_http`
(`:1371-1385`) per IF-0-P2-2, adds `remote_http_client` to `ManagedClient`, and threads the
client into `_connect_remote_stream`'s `remote_stack` (`:1416`) ahead of the transport.
`manager.py:1418`'s `transport[:2]` is left alone — 2.0.0 yields a 2-tuple and the slice
still holds. SL-3.3 renames `httpx` → `httpx2` at `cli.py:1856` and `:1879` (the call sites
use only `Timeout`, `AsyncClient(timeout=…, follow_redirects=True)`, `.stream`, `.get`,
`.status_code`, `.json()`, all of which `httpx2` 2.9.1 exports, and `httpx2.AsyncClient.__init__`
is keyword-only, which these call sites already satisfy), and proves `manifest/refresher.py`
either unchanged or minimally adjusted.

**Also in scope for SL-3.2, as an explicit disposition rather than a silent drop**: the
roadmap's `_read_*` audit. JSON-RPC error `code` and `data` are discarded at **two** sites,
not one — `manager.py:1506` (`_handle_stdout_line`, stdio) and `manager.py:1718` (`_read_sse`,
remote), both doing `Exception(payload["error"].get("message", "Unknown error"))`. No P2 exit
criterion depends on typed downstream errors, so SL-3.2 does **not** change the behaviour;
it adds a `# TODO(P3B)` at both sites naming the dropped fields, and SL-5 records the
deferral in the roadmap amendment. Changing it here would alter error semantics inside a
phase whose whole premise is behaviour preservation.

### SL-4 — Test repair + runtime acceptance harness (roadmap Lane D)

- **Scope**: Port the pre-existing suite and CI probes onto `mcp` 2.x, and build the
  deployed-wire runtime harness that proves the six handlers answer in both eras and that
  authenticated and redirected remote downstreams still connect.
- **Owned files**: `tests/*.py`, `tests/fixtures/**`, `tests/mcp2x/conftest.py`, `tests/runtime/**`, `.github/probe/**`
- **Interfaces provided**: `tests/runtime/harness.py` (`gateway_on_spare_port` fixture, `modern_post` helper)
- **Interfaces consumed**: IF-0-P2-1 (the six `_handle_*` methods and their result models), IF-0-P2-2 (`ManagedClient.remote_http_client`), IF-0-P2-3 (declared bounds), IF-0-P2-4 (the modern envelope literals)
- **Parallel-safe**: no (terminal integration lane; depends on SL-1, SL-2, SL-3)

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-4.1 | test | — | `tests/runtime/harness.py`, `tests/runtime/conftest.py` | The `gateway_on_spare_port` fixture itself: it refuses to bind `3344`, it applies the full isolation set, and it asserts `Gateway initialized: <n>/1 servers online` in the boot log | `uv run pytest tests/runtime/ -q -k harness` |
| SL-4.2 | test | SL-4.1 | `tests/runtime/test_wire_handshake_era.py` | EC-P2-2 and EC-P2-3: boot on a spare port, POST handshake-era JSON-RPC to `/mcp`, and validate a **typed** result for each of `tools/list`, `tools/call`, `resources/list`, `resources/read`, `prompts/list`, `prompts/get` by parsing each response's `result` with the SDK's own model; plus a `tools/call` with arguments violating the tool's `inputSchema` returning `isError: true` and text starting `Input validation error:`; plus a boot-log assertion that no line matches `Fatal error` | `uv run pytest tests/runtime/test_wire_handshake_era.py -q` |
| SL-4.3 | test | SL-4.1 | `tests/runtime/test_wire_modern_era.py` | EC-P2-5 and EC-P2-6: over the same deployed `/mcp`, a `server/discover` returning a `DiscoverResult` whose `supported_versions` contains `2026-07-28` and whose `capabilities` advertise tools, resources, and prompts; and modern-envelope `tools/list`, `tools/call`, and invalid-argument `tools/call` built strictly per IF-0-P2-4 | `uv run pytest tests/runtime/test_wire_modern_era.py -q` |
| SL-4.4 | test | SL-4.1 | `tests/runtime/test_downstream_remote.py`, `tests/runtime/fake_remote.py` | EC-P2-7: a fake `mcp` 2.x Streamable HTTP downstream that 401s without the configured `Authorization` header, connected through the gateway and serving a real `gateway.invoke`; a second route that 307-redirects `/relocated` → `/mcp` and still resolves; and a leak proof — N=5 disconnect/reconnect cycles, asserting the previous `ManagedClient.remote_http_client.is_closed` is `True` after each `_cleanup_client` and that the process's open socket count does not grow | `uv run pytest tests/runtime/test_downstream_remote.py -q` |
| SL-4.5 | test | SL-4.1 | `tests/runtime/test_downstream_handshake_era.py` | EC-P2-4: a downstream fixture pinned to `mcp` 1.x connected through the 2.x gateway, serving a real tool call, with `ServerStatus.protocol_version` asserted to be a `HANDSHAKE_PROTOCOL_VERSIONS` member and asserted **not** to be `2026-07-28` | `uv run pytest tests/runtime/test_downstream_handshake_era.py -q` |
| SL-4.6 | impl | SL-4.5 | `.github/probe/p1_probe_server.py`, `.github/probe/p1_probe_client.py` | — | — |
| SL-4.7 | impl | SL-4.6 | `tests/*.py`, `tests/fixtures/**`, `tests/mcp2x/conftest.py` | — | — |
| SL-4.8 | verify | SL-4.7 | `tests/**`, `.github/probe/**` | full suite | `uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/pmcp --exclude baml_client` |

SL-4.6 keeps both probe **filenames** so `.github/workflows/test.yml:142` and `:164` need no
edit: `p1_probe_server.py` becomes `from mcp.server import MCPServer` / `MCPServer("p1probe")`
(the `@mcp.tool()` decorator and `mcp.run()` are unchanged —
`mcp/server/mcpserver/server.py:621` and `:357`), and `p1_probe_client.py` becomes
`from mcp.client.streamable_http import streamable_http_client` with `async with
streamable_http_client(url) as (read, write):` (2.0.0 yields a 2-tuple, so the current
3-tuple unpack must change). `ClientSession` in that file stays. SL-4.7 sweeps the 50
pre-existing `tests/*.py` for 1.x-only API — at minimum `tests/test_credential_boot.py:43,45`'s
`FastMCP` — and repairs them.

**How SL-4.5 gets a genuine `mcp` 1.x downstream inside a 2.x project**, since this is the one
acceptance row whose mechanism is not obvious: a session-scoped fixture builds a throwaway
venv once — `uv venv <tmp>/mcp1 && VIRTUAL_ENV=<tmp>/mcp1 uv pip install "mcp==1.25.0"` — and
registers the downstream in the fixture manifest as
`{"command": "<tmp>/mcp1/bin/python", "args": ["<tmp>/p2_downstream_1x.py"]}`, where that
script is a 1.x `FastMCP` server. The gateway's own interpreter stays on 2.x; only the
subprocess is pinned. The fixture skips (never silently passes) if `uv` is unavailable or the
install fails, and the same `(basename, args)` uniqueness rule as the CI probe applies so the
orphan-kill scan cannot match an unrelated process. `1.25.0` is the version this worktree's
venv already resolves, so it is the known-good 1.x peer.

**How SL-4.1's helper reads a modern response.** `modern_post` must return the decoded
JSON-RPC message regardless of framing: if the response `Content-Type` is
`application/json`, `json.loads(body)`; if it is `text/event-stream`, take the last
`data: ` line of the last `event: message` frame and `json.loads` that. See IF-0-P2-4 for
why both occur under `json_response=False`.

**No new pytest marker.** `tests/runtime/` is selected by path, not by marker: registering a
marker means editing `[tool.pytest.ini_options].markers` in `pyproject.toml`, which SL-1
owns, and a cross-lane edit to a single-writer file is exactly what the partition exists to
prevent. Reusing the existing `live` marker is also wrong — `addopts = "-m 'not live'"`
would deselect these tests by default and the acceptance evidence would silently stop
running. `testpaths = ["tests"]` already picks the directory up, the CI `test` job runs
`uv sync --all-extras` then `uv run pytest tests/` on 3.10/3.11/3.12 (each on its own
runner, so no port contention), and anyone who needs to skip them passes
`--ignore=tests/runtime`. The `http` extra is required, so `uv sync --all-extras` is a
precondition of every command above. `mcp` 2.0.0, `mcp-types` 2.0.0, and `httpx2` 2.9.1 all
declare `Requires-Python: >=3.10`, so the existing matrix is unaffected.

### SL-docs — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, update cross-cutting documentation touched or
  invalidated by this phase's impl lanes, and append post-execution amendments to the phase
  spec for the things this plan found that the roadmap did not name.
- **Owned files**: `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `MIGRATION.md`, `ARCHITECTURE.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `llm.txt`, `llms.txt`, `llms-full.txt`, `docs/**`, `rfcs/**`, `adrs/**`, `.claude/docs-catalog.json`, `specs/phase-plans-v11.md`, `plans/phase-plan-v11-P2.md`
- **Interfaces provided**: (none)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-1, SL-2, SL-3, SL-4

**`CHANGELOG.md` is deliberately excluded from this lane's owned files** — the roadmap gives
it to Lane C (SL-1), and two lanes cannot share the single `## [Unreleased]` block. This
overrides the docs-sweep template's default list for this phase only.

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | — | `.claude/docs-catalog.json` | Rescan: `python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/scaffold_docs_catalog.py" --rescan`. Picks up any new doc files created by impl lanes; preserves `touched_by_phases` history. If the helper is absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and proceed. |
| SL-docs.2 | docs | SL-docs.1 | per catalog | For each file in the catalog, decide: does this phase's work change it? If yes, update the file and append `P2` to its `touched_by_phases`. If no, leave it. Record in the commit message any files intentionally skipped. |
| SL-docs.3 | docs | SL-docs.2 | `specs/phase-plans-v11.md`, `plans/phase-plan-v11-P2.md` | Append `### Post-execution amendments` to the Phase 2 section recording: (a) the three files the roadmap's lane partition omitted — `src/pmcp/manifest/refresher.py`, `src/pmcp/transport/http.py`, `.github/probe/**` — and which lane took each; (b) the corrected `cli.py` line numbers (`:1856`, `:1879`); (c) the second error-`code`/`data` discard site at `manager.py:1718` and its deferral to P3B; (d) the two open decisions as resolved, with their source citations; (e) `mcp.server.fastmcp` not existing in 2.0.0, which is what actually gates EC-P2-1. |
| SL-docs.4 | verify | SL-docs.3 | — | Run any repo doc linters (`markdownlint`, `vale`, `prettier --check`, Mermaid/PlantUML render check). If none configured, no-op. |
| SL-docs.5 | docs | SL-docs.4 | `specs/phase-plans-v11.md` | **Post-merge**, outside the lane DAG: record PR #112's closure — the closing comment URL, the phase PR number that superseded it, and its final state — as the EC-P2-8 evidence artifact. Gates nothing; by construction this evidence cannot exist before the merge. |

## Execution Notes

- **Decision 1 — `Server.__init__(on_*=...)`, not `add_request_handler`.** Source evidence,
  read this session in `mcp/server/lowlevel/server.py`: `:446-462` funnels the `on_*` kwargs
  into `self._request_handlers` as `HandlerEntry(params_type, handler)`, and `:472-496`
  shows `add_request_handler` writing the *same* `HandlerEntry` into the *same* dict. They
  are functionally identical, so the choice is decided entirely by failure modes. The
  constructor binds the SDK's canonical `params_type` per spec method (`:457` `tools/list` →
  `PaginatedRequestParams`, `:458` `tools/call` → `CallToolRequestParams`, `:453`
  `resources/read` → `ReadResourceRequestParams`, `:450` `prompts/get` →
  `GetPromptRequestParams`), which cannot drift; `add_request_handler` takes `params_type`
  from the caller, where a plausible-but-wrong choice (e.g. `types.RequestParams` for
  `tools/call`) silently disables `name`/`arguments` validation and raises nothing. The
  constructor kwargs are also individually typed down to the result model
  (`Awaitable[types.CallToolResult | types.InputRequiredResult]` at `:151-155`), so mypy
  catches a handler returning the wrong shape; the `RequestHandler` alias used by
  `add_request_handler` erases that to `HandlerResult = BaseModel | dict | None`
  (`mcp/server/context.py:132`), which accepts almost anything. **`MCPServer` is not used**,
  per the roadmap.
- **Decision 2 — migrate `cli.py` to `httpx2` and declare `httpx2`; do not declare `httpx`.**
  Source evidence: `mcp` 2.0.0's `METADATA:26` is `Requires-Dist: httpx2>=2.5.0` and the file
  declares no `httpx` at all, so the bare `import httpx` at `cli.py:1856`/`:1879` becomes a
  `ModuleNotFoundError` after the bump. Declaring `httpx` would work but would put a second,
  otherwise-unused HTTP stack in every install purely to avoid a two-line diff, and would
  leave pmcp's diagnostics on a different HTTP library than its own transport. The migration
  is mechanical and was checked against the real package: `httpx2` 2.9.1 exports
  `AsyncClient`, `Timeout`, `Response`, `HTTPStatusError`, `TimeoutException`,
  `RequestError`, `Auth`, `BasicAuth` and 67 more names; `AsyncClient.__init__`
  (`httpx2/_client.py`) accepts `headers`, `timeout`, and `follow_redirects` and is
  keyword-only, which both call sites already satisfy; `AsyncClient.stream`, `.get`, `.post`
  and `Response.json` / `.raise_for_status` all exist. **Neither option is "leave it
  transitive"** — this repo has already shipped broken twice by riding an undeclared
  transitive dependency, which is why P1's guards exist. SL-1 declares `httpx2>=2.5.0,<3.0.0`
  in the same day-1 freeze, and SL-3 does the rename. The same rule forces the third
  declaration: SL-2 will `import jsonschema` directly (2.0.0's low-level `Server` does no
  input-schema validation), and although `mcp` 2.0.0 requires `jsonschema>=4.20.0`
  transitively, pmcp declares it.
- **PR #112 — superseded, not merged.** Its own `install-smoke` run proves a bare cap raise
  ships a dead install (`ImportError: cannot import name 'streamablehttp_client'`, run
  `31195645987`), and its bound `>=1.8.0,<3.0.0` also keeps a floor this phase must raise.
  Mechanism: SL-1's `pyproject.toml` change lands in the phase PR; when that merges to `main`,
  Dependabot closes #112 automatically as no-longer-applicable. Do not rely on that alone —
  SL-docs.5 posts an explicit comment on #112 naming the phase PR that superseded it and
  records the resulting state, so the audit trail says *why* it closed rather than leaving a
  silently-vanished PR. If Dependabot has not closed it within one poll cycle, close it by
  hand with the same comment. This satisfies EC-P2-8's "#112 resolved".
- **Intra-phase CI-red window is expected and intentional.** SL-1's day-1 freeze is the whole
  point of the lane ordering — it publishes `mcp>=2.0.0` so SL-2, SL-3, and SL-4 develop
  against the real 2.x API instead of against a reading of it. Between SL-1 merging into the
  phase integration branch and SL-2/SL-3 landing, that branch's `install-smoke`,
  `min-version-smoke`, and `test` jobs are red, for exactly the reason #112 is red. That is
  the freeze working, not a regression. **`main` only ever sees the combined, green PR** —
  no lane merges to `main` alone.
- **CHANGELOG.** SL-1 writes one `## [Unreleased]` entry covering the whole phase, since SL-1
  is the single writer. It must state which eras are served — handshake `2024-11-05` through
  `2025-11-25` via `initialize`, and modern `2026-07-28` via the per-request `_meta` envelope
  plus `server/discover` — and must not claim `2026-07-28` is negotiable through `initialize`
  or that downstream servers are reached at it. EC-P2-8 checks this sentence.
- **Runtime-step safety, non-negotiable, applies to every SL-4 task and every `## Verification`
  block that binds a port.** A live gateway runs on `127.0.0.1:3344` under systemd; its pid
  **this session** was `1119829` (the previously recorded `~1861700` is stale — it restarts).
  Therefore: record the pid at run time with `ss -ltnp | grep ':3344 '` before and after each
  runtime step and assert it is unchanged; never bind `3344` (the `gateway_on_spare_port`
  fixture asserts `port != 3344` and allocates via `socket.bind(("127.0.0.1", 0))`); never
  use `pgrep -f "pmcp …"`, which self-matches the invoking shell — use the listening socket
  or a bracketed pattern.
- **Boot isolation requires all six controls together, and the assertion is a count of one.**
  `--config` alone does not isolate (the manifest layer falls back to the package-shipped
  catalog), `HOME` alone does not isolate (it removes only the user overlay), and
  `_find_project_manifest()` walks from `Path.cwd()` independent of both `--project` and
  `HOME`. Every boot must pass `--config`, `--project`, `--policy` (this is what bounds the
  orphan-kill set), `--lock-dir`, **and** redirected `HOME` and `XDG_CONFIG_HOME`, and must
  run from a cwd inside the throwaway directory. The correct assertion is
  `Gateway initialized: <n>/1 servers online` — exactly the fixture count, matching
  `.github/workflows/test.yml:163`. **Do not tripwire on "counts in the hundreds"**: the
  shipped manifest legitimately contributes ~106 `skipped` / `policy_denied` entries in a
  correctly isolated run, so those buckets being large is the *correct* output.
- **Never isolate by moving or deleting files outside the worktree.** For any "no overlay"
  condition, change where the code looks (`HOME=/tmp/… XDG_CONFIG_HOME=/tmp/…`). An agent
  deleted the operator's `~/.pmcp/manifest.yaml` doing exactly this last week and the suite
  went green for the wrong reason. No lane and no verification step touches anything under
  the operator's real `$HOME`.
- **`/health` and imports are not acceptance.** `transport/http.py:436` returns `"ok": True`
  as a hardcoded literal, computed from nothing. `import pmcp.server` never instantiates
  `Server`, never registers a handler, and never listens — which is precisely how the
  `mcp` 2.x break survived every import check. Every acceptance item below terminates in a
  real request in / validated typed result out. `/health` is used only as a boot-readiness
  poll.
- **EC-P2-6 must go over the deployed HTTP wire.** A stdio or direct-dispatch test can pass
  while `/mcp` is broken, because the modern era is selected in
  `streamable_http_manager.py:181-187` from the HTTP header — code a stdio test never reaches.
  SL-4.3 POSTs to a running gateway's `/mcp` on a spare port.
- **Single-writer files**: `pyproject.toml`, `uv.lock`, `CHANGELOG.md`,
  `.github/workflows/test.yml` — SL-1 only; no other lane adds a CHANGELOG entry.
  `src/pmcp/server.py`, `src/pmcp/transport/http.py` — SL-2 only. `src/pmcp/client/manager.py`,
  `src/pmcp/cli.py`, `src/pmcp/manifest/refresher.py` — SL-3 only. `tests/conftest.py` and
  every pre-existing `tests/*.py` — SL-4 only; SL-1/2/3 put their new tests under
  `tests/mcp2x/` with distinct filenames so the globs stay disjoint
  (`tests/*.py` does not match `tests/mcp2x/*.py`). `.claude/docs-catalog.json` and
  `specs/phase-plans-v11.md` — SL-docs only.
- **Cross-phase file collision.** `CHANGELOG.md` and `.claude/docs-catalog.json` are written
  by every concurrent phase in this repo and are not lane-partitionable. Writes serialize on
  merge order; whichever phase merges second rebases and re-applies its entry rather than
  resolving in place, and the catalog rescan is re-run after the rebase. The orchestrator
  should expect an add/add or content conflict on exactly these two paths across phases and
  must not read it as a stale-base signal.
- **Known destructive changes**: `src/pmcp/server.py` — SL-2 deletes the `_setup_handlers`
  method and the six `@self._server.<decorator>()` registrations inside it; the handler
  bodies are preserved as inner helpers, not deleted. `src/pmcp/client/manager.py` — SL-3
  deletes the `streamablehttp_client` import at `:22` and the call at `:1382`. Nothing else
  is removed; no file is deleted.
- **Expected add/add conflicts**: none — there is no preamble lane and no lane stubs a file
  another lane replaces. `tests/mcp2x/` is created by whichever of SL-1/SL-2/SL-3 commits
  first; the three files in it are distinct, so this is an add/add on the directory only,
  which git resolves without conflict.
- **SL-0 re-exports**: not applicable — this phase has no preamble lane and adds no
  `__init__.py` symbol.
- **Worktree naming**: `claude-execute-phase` allocates unique worktree names via
  `scripts/allocate_worktree_name.sh`. This plan does not spell out lane worktree paths. On
  this host, worktrees belong under `/mnt/workspace/worktrees/`.
- **`uv sync --all-extras`, never bare `uv sync`.** Bare `uv sync` prunes `pytest`, after
  which `uv run pytest` falls through to a system `pytest` that cannot import `pmcp` and
  reports a misleading pass. The `http` extra is also required by every `tests/runtime/` test.
- **Stale-base guidance** (copy verbatim): Lane teammates working in isolated worktrees do
  not see sibling-lane merges automatically. If a lane finds its worktree base is pre-SL-1,
  it MUST stop and report rather than committing — the orchestrator will re-spawn or rebase.
  Silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree produces commits
  that destroy peer-lane work on `--no-ff` merge. SL-4 additionally must not start before
  SL-2 and SL-3 have merged, since its acceptance tests exercise their symbols.
- **Roadmap gaps this plan closes** (all reported to the lead, all recorded by SL-docs.3):
  the roadmap's Key files and 4-lane partition omit `src/pmcp/manifest/refresher.py`,
  `src/pmcp/transport/http.py`, and `.github/probe/**`; the last of these gates EC-P2-1
  because `mcp.server.fastmcp` does not exist in 2.0.0. The `cli.py` line numbers have moved
  to `:1856`/`:1879`. The error-`code`/`data` discard exists at `:1718` as well as `:1506`.

## Execution Policy
- execute: effort=medium
- repair: effort=medium
- SL-1: effort=low, reason=three specifier edits plus a lock regeneration
- SL-2: effort=high, reason=four removed decorator behaviours must be restored by hand and a wrong params_type silently disables validation
- SL-3: effort=high, reason=transport ownership and redirect semantics regress silently rather than failing loudly
- SL-4: effort=medium, reason=harness construction is mechanical but the isolation rules are unforgiving
- SL-5: effort=low, reason=docs sweep plus a spec amendment

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `roadmap_amendment`
- target surfaces: `src/pmcp/server.py`, `src/pmcp/client/manager.py`, `pyproject.toml`
- evidence paths: `plans/phase-plan-v11-P2.md`, `plans/detailed-mcp-2x-spec-2026-07-28-stage1-20260805-1740.md`, `specs/phase-plans-v11.md`
- redaction posture: `metadata_only`
- downstream handling: roadmap amendment recording the three files the lane partition omitted, the corrected line numbers, the second error-discard site deferred to P3B, and both resolved decisions with citations. Assumption 1a (the two-era split) was **confirmed**, not falsified — `mcp_types/version.py` declares exactly the split the roadmap describes — so no amendment is required on that axis before P3B is planned.

## Acceptance Criteria

- [ ] EC-P2-1 — proven by V1: a wheel built from the merged branch installed into a fresh
      venv with no lockfile and no pin resolves `mcp` 2.x and `pmcp --version` succeeds
      (`install-smoke` reproduced locally), plus V2 reproducing `min-version-smoke` end to
      end — floor parsed as `2.0.0`, installed pinned at it, startup modules imported, and
      the ported `.github/probe/p1_probe_client.py` completing a real downstream tool call
      through a booted gateway. Both CI jobs green on the phase PR.
- [ ] EC-P2-2 — proven by `uv run pytest tests/runtime/test_wire_handshake_era.py -q`, whose
      fixture boots the gateway on a spare port, waits for it to listen, asserts
      `Gateway initialized: <n>/1 servers online`, and asserts no `Fatal error` line in the
      boot log; and by V3, which performs the same boot from the shell and greps the log.
- [ ] EC-P2-3 — proven by `uv run pytest tests/runtime/test_wire_handshake_era.py -q`: all
      six methods POSTed to the deployed `/mcp` and each response's `result` parsed with the
      SDK's own typed model, plus the invalid-argument `tools/call` returning
      `isError: true` with text starting `Input validation error:`. Unit-level coverage of the
      same contract is `uv run pytest tests/mcp2x/test_server_handlers.py -q`. Registry
      presence is asserted only as a precondition inside those tests, never as the criterion.
- [ ] EC-P2-4 — proven by `uv run pytest tests/runtime/test_downstream_handshake_era.py -q`:
      a downstream pinned to `mcp` 1.x serves a real tool call through the 2.x gateway and its
      recorded `protocol_version` is asserted to be in `HANDSHAKE_PROTOCOL_VERSIONS` and
      asserted not to equal `2026-07-28`.
- [ ] EC-P2-5 — proven by `uv run pytest tests/runtime/test_wire_modern_era.py -q -k discover`
      and by V4c: a `server/discover` over the deployed wire returning a `DiscoverResult`
      whose `supportedVersions` contains `2026-07-28` and whose `capabilities` advertise
      tools, resources, and prompts. No aggregated inventory is added — `DiscoverResult`
      carries only `supported_versions`, `capabilities`, and `instructions`.
- [ ] EC-P2-6 — proven by `uv run pytest tests/runtime/test_wire_modern_era.py -q`: modern
      `tools/list`, `tools/call`, and invalid-argument `tools/call` POSTed to a **running
      gateway's** `/mcp` on a spare port with the complete IF-0-P2-4 envelope (both `_meta`
      keys plus matching `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` headers, and an
      `Accept` carrying both media types), and by V4 / V4a / V4b, which reproduce the modern
      `tools/call`, `tools/list`, and invalid-argument `tools/call` from the shell with `curl`
      against a shell-booted gateway so the assertion does not depend on the pytest fixture.
- [ ] EC-P2-7 — proven by `uv run pytest tests/runtime/test_downstream_remote.py -q`: an
      authenticated fake Streamable HTTP downstream that 401s without its header connecting and
      serving a real `gateway.invoke` (proving headers survive the rebuilt client); a
      307-redirected downstream resolving (proving `follow_redirects=True`); and five
      disconnect/reconnect cycles after each of which the prior
      `ManagedClient.remote_http_client.is_closed` is `True` and the process's open socket count
      has not grown. Paired grep, not standalone:
      `rg -n 'follow_redirects=True' src/pmcp/client/manager.py`.
- [ ] EC-P2-8 — proven by V5: `uv run pytest tests/ -q`, `uv run ruff check src/ tests/`,
      `uv run ruff format --check src/ tests/`, and `uv run mypy src/pmcp --exclude baml_client`
      all green; `rg -n '2026-07-28' CHANGELOG.md` showing the `## [Unreleased]` entry naming
      both eras and their access paths; and `gh pr view 112 --json state` reporting `CLOSED`
      with the superseding comment recorded by SL-docs.5.

## Verification

Run from the merged branch. `/tmp/p2*` are throwaway paths. **V2, V3, and V4 start a gateway
on port `3388`** — read `## Execution Notes > Runtime-step safety` and
`> Boot isolation` first. No step may bind `3344`.

```bash
# V0 — dependencies present (never bare `uv sync`)
uv sync --all-extras

# V0a — record the live gateway pid; every runtime step re-asserts it afterwards
LIVE_PID=$(ss -ltnp 2>/dev/null | awk '/127.0.0.1:3344 /{print $0}' | grep -o 'pid=[0-9]*' | head -1)
echo "live gateway before: $LIVE_PID"

# V1 — install-smoke reproduced: unpinned, lockfile-free resolve into a clean venv.
#      This is the ceiling proof: it says the declared bound is installable at all.
rm -rf /tmp/p2fresh /tmp/p2dist
uv build --wheel --out-dir /tmp/p2dist
uv venv /tmp/p2fresh
VIRTUAL_ENV=/tmp/p2fresh uv pip install /tmp/p2dist/*.whl
VIRTUAL_ENV=/tmp/p2fresh uv pip list | grep -E '^(mcp|httpx2|jsonschema) '
/tmp/p2fresh/bin/pmcp --version
/tmp/p2fresh/bin/python -c "import pmcp.client.manager, pmcp.server, pmcp.config.loader"

# V2 — min-version-smoke reproduced end to end, INCLUDING the ported probes.
#      This is the floor proof and the EC-P2-1 acceptance step.
FLOOR=$(python3 - <<'PY'
import pathlib, re
text = pathlib.Path("pyproject.toml").read_text()
m = re.search(r'"mcp>=([0-9.]+),<', text)
assert m, "could not parse the mcp lower bound from pyproject.toml"
print(m.group(1))
PY
)
test "$FLOOR" = "2.0.0"
rm -rf /tmp/p2floor /tmp/p2home
uv venv /tmp/p2floor
VIRTUAL_ENV=/tmp/p2floor uv pip install "$(ls /tmp/p2dist/*.whl)[http]" "mcp==${FLOOR}"
/tmp/p2floor/bin/pmcp --version
mkdir -p /tmp/p2home/lock /tmp/p2home/xdg
cp .github/probe/p1_probe_server.py .github/probe/p1_probe_client.py /tmp/p2home/
printf '{"mcpServers": {"p1probe": {"command": "/tmp/p2floor/bin/python", "args": ["/tmp/p2home/p1_probe_server.py"]}}}\n' > /tmp/p2home/mcp.json
printf 'servers:\n  allowlist: ["p1probe"]\n' > /tmp/p2home/policy.yaml
( cd /tmp/p2home && HOME=/tmp/p2home XDG_CONFIG_HOME=/tmp/p2home/xdg /tmp/p2floor/bin/pmcp \
    --transport http --host 127.0.0.1 --port 3388 \
    --config /tmp/p2home/mcp.json --project /tmp/p2home \
    --policy /tmp/p2home/policy.yaml --lock-dir /tmp/p2home/lock -l info \
    > /tmp/p2boot.log 2>&1 & echo $! > /tmp/p2home/pid )
for _ in $(seq 1 30); do curl -sf --max-time 2 http://127.0.0.1:3388/health >/dev/null && break; sleep 1; done

# V3 — EC-P2-2: booted, listening, isolated to exactly the fixture, no fatal error.
grep -E 'Gateway initialized: [0-9]+/1 servers online' /tmp/p2boot.log
! grep -q 'Fatal error' /tmp/p2boot.log
ss -ltn | grep -q '127.0.0.1:3388'

# V2b — the ported probe client completes a real downstream tool call at the floor.
/tmp/p2floor/bin/python /tmp/p2home/p1_probe_client.py http://127.0.0.1:3388/mcp

# V4 — EC-P2-6 from the shell, independent of the pytest fixture: a modern-envelope
#      tools/call against the SAME running gateway. Header names and _meta keys come
#      from mcp/shared/inbound.py:59,62,65 and mcp_types/_types.py:53,62.
#      `Accept` MUST carry both media types: pmcp sets json_response=False and
#      _streamable_http_modern.py:336-339 answers 406 otherwise. The response body may
#      be plain application/json OR one text/event-stream `data:` frame depending on
#      whether the handler finished inside the 15s deferral window (:403, :445-462),
#      so the reader below accepts both framings.
cat > /tmp/p2read.py <<'PY'
import json, sys
raw = sys.stdin.read()
try:
    msg = json.loads(raw)
except json.JSONDecodeError:
    data = [ln[len("data:"):].strip() for ln in raw.splitlines() if ln.startswith("data:")]
    assert data, f"neither JSON nor an SSE data frame: {raw!r}"
    msg = json.loads(data[-1])
json.dump(msg, sys.stdout)
PY
curl -sS -X POST http://127.0.0.1:3388/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: gateway.health' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
        "name":"gateway.health","arguments":{},
        "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                 "io.modelcontextprotocol/clientCapabilities":{}}}}' \
  | python3 /tmp/p2read.py > /tmp/p2modern.json
python3 -c "import json; d=json.load(open('/tmp/p2modern.json')); assert 'error' not in d, d; assert d['result']['isError'] is False, d"

# V4a — a modern tools/list over the same wire returns the aggregated catalog.
curl -sS -X POST http://127.0.0.1:3388/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: tools/list' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{
        "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                 "io.modelcontextprotocol/clientCapabilities":{}}}}' \
  | python3 /tmp/p2read.py \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'error' not in d, d; assert any(t['name']=='gateway.invoke' for t in d['result']['tools']), d"

# V4b — the same envelope with a deliberately invalid argument must come back as a
#       schema-validation error, proving the restored inputSchema check.
curl -sS -X POST http://127.0.0.1:3388/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: tools/call' -H 'Mcp-Name: gateway.describe' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
        "name":"gateway.describe","arguments":{"tool_id":12345},
        "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                 "io.modelcontextprotocol/clientCapabilities":{}}}}' \
  | python3 /tmp/p2read.py | grep -q 'Input validation error'

# V4c — EC-P2-5: server/discover over the same wire.
curl -sS -X POST http://127.0.0.1:3388/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: server/discover' \
  -d '{"jsonrpc":"2.0","id":4,"method":"server/discover","params":{
        "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                 "io.modelcontextprotocol/clientCapabilities":{}}}}' \
  | python3 /tmp/p2read.py \
  | python3 -c "import json,sys; d=json.load(sys.stdin); r=d['result']; assert '2026-07-28' in r['supportedVersions'], r; assert r['capabilities'].get('tools') is not None, r"

kill "$(cat /tmp/p2home/pid)" 2>/dev/null || true

# V0b — the live gateway is untouched
test "$(ss -ltnp 2>/dev/null | awk '/127.0.0.1:3344 /{print $0}' | grep -o 'pid=[0-9]*' | head -1)" = "$LIVE_PID"

# V5 — EC-P2-8: full suite, lint, types, changelog, and #112.
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
rg -n '2026-07-28' CHANGELOG.md
rg -n '2025-11-25' CHANGELOG.md
gh pr view 112 --json state,url --jq '.state'   # expect CLOSED
```
