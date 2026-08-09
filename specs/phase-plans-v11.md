# PMCP — Phase Plan v11 (MCP spec 2026-07-28 / mcp 2.x fleet migration)

> How to use this document: run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v11-<ALIAS>.md` — the alias is UPPERCASE; `PLAN_RE` in `phase_loop_runtime.plan_manifest` requires an uppercase first character and will not match a lowercase filename), then `/claude-execute-phase <alias>` to build it.

---

## Context

**MCP spec `2026-07-28` is the current stable revision, and `mcp` 2.x is its only implementation.** `mcp` 1.29.0 tops out at `2025-11-25` with no subscriptions support. So the `mcp<2.0.0` caps in `pmcp` (from #111) and `pangram-mcp` — each correct as triage for a package that could not start on a fresh install — are now precisely what pin the fleet to the previous protocol revision.

The migration is smaller than it first appears, and the hard part is already half-built. Six of pmcp's seven `mcp` imports survive 2.0 untouched, but the seventh is **not merely a rename**: `streamable_http_client` also dropped `headers`, `timeout`, `sse_read_timeout`, and `httpx_client_factory` in favour of a caller-built `http_client` — and it is an `httpx2` client, a different httpx major. `sse_client` is unchanged. The genuine break is invisible to imports: with that aliased, every module loads and the gateway then **dies at boot** with `'Server' object has no attribute 'list_tools'` — lowlevel `Server` dropped its decorators in favour of `add_request_handler(method, params_type, handler)`. All six registration sites live in one file. Meanwhile `client/manager.py` already carries a downstream version-negotiation ladder (`PREFERRED_PROTOCOL_VERSION`, an accepted-version list, an initialize-error detector, and a `2024-11-05` retry), so bridging 108 mostly-1.x downstream servers is an **extension of existing machinery, not a new subsystem**.

Two traps are recorded here because both look like the obvious path. `MCPServer` (the FastMCP successor) exposes exactly the six method names pmcp registers — but `StreamableHTTPSessionManager` takes the *lowlevel* `Server`, and `MCPServer.run(transport=...)` owns its own transport, which would displace pmcp's Starlette app, `/health`, `/metrics`, and auth middleware. Stay lowlevel. And the unit suite is **not** valid acceptance: 2276 tests passed while the gateway could not boot.

Sequencing is constrained by a live hazard. Dependabot PR **#112** already proposes raising the cap to `mcp<3.0.0`. Seven checks pass on it — three test-matrix jobs, lint, typecheck, build, notify — and **only `install-smoke` fails**, the guard added in #111 catching an unported cap raise in the wild. That PR must not merge before the port lands, and it is the reason P1 (guard hardening) precedes P2 (the port): the guards are what make the port safe to attempt.

---

## Architecture North Star

```
                    clients (Claude Code, Codex, …)
                              │
                    MODERN ERA │ 2026-07-28 only
                    per-request _meta protocolVersion
                    + server/discover  (NOT initialize)
                              ▼
        ┌─────────────────────────────────────────────┐
        │  pmcp gateway  (mcp 2.x)                    │
        │                                             │
        │   server.py      6 handlers via             │
        │                  add_request_handler(...)   │
        │                  + ADAPTERS: 2.x calls      │
        │                  handler(ctx, typed_params) │
        │                  and validates typed results│
        │   transport/     own Starlette app:         │
        │     http.py      /mcp /health /metrics      │
        │                  (delegates to              │
        │                   handle_modern_request)    │
        │                                             │
        │   client/        HANDSHAKE ladder — ceiling │
        │     manager.py   stays 2025-11-25:          │
        │                  2025-11-25 → 2025-06-18    │
        │                   → 2025-03-26 → 2024-11-05 │
        └─────────────────────────────────────────────┘
                              │
                  HANDSHAKE ERA │ initialize
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
      firecrawl (1.x)   playwright (1.x)   pangram-mcp
       spec 2025-11-25   spec 2025-11-25    (→ 2.x in PG)

  TWO ERAS, NOT ONE LADDER. `mcp_types.version` separates
  HANDSHAKE_PROTOCOL_VERSIONS (2024-11-05 … 2025-11-25, reached via
  `initialize`) from MODERN_PROTOCOL_VERSIONS (2026-07-28 only, reached via
  per-request `_meta` + `server/discover`). 2026-07-28 CANNOT be requested
  through `initialize`. The gateway serves the modern era upward and speaks
  the handshake era downward — never raise the downstream ladder to 2026-07-28.
```

---

## Assumptions (fail-loud if wrong)

1. ~~`mcp` 2.x retains backward compatibility with handshake-based revisions on the client side.~~ **VERIFIED 2026-08-05, no longer an assumption.** A `mcp` 2.0.0 client initialized against a `mcp` 1.29.0 stdio server, negotiated `2025-11-25`, listed tools, and completed a `tools/call`. The downstream handshake path works unchanged from a 2.x gateway.
1a. **The protocol has two eras and they are not interchangeable.** `mcp_types.version` defines `HANDSHAKE_PROTOCOL_VERSIONS = (2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25)` and `MODERN_PROTOCOL_VERSIONS = (2026-07-28,)`. `2026-07-28` is unreachable via `initialize`. Any plan that treats the version list as one ladder is wrong.
2. ~~The six handler bodies are unaffected — only how they are attached moves.~~ **FALSE, corrected 2026-08-05 at review.** 2.x invokes `handler(ctx, typed_params)` and validates a typed result (`RequestHandler = Callable[[ServerRequestContext, _ParamsT], Awaitable[BaseModel | dict | None]]`), while today's bodies take zero or domain arguments and return bare lists. The removed decorators supplied argument adaptation, result wrapping, and tool-input-schema validation. P2 must write explicit adapters restoring all three; treating this as registration-only would raise `TypeError`, emit invalid results, or silently drop input validation.
3. `StreamableHTTPSessionManager` in 2.x still accepts the lowlevel `Server` as `app`, so pmcp keeps owning its Starlette app and auth middleware.
4. `release.yml` + PyPI trusted publishing keep working for both repos, and the `phase-loop` validator runtime stays installed.
5. Downstream third-party servers are not required to move; the gateway absorbs the version difference. No downstream server is dropped by this roadmap.
6. `pangram-mcp` can move to 2.x independently of the gateway, because a 2.x server still accepts an older client. **PG's exit criteria test this explicitly rather than assuming it** — if it fails, PG gains a dependency on P2.

---

## Non-Goals

- Porting any third-party downstream server. The gateway bridges; they stay as they are.
- Implementing MCP subscriptions *end-to-end through the gateway* (proxying downstream `notifications/*` up to clients). P3B lands the client-facing surface only; gateway-level fan-out of downstream notifications is a future roadmap.
- Removing the version-fallback ladder. It stays for the foreseeable future — it is the compatibility contract with 108 servers.
- Any change to the `gateway.*` tool schemas. Client-visible tool contracts are unchanged by this roadmap.
- Re-litigating the `mcp<2` caps as a strategy. They were correct; this roadmap is how they get raised safely.

---

## Cross-Cutting Principles

1. **The unit suite is never sufficient acceptance for a gateway phase.** Every phase touching `pmcp` must prove (a) the process starts and listens, and (b) a real downstream server serves a real tool call through it. 2276 green tests coexisted with a gateway that could not boot.
2. **Never weaken a release guard to make a phase pass.** If `install-smoke` or `min-version-smoke` fails, the change is wrong, not the job.
3. **Every phase leaves `127.0.0.1:3344` working.** The gateway is the operator's daily driver. Phases are independently revertable; no phase may depend on a later phase to restore service.
4. **Dependency bounds stay two-sided.** Raising a floor or ceiling always leaves both ends declared and both ends tested — #111 shipped broken twice from a missing upper bound, and 0.1.2 shipped with a wrong lower bound.
5. **Version floors and ceilings are set by installing, not by reading source.** A review leg derived a floor from when a symbol first appeared; installation proved that version still failed for a different reason.
6. **Protocol vocabulary is frozen to what the spec defines.** No phase invents a method name, `_meta` key, or error shape not in `2026-07-28`.

---

## Top Interface-Freeze Gates

- **IF-0-P1-1** — The four-guard CI contract for first-party Python packages: `install-smoke` (fresh resolve, no lockfile, import the **entry-point** module), `min-version-smoke` (install pinned at the declared floor), a `schedule:` trigger, and a `__version__`-vs-distribution-metadata drift test. P2 and PG both rely on these guards to detect a bad constraint.
- **IF-0-P2-1** — `pmcp` runs on `mcp>=2.0.0,<3.0.0`, serves the MODERN era (`2026-07-28`) to clients via per-request `_meta` + `server/discover`, and speaks the HANDSHAKE era (ceiling `2025-11-25`) downstream. This is the protocol surface P3B extends.
- **IF-0-P2-2** — ~~Handler registration shape: `self._server.add_request_handler(<method-string>, <ParamsType>, <handler>)` on the lowlevel `Server`. P3B registers `subscriptions/listen` through this same call.~~ **STALE, corrected by P3B — see Phase 3B → Post-execution amendments, item 1.** P2's own Decision 1 (`plans/phase-plan-v11-P2.md:648-666`) rejected `add_request_handler` in favour of `Server.__init__(on_*=...)`, and the frozen mechanism P2 actually shipped is IF-0-P2-1, not this gate — whose ID also collides with P2's plan-level IF-0-P2-2 (the downstream Streamable HTTP transport contract, `plans/phase-plan-v11-P2.md:323`). P3B registers `subscriptions/listen` via `on_subscriptions_listen=` on that same constructor. (`server/discover` needs no registration — the 2.x lowlevel `Server` auto-registers it.)
- **IF-0-PG-1** — `pangram-mcp` on `mcp` 2.x, reachable from both a 1.x and a 2.x gateway, published to PyPI.
- **IF-0-P5-1** — A manifest field expressing "credential optional under condition X", replacing the placeholder-secret workaround.

---

## Phases

### Phase 1 — Release-guard hardening (P1)

**Objective**
Level `pmcp`'s CI up to the four guards already proven in `pangram-mcp`, so the cap raise in P2 cannot ship a package that installs but cannot run.

**Exit criteria**
- [ ] EC-P1-1 — A `min-version-smoke` job parses the declared `mcp` floor out of `pyproject.toml`, installs the built wheel pinned at exactly that version, and imports the gateway's startup modules; it fails if the floor is unsatisfiable or non-functional.
- [ ] EC-P1-2 — `.github/workflows/test.yml` gains a `schedule:` trigger and `workflow_dispatch`; a manually dispatched run passes on unchanged `main`.
- [ ] EC-P1-3 — A test asserts `pmcp.__version__ == importlib.metadata.version("pmcp")`, failing if either drifts.
- [ ] EC-P1-4 — The existing `install-smoke` job is unmodified and still passes, and PR #112 still fails only that job (the guard's behaviour is unchanged by this phase).
- [ ] EC-P1-5 — Full suite, ruff, and mypy green; CHANGELOG entry under `### Changed`.

**Scope notes**
- Decompose into 2 lanes with disjoint files. `.github/workflows/test.yml` is a **single-writer file** — one lane owns it entirely; splitting the two job additions across lanes would serialize on conflicts.
- Lane A owns `.github/workflows/test.yml`: adds `min-version-smoke` (floor parser + pinned install) and the `schedule`/`workflow_dispatch` triggers. Pass the parsed floor through `env:`, never inlined into `run:`.
- Lane B owns `tests/`: the `__version__` drift test. Independent of Lane A and can start immediately.
- Port the jobs from `pangram-mcp`'s `.github/workflows/test.yml`, which already runs all four guards — do not re-derive them.

**Non-goals**
- Changing any dependency bound. This phase only adds detection; P2 does the raising.

**Key files**
- .github/workflows/test.yml
- tests/
- CHANGELOG.md

**Depends on**
- (none)

**Produces**
- IF-0-P1-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `roadmap_amendment`
- target surfaces: `.github/workflows/test.yml`, `pyproject.toml`, `uv.lock`, `tests/`
- evidence paths: `plans/phase-plan-v11-P1.md`, `specs/phase-plans-v11.md`
- redaction posture: `metadata_only`

**Post-execution amendments**

*P1's dependency-bound non-goal is narrowed to the ceiling.* The "Non-goals"
above read "changing any dependency bound". Held literally, the phase is
unmergeable: `min-version-smoke` installs pinned at the declared floor, and the
declared floor of `mcp>=1.0.0` does not install. That would land a permanently
red job on `main`, contradicting EC-P1-5 and undermining P2, whose premise is
that these guards are trustworthy. Cross-Cutting Principle 2 forbids weakening
the guard to make the phase pass; Principles 4 and 5 require both bounds be
declared and set by installing. The reading that satisfies all three: **the
non-goal reserves the ceiling raise for P2, while a floor that installation
proves false is corrected here.** P1 therefore changed `mcp>=1.0.0,<2.0.0` to
`mcp>=1.8.0,<2.0.0` and regenerated `uv.lock`. **P2 is unaffected** — it still
raises the cap and re-derives the floor for `mcp` 2.x.

Evidence for the 1.8.0 floor, gathered by installing the built wheel pinned at
each candidate rather than by reading source:

| pinned `mcp` | `import pmcp.client.manager, pmcp.server, pmcp.config.loader` |
|---|---|
| 1.0.0 / 1.6.0 / 1.7.0 / 1.7.1 | `ModuleNotFoundError: No module named 'mcp.client.streamable_http'` |
| 1.8.0 | OK — and the gateway boots, listens, and serves a real downstream tool call |

`client/manager.py:22` imports `streamablehttp_client` from
`mcp.client.streamable_http`, a module that first exists in `mcp` 1.8.0. The
functional half of that evidence is the acceptance step: at `mcp==1.8.0`, with
the startup set bounded by `--policy` to a single throwaway stdio fixture, an
MCP client over Streamable HTTP initialized against the gateway,
`gateway.connect_server` brought the fixture online, and `gateway.invoke` on
`p1probe::p1_echo` returned `p1-floor-ok:floor` with `isError: false`. Imports
are not acceptance for a gateway, and `/health` is barely stronger — it returns
`"ok": True` as a hardcoded literal — so both are retained as preconditions
only.

*EC-P1-1 was tightened, not weakened.* Its text asks only that the job "imports
the gateway's startup modules". As shipped, `min-version-smoke` also boots the
gateway and serves a real downstream tool call, per Cross-Cutting Principle 1.
The `mcp` 2.x break this roadmap exists to fix was invisible to imports and
would also have been invisible to `/health`. Strengthening a guard is always in
scope; weakening one never is.

*Post-merge operational evidence (EC-P1-2, EC-P1-4).* Pending. Neither is
provable from a lane worktree: `workflow_dispatch` is only dispatchable once the
workflow is on the default branch, and #112's checks are only meaningful when
re-run against post-P1 `main`. Recorded here by the post-merge closeout, which
gates nothing — the correlated `workflow_dispatch` run URL, conclusion, and
`headSha`; and #112's old and refreshed head SHAs with the conclusion against
the new one. After P1, #112 is expected to fail `install-smoke` **only**: it
raises the cap but not the floor, so `min-version-smoke` still installs
`mcp==1.8.0` and passes.

---

### Phase 2 — Gateway runtime parity on mcp 2.x (P2)

**Objective**
Raise `pmcp` onto `mcp` 2.x so it runs correctly on the new library and negotiates `2026-07-28` with clients, while continuing to serve every downstream server exactly as today. Behaviour-preserving: no new client-visible protocol features.

**Exit criteria**
- [ ] EC-P2-1 — A wheel built from this phase installs into a clean environment with no lockfile, resolves `mcp` 2.x, and `pmcp --version` succeeds — `install-smoke` and `min-version-smoke` both green.
- [ ] EC-P2-2 — The gateway starts on HTTP and listens on its configured port with no `Fatal error` in the log (this is the check that catches the `'Server' object has no attribute 'list_tools'` class of break, which the unit suite cannot).
- [ ] EC-P2-3 — Each of the six handlers is exercised **at the wire level** — a real request in, a validated typed result out — for `tools/list`, `tools/call`, `resources/list`, `resources/read`, `prompts/list`, `prompts/get`. Registry-presence assertions are explicitly **not** sufficient. `tools/call` is additionally tested with **invalid** arguments and must return a schema-validation error, proving the validation the removed decorators used to supply is still in place.
- [ ] EC-P2-4 — A downstream server still on `mcp` 1.x connects through the 2.x gateway over the **handshake** path and serves a real tool call; the negotiated version for that server is a `HANDSHAKE_PROTOCOL_VERSIONS` member (≤ `2025-11-25`), not `2026-07-28`.
- [ ] EC-P2-5 — A **modern-era** request carrying `io.modelcontextprotocol/protocolVersion: 2026-07-28` in `_meta` is accepted and answered, and the SDK's default `server/discover` returns correct `supported_versions` and `capabilities`. Validating the SDK default is sufficient — `DiscoverResult` carries only `supported_versions`, `capabilities`, and `instructions`, so there is no aggregated inventory to add.
- [ ] EC-P2-6 — **The six proxied handlers work under a modern envelope, exercised over the deployed HTTP wire** — POSTed to the running gateway's `/mcp` on a spare port, not via stdio or direct dispatch: a modern `tools/list` returns the aggregated catalog, a modern `tools/call` succeeds, and a modern `tools/call` with invalid arguments returns a schema-validation error. The envelope must be complete (`_meta` carrying both `protocolVersion` and `clientCapabilities`) with matching HTTP routing headers, since the session manager selects the modern handler from the protocol-version header. A stdio-only or direct-dispatch test can pass while `/mcp` is broken.
- [ ] EC-P2-7 — **An authenticated remote downstream over Streamable HTTP still connects and serves a tool call**, proving the rebuilt `http_client` carries headers. Additionally: a **redirected** downstream still resolves (proving `follow_redirects=True`), and repeated disconnect/reconnect cycles leak no httpx2 clients (proving exit-stack ownership). stdio-only verification does not satisfy this.
- [ ] EC-P2-8 — Full suite green, ruff/mypy clean, CHANGELOG accurate about which eras are served, and PR #112 resolved per the Execution Notes.

**Scope notes**
- **The two eras are the design.** `mcp_types.version` splits them explicitly: `HANDSHAKE_PROTOCOL_VERSIONS = (2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25)` versus `MODERN_PROTOCOL_VERSIONS = (2026-07-28,)`. `2026-07-28` **cannot be requested through `initialize`** — it is reached via per-request `_meta` plus `server/discover`. Therefore: **do not raise `PREFERRED_PROTOCOL_VERSION` to `2026-07-28`.** That constant governs the downstream *handshake* ladder and its ceiling of `2025-11-25` is correct. Upstream (serving clients) and downstream (calling servers) are different eras and must be kept separate in both code and tests.
- Verified in-session, so the plan does not rest on it: a `mcp` 2.0.0 client initializes against a `mcp` 1.29.0 server, negotiates `2025-11-25`, lists tools, and completes a `tools/call`. The downstream path works unchanged from a 2.x gateway.
- Verified in-session: `StreamableHTTPSessionManager` delegates to `handle_modern_request`, so pmcp keeping its own Starlette app does **not** strand it on the old era.
- Decompose into 4 lanes partitioned by disjoint files. Intra-phase freeze, publish day 1: Lane C raises the cap first so every other lane can develop and test against 2.x immediately.
- Lane C owns `pyproject.toml` + `CHANGELOG.md`: raise to `mcp>=2.0.0,<3.0.0`, keeping a two-sided bound and rewriting (not deleting) the load-bearing-bound comment.
- Lane A owns `src/pmcp/server.py`: convert the six decorator registrations to `add_request_handler`. **Handler bodies cannot be attached unchanged** — 2.x invokes `handler(ctx, typed_params)` and validates a typed result (`RequestHandler = Callable[[ServerRequestContext, _ParamsT], Awaitable[BaseModel | dict | None]]`), while today's bodies take zero or domain arguments and return bare lists. The removed decorators supplied argument adaptation, result wrapping, and tool-input-schema validation; this lane must write explicit adapters that restore all three. Note `Server.__init__` also accepts `on_list_tools` / `on_call_tool` callbacks — evaluate them against `add_request_handler` before choosing.
- Lane B owns `src/pmcp/client/manager.py`: **this is not a rename.** 1.29's `streamablehttp_client(url, headers=..., timeout=..., sse_read_timeout=..., httpx_client_factory=...)` becomes 2.x's `streamable_http_client(url, *, http_client: httpx2.AsyncClient | None, terminate_on_close)`. Every one of those keyword arguments is gone; the caller now builds and owns an `httpx2.AsyncClient` carrying headers and timeouts. pmcp passes `headers=` today (around `:1382`), so the naive rename raises `TypeError` and **breaks every authenticated remote downstream**. Note `httpx2` is a different httpx major. **Two behaviours the old helper provided must be reproduced explicitly:** it followed redirects, and it owned and closed the client it created — 2.x sets `client_provided = http_client is not None` and deliberately does **not** close a caller-supplied client. So the rebuilt client needs `follow_redirects=True` and must be owned through the managed connection's exit stack, or redirected downstreams regress and every reconnect leaks a client. `sse_client` is UNCHANGED — do not touch it. Also audit `_read_*` paths: JSON-RPC error `code`/`data` appear to be discarded (around `:1506`), which would make typed-error handling impossible. Leave the handshake ladder ceiling at `2025-11-25`.
- Lane B also owns the `httpx` fallout outside the client: `src/pmcp/cli.py` does a bare `import httpx` (around `:1844` and `:1867`) while never declaring `httpx` in `pyproject.toml` — it rides mcp's transitive dependency. mcp 2.x ships `httpx2` instead, so those imports fail after the bump. Either declare `httpx` explicitly or migrate those call sites to `httpx2`; decide which in the phase plan.
- Lane D owns `tests/` and the runtime acceptance harness for EC-P2-2, EC-P2-4, and EC-P2-5.
- Do **not** migrate to `MCPServer` (see Context). Test on a spare port; never disturb the live service.

**Non-goals**
- Subscriptions / GET retirement (P3B). This phase is library parity plus proof that both eras answer.

**Key files**
- pyproject.toml
- src/pmcp/server.py
- src/pmcp/client/manager.py
- tests/
- CHANGELOG.md

**Depends on**
- P1

**Produces**
- IF-0-P2-1
- IF-0-P2-2

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `roadmap_amendment`
- target surfaces: `src/pmcp/server.py`, `src/pmcp/client/manager.py`, `pyproject.toml`
- evidence paths: `plans/phase-plan-v11-P2.md`, `plans/detailed-mcp-2x-spec-2026-07-28-stage1-20260805-1740.md`
- redaction posture: `metadata_only`
- note: if Assumption 1a (the two-era split) proves wrong, this phase must raise a roadmap amendment before P3B is planned.

### Post-execution amendments

Recorded by SL-docs after SL-1 through SL-4 landed and were verified. Assumption 1a
(the two-era split) was **confirmed, not falsified** — `mcp_types/version.py` declares
exactly the split this roadmap describes — so nothing below reopens that axis; these are
corrections to detail the roadmap's lane partition and scope notes got wrong or omitted,
plus findings surfaced only during execution.

1. **The roadmap's 4-lane partition (Scope notes, above) omits four files the phase plan
   had to give an explicit owner.** `src/pmcp/manifest/refresher.py` (its `ClientSession`/
   `stdio_client` imports were verified unaffected, but the file needed an owner to prove
   that), `src/pmcp/transport/http.py` and `src/pmcp/tools/handlers.py` (both went to Lane
   A / SL-2, alongside `src/pmcp/server.py`), and `.github/probe/**` (went to Lane D /
   SL-4). `src/pmcp/manifest/registry.py` was checked and needs **no** owner: it has no
   `mcp` import, and its camelCase identifiers (`isLatest`, `remoteUrl`, `nextCursor`, …)
   are keys of the unrelated MCP Registry REST API, not MCP protocol models — a camelCase
   regex sweep pulls it in but a check for `mcp` usage correctly excludes it.
2. **`mcp.server.fastmcp` not existing in `mcp` 2.0.0 is what actually gates EC-P2-1.**
   `.github/probe/p1_probe_server.py` imported it; `min-version-smoke` boots a gateway
   from that probe at the declared floor, so EC-P2-1 cannot pass until the probe is
   ported to `mcp.server.MCPServer`. The roadmap's Scope notes for Lane D name the probes
   but not this specific blocking dependency.
3. **Two hard runtime breaks the roadmap never named, both load-bearing:**
   - `Resource.uri` and `TextResourceContents.uri` move from `AnyUrl` to `str` in `mcp`
     2.0.0. `Resource(uri=AnyUrl(...))` raises `ValidationError` under 2.0.0's `Tool`/
     `Resource` models; `src/pmcp/server.py` constructed exactly that at four call sites
     and imported `AnyUrl` for it. SL-2 dropped the import and passed plain strings.
   - `JSONRPCMessage` stops being a pydantic model — it is a bare `types.UnionType` in
     2.0.0 (`mcp_types/jsonrpc.py`) with no `.model_validate`. `client/manager.py` called
     `JSONRPCMessage.model_validate(...)` on every outbound remote request and
     notification; the replacement is `mcp_types.jsonrpc_message_adapter` (a
     `TypeAdapter`), which SL-3 substituted at both call sites.
   - A **measured** finding narrows the churn these two breaks might suggest: 2.0.0's
     `MCPModel` keeps `populate_by_name=True`, so constructor-keyword camelCase (e.g.
     `Tool(inputSchema=...)`) still validates — only *attribute reads* of the camelCase
     name break. `src/pmcp/tools/handlers.py`'s 26 `inputSchema=` construction sites
     therefore needed no source change; only the handful of `.inputSchema`/`.mimeType`/
     `.isError` *attribute reads* elsewhere (`src/pmcp/server.py`, and seven sites in
     `tests/`) had to move to snake_case. See item 6 below for a second gate the plan
     checked only against runtime evidence, not this one.
4. **`cli.py`'s two `import httpx` sites are at `:1856` and `:1879`**, not the roadmap's
   approximate `~:1844`/`~:1867` (both files have moved since the roadmap was written).
   Both were left unedited, per Decision 2: `httpx` is now declared directly rather than
   riding `mcp`'s (removed) transitive dependency, and `httpx2` is declared separately for
   the downstream transport client — two HTTP stacks coexist deliberately.
5. **The JSON-RPC error `code`/`data` discard the roadmap flagged exists at two sites, not
   one**: `client/manager.py`'s stdio path (`_handle_stdout_line`, currently `:1544`) and
   its remote path (`_read_sse`, currently `:1760` — the roadmap's `~:1718` has also
   moved). Both do `Exception(payload["error"].get("message", "Unknown error"))`,
   discarding `code` and `data`. No P2 exit criterion depends on typed downstream errors,
   so SL-3 left the behaviour unchanged at both sites and added a `# TODO(P3B)` naming the
   dropped fields at each — **deferred to P3B**, not a P2 defect.
6. **Three of the "resolved" Execution Notes decisions, plus a fourth the roadmap didn't
   anticipate at all, all confirmed as recorded** — Decision 1 (`Server.__init__(on_*=...)`
   over `add_request_handler`, on typed-`params_type` and mypy-checked-result-type
   grounds), Decision 2 (declare both `httpx` and `httpx2`, leave `cli.py` unedited), and
   Decision 3 (keep the default `OpenTelemetryMiddleware`, pinned by test rather than left
   to inherit silently) all landed exactly as decided, with the source citations the plan
   gives. The fourth, found only once execution ran the CI gates the plan's own reasoning
   had not: **`Tool(inputSchema=...)` validates at runtime but fails `mypy`**, both true
   simultaneously. SL-2/SL-4 hit `Unexpected keyword argument "inputSchema" for "Tool"; did
   you mean "input_schema"?` from `uv run mypy src/pmcp --exclude baml_client` on the
   pre-fix tree and changed all 26 sites in `src/pmcp/tools/handlers.py` to `input_schema=`
   (confirmed by this docs session: `git show 5f40168:src/pmcp/tools/handlers.py | grep -c
   inputSchema=` on the pre-P2 commit is exactly 26; `mypy` on the current, fixed tree is
   clean). Runtime acceptance is fine — `populate_by_name=True` accepts the field name and
   the camelCase alias equally as a *constructor* keyword — but mypy synthesizes
   `Tool.__init__` from
   the field names via `dataclass_transform` and has no notion of alias acceptance, so it
   only accepts `input_schema=`. `uv run mypy src/pmcp` is a required CI gate
   (`tool test command` for SL-2.4/SL-4.8), so this is not cosmetic: item 3's "26 sites
   need no change" claim was verified against runtime behaviour only, which is the wrong
   gate. All 26 sites in `src/pmcp/tools/handlers.py` were changed to `input_schema=`.
   **Rule for future phases: in pmcp source, prefer the field name over the wire alias**,
   even where an SDK's own prose examples show the camelCase alias — it is the only form
   both runtime and `mypy` accept.
7. **A second `httpx2.AsyncClient` leak beyond the one IF-0-P2-2 named.** IF-0-P2-2
   describes the reconnect-path leak (`_cleanup_client` not closing `sse_exit_stack` for
   remote clients) and SL-3 fixed it. Execution surfaced a second, adjacent leak the
   interface freeze didn't name: if entering the transport context itself raises *after*
   the owned `httpx2.AsyncClient` is already in `remote_stack`, the client leaked on
   failed connects too. SL-3 fixed both in `_connect_remote_stream` — the client is
   entered into `remote_stack` first, and a transport-context failure now closes
   `remote_stack` in the `except` branch before re-raising, rather than leaking. Note the
   *first* leak (the reconnect path) is **pre-existing** — it predates the mcp 2.x port,
   since `_cleanup_client` never closed `sse_exit_stack` for remote clients even on 1.x —
   so it reads as a bug fix carried by this phase, not as a regression 2.x introduced.
8. **Open debt for P3B, surfaced but not a P2 defect.** `ClientManager`'s concurrent
   `asyncio.gather` shutdown of every managed client (the `disconnect_all` path) trips
   anyio's cross-task cancel-scope guard when a fully connected-then-torn-down
   streamable-HTTP client crosses an event-loop boundary while sharing that loop with
   another anyio-driven server (e.g. a test's own uvicorn fixture). Isolated by bisection:
   reproduces with a plain, unauthenticated, non-redirected connect, so it is orthogonal
   to IF-0-P2-2's transport rebuild and reads as an anyio/httpx2/uvicorn interaction
   rather than a pmcp defect. `tests/runtime/test_downstream_remote.py` works around it by
   sharing one `run_fake_remote` lifecycle across all of EC-P2-7's assertions and using
   per-name `disconnect_server()` rather than `disconnect_all()`; its module docstring
   carries the full reproduction recipe. Not an exit criterion for P2 — flagged here so
   P3B (which touches `ClientManager`'s shutdown paths directly) inherits the context
   rather than rediscovering it.
9. **Three pre-existing test bugs surfaced once migration errors stopped masking them**,
   all unrelated to the port and fixed in the same commit that repaired the suite
   (`dcc2b0d`): a `test_scoped_advisor_audit.py` call to `gateway.provision` omitted the
   schema-required `server_name` argument, which only started failing once IF-0-P2-1's
   restored input-schema validation ran — correctly — *before* the policy check the test
   meant to exercise; the same file's audit-latch test asserted the raised exception's own
   message ("audit sink is unavailable") rather than the fixed, non-leaking message
   `_handle_call_tool`'s `except ScopedAdvisorAuditError` has always mapped it to ("Scoped
   advisor audit channel failed" — unchanged by this port); and
   `test_baseline_constraints.py` pinned the *absence* of a `description` field on
   `Server`/`Implementation`, which `mcp` 2.0.0 added. pmcp does not use the new
   `description` surface — a possible future target, deliberately not acted on here — the
   test now documents the surface exists rather than asserting it doesn't.
10. **`booted_gateway()` (`tests/runtime/harness.py`) must not require a live gateway on
    `:3344`.** `tests/runtime/` runs under the default `uv run pytest tests/` on every CI
    matrix leg with no marker (see the plan's "No new pytest marker" note), and GitHub
    runners carry no pmcp systemd unit at all. A hard assertion that a live gateway exists
    would have failed 13 of 18 runtime tests on the phase PR's own CI — making EC-P2-8
    unreachable there before it could even report red for a real reason. The fixture
    treats an absent live gateway (`live_pid is None`) as "nothing to protect, proceed,"
    matching the plan's own V0a/V0b semantics where an empty `LIVE_PID` on both sides is a
    passing comparison, not an abort condition. Recorded so a future edit doesn't
    reintroduce a hard assert here.

---

### Phase 3B — Subscriptions and GET retirement (P3B)

**Objective**
Implement `subscriptions/listen` and retire the HTTP GET endpoint it replaces, which is the one part of this migration that changes observable client behaviour.

**Exit criteria**
- [ ] EC-P3B-1 — `subscriptions/listen` is registered through the IF-0-P2-2 shape, honours the `notifications` filter, sends `notifications/subscriptions/acknowledged` as the first message carrying `io.modelcontextprotocol/subscriptionId` in `_meta`, and never sends a notification type the client did not request.
- [ ] EC-P3B-2 — Cancellation works from both sides: a client closing the stream (HTTP) or sending `notifications/cancelled` (stdio) ends the subscription, and a server-initiated close sends the empty `subscriptions/listen` response before closing.
- [ ] EC-P3B-3 — The HTTP GET path on `/mcp` is retired without breaking `/health` or `/metrics`, and a client using the old GET flow receives a defined error rather than a hang.
- [ ] EC-P3B-4 — **A real catalog mutation delivers a notification end to end.** Calling `gateway.connect_server` / `disconnect_server` / `refresh` — which mutate the tool, resource, and prompt indexes in `ClientManager` — causes a subscribed client to receive the corresponding `notifications/*/list_changed`. A synthetic test that fires the publisher directly does **not** satisfy this criterion.
- [ ] EC-P3B-5 — Gateway starts, a downstream tool call still succeeds, and a client with no subscription is unaffected; full suite, ruff, mypy green.
- [ ] EC-P3B-6 — CHANGELOG documents the GET retirement as a breaking client-visible change, released as a **major version**.

**Scope notes**
- **A listener with no publishers is the failure mode to design against.** `subscriptions/listen` lives in `src/pmcp/server.py`, but the events that should drive it originate in `src/pmcp/client/manager.py`, where tools/resources/prompts are indexed and removed (around `:1099`). Without an explicit event path from those mutations to the subscription, handler-level tests pass while live `connect_server` / disconnect / refresh deliver nothing. Define the publisher interface **before** the listener.
- Decompose into 4 lanes. Lane A owns `src/pmcp/transport/http.py` (GET retirement, route table, protocol-version header). Lane B owns `src/pmcp/server.py` (the `subscriptions/listen` handler). Lane C owns `src/pmcp/client/manager.py` (**the production event publishers** — the lane that was missing). Lane D owns `tests/`.
- Lanes A, B, and C touch different files but share one contract: the subscription id, the event payload shape, and the stream lifecycle. Publish that contract as an intra-phase freeze on day 1 so Lane D can write tests against it immediately.
- **Release as a major version.** "Explicitly flagged breaking minor" was rejected at review: retiring a documented endpoint is a compatibility break and semver should say so plainly.
- Highest-risk phase in the roadmap — it is the only one that changes what existing clients see. Ship it alone.
- Multiple concurrent subscriptions must demultiplex by `subscriptionId`; on stdio all subscriptions share one channel.

**Non-goals**
- Proxying downstream servers' `notifications/*` up to clients (future roadmap; see Non-Goals).

**Key files**
- src/pmcp/transport/http.py
- src/pmcp/server.py
- src/pmcp/client/manager.py
- tests/
- CHANGELOG.md

**Depends on**
- P2

**Produces**
- (none)

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `src/pmcp/transport/http.py`, `src/pmcp/server.py`, `CHANGELOG.md`
- evidence paths: `plans/phase-plan-v11-P3B.md`
- redaction posture: `metadata_only`

### Post-execution amendments

Recorded by SL-docs after SL-1 through SL-5 landed and were verified. `plans/phase-plan-v11-P2.md`
was checked against every item below and needs no edit of its own — its Decision 1 already
recorded the constructor form correctly; the drift was entirely in this roadmap's own
Top Interface-Freeze Gates section (item 1).

1. **EC-P3B-1's "IF-0-P2-2 shape" pointed at a stale gate, and the gate itself is now
   corrected in place above (Top Interface-Freeze Gates).** The roadmap's `IF-0-P2-2`
   (`:100`, pre-amendment) prescribed `add_request_handler`, which P2's own Decision 1
   rejected in favour of `Server.__init__(on_*=...)` — and the ID collided with P2's
   plan-level IF-0-P2-2 (the downstream transport contract, `plans/phase-plan-v11-P2.md:323`).
   P3B honoured EC-P3B-1's *intent* — register through the frozen handler-registration
   mechanism, whatever it turns out to be — which is the constructor kwarg
   `on_subscriptions_listen=`, typed exactly to `ListenHandler.__call__` and the only form
   `mypy` accepts for a result-typed handler. `src/pmcp/server.py:228`.
2. **A missing lane-dependency edge: SL-2 → SL-3.** SL-2's declared "Interfaces consumed"
   named only IF-0-P3B-1 and was marked "Parallel-safe: yes"
   (`plans/phase-plan-v11-P3B.md:455-456`), but IF-0-P3B-2 requires `GatewayServer` to pass
   `catalog_events=` into `ClientManager` — a kwarg SL-3 owns. SL-5 *did* declare
   IF-0-P3B-2 as consumed, so the roadmap-level interface existed; the lane table simply
   never drew the SL-2 → SL-3 edge. SL-2 correctly stopped short of editing SL-3's file,
   built everything else, and landed a one-line follow-up (`8a0e4ea`, "IF-0-P3B-2 follow-up")
   after SL-3 merged (`dce7bf9` then `8a0e4ea`). Future lane tables must derive
   "Interfaces consumed" from what the interface freeze's *construction order* requires,
   not from what a lane's own scope paragraph mentions.
3. **The two production `flush()` calls IF-0-P3B-1 mandated as an "ordering nicety" were
   removed — a deliberate deviation from the plan's own text, not an oversight.** The plan
   required `_index_capabilities` and `disconnect_server` to end with
   `await self._catalog_events.flush()`. Both sit on exactly the paths EC-P3B-4 drives, so
   the acceptance criterion could have passed with the self-scheduling drain silently
   broken — `flush()` would deliver the event regardless and the mechanism under test would
   never run. The plan carried this tension internally: it mandated the flushes *and*
   required the e2e to prove self-draining. SL-3 resolved it in favour of the criterion;
   `src/pmcp/client/manager.py` has no `flush()` call on either path (confirmed by grep —
   the two remaining `flush()`-adjacent comments at `:896` and `:1292` are deliberate
   "no flush() here" markers), and SL-3's lane tests plus EC-P3B-4 pass without them, which
   is the proof the self-scheduling drain delivers on production paths rather than riding a
   flush call site.
4. **`gateway.refresh` is diff-based and will not reconnect a never-eager server; EC-P3B-4's
   e2e models a real operator flow rather than assuming a bare refresh redelivers.**
   `tools/handlers.py`'s refresh builds `to_connect` from `eager_by_name` only, so a
   disconnected lazy server is left alone by a bare refresh. `tests/runtime/test_subscriptions_e2e.py`
   therefore adds the second downstream fixture to `autoStart` on the isolated boot's
   on-disk config between `disconnect_server` and `refresh` — modelling an operator editing
   `autoStart` then refreshing — rather than defeating or working around the diff logic.
   Verified by smoke test before the acceptance test was written.
5. **`client/manager.py:1099` (as cited by this roadmap's Phase 3B scope notes) is
   `_index_tools` at `:1106` on the commit P3B actually branched from** — consistent with
   Phase 2's amendment 2, which found the same drift; line numbers in this roadmap are
   provenance snapshots and move as the tree does.
6. **The plan mispredicted one test failure.** It expected `tests/test_transport_http.py:212`
   to go red on GET retirement; it did not, because that line's test
   (`test_metrics_counter_increments_on_401`) carried a stale comment
   ("GET with no session ID returns keep-alive SSE") but no assertion on the GET response
   at all — it passed throughout. Adding a real `405` assertion there was new work SL-5.5
   did, not a repair of a red test.
7. **An inherited flaky assertion was fixed, not introduced by this phase.**
   `tests/runtime/test_downstream_remote.py` asserted strict equality on socket counts
   across reconnect cycles while its own comment and failure message described detecting
   *growth*; a late-closing socket makes a later sample lower than an earlier one, so the
   test could fail on a *decrease* while reporting "socket count grew". Inherited from P2
   and already on `main` before P3B started; SL-5 changed the assertion to a no-growth
   check consistent with its own stated intent.
8. **Two harness capabilities EC-P3B-4 required that SL-5.1's contract did not name in
   advance**: `booted_gateway(extra_servers_no_autostart=...)` — without it,
   `gateway.connect_server` in the e2e test would be a no-op rather than a genuine first
   connect, because the fixture would already be running — and
   `booted_gateway(request_timeout=...)`, needed for the deployed-wire timing regression
   that proves the `request_timeout` exemption. Both are additive to the harness's existing
   design goal (nothing hardcodes a live catalog change) rather than a change to it.
9. **The anyio `disconnect_all` debt Phase 2's amendment 8 flagged for this phase was
   deferred again, deliberately — see this plan's Execution Notes → Decision 3 for the full
   reasoning** (no production exposure in the systemd deployment, a real fix is a
   concurrency refactor out of scope for the riskiest phase in the roadmap, the P2 workaround
   still holds, and this phase's only new shutdown code — `ListenHandler.close()` — is sync
   and never crosses a cancel scope). Recorded here so the debt does not read as forgotten;
   it is carried forward to a future bounded phase after the 2.0.0 release.
10. **The SDK ships the listener; this phase wrote no listen-stream state machine.** `mcp`
    2.0.0's `mcp/server/subscriptions.py` supplies both `ListenHandler` (ack-first,
    subscription-id stamping, filter honouring, backlog/concurrency bounds, graceful close)
    and `SubscriptionBus`/`InMemorySubscriptionBus`. P3B's work was the wiring — a sink
    bridging pmcp's sync catalog mutators to the SDK's async bus, construction order, and
    HTTP transport survival — not the mechanism. This roadmap's Phase 3B objective and
    scope notes read as though pmcp builds the subscription machinery; it does not.
11. **One `2.0.0` release covers both P2 and P3B, deviating from this roadmap's stated
    cadence.** Execution Notes → "Release cadence" (`:621`) states "P2 is a minor
    (`1.22.0`)" and "P3B is a MAJOR version" as two separate releases. In practice, `1.22.0`
    (2026-08-06) shipped P5's manifest credential optionality instead — P2's mcp-2.x port
    sat in `## [Unreleased]` until P3B's SL-1 promoted it directly into `## [2.0.0]`
    alongside the GET-retirement entry. This was raised with the operator as the one
    droppable task in the phase and explicitly decided: keep a single `2.0.0`, do not cut
    an interim minor at tag time — see `plans/phase-plan-v11-P3B.md`'s Execution Notes →
    Decision 4 for the reasoning (P2 and P3B are one migration; a minor whose entire
    content is "the SDK underneath changed" invites operators to take it and the major
    separately, doubling the upgrade events for one migration) and the CI-coherence argument
    (P1's drift test pins `pyproject.toml` against `src/pmcp/__init__.py` only, so a
    CHANGELOG heading of `[2.0.0]` while both files still said `1.22.0` would have left
    `main` self-inconsistent with nothing in CI flagging it).

---

### Phase 4 — pangram-mcp onto mcp 2.x (PG)

**Objective**
Move the first-party `pangram-mcp` server off the removed `FastMCP` API onto `MCPServer`, raising its `mcp` cap, and prove it is reachable from both a 1.x and a 2.x gateway.

**Exit criteria**
- [ ] EC-PG-1 — `src/pangram_mcp/server.py` uses `MCPServer` instead of `mcp.server.fastmcp.FastMCP`; the `analyze` tool keeps its `ToolAnnotations` and its input/output contract is byte-identical.
- [ ] EC-PG-2 — `pyproject.toml` declares a two-sided `mcp` bound whose floor is verified by installing at exactly that floor (not by reading source), and all four guards from IF-0-P1-1 pass.
- [ ] EC-PG-3 — The **currently published 1.x** `pmcp` gateway connects to the ported server and `pangram::analyze` returns a live classification — this is the test of Assumption 6; if it fails, PG gains a dependency on P2 and the roadmap is amended.
- [ ] EC-PG-4 — After P2 lands, the 2.x gateway also connects and serves `pangram::analyze`.
- [ ] EC-PG-5 — Published to PyPI; `uvx pangram-mcp` starts clean from a cold cache.

**Scope notes**
- Decompose into 2 lanes with disjoint files. Lane A owns `src/pangram_mcp/server.py` (the API port). Lane B owns `pyproject.toml` + `tests/` (bound raise, floor bisection, drift test).
- Port surface is small: `MCPServer.tool()` still accepts `annotations`, and `ToolAnnotations` still lives in `mcp.types`. Verify both against the installed 2.x library before writing code.
- Different repository (`Consiliency/pangram-mcp`) with its own release cadence — deliberately not coupled to the gateway port, so a three-line change is not gated behind a gateway rewrite.
- EC-PG-4 can only be checked after P2; run EC-PG-1..3 and EC-PG-5 first and treat EC-PG-4 as a follow-up verification, not a blocker on publishing.

**Non-goals**
- Any change to the `analyze` tool's behaviour, schema, or the Pangram API integration.

**Key files**
- src/pangram_mcp/server.py
- pyproject.toml
- tests/test_server.py

**Depends on**
- (none)

**Produces**
- IF-0-PG-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pangram_mcp/server.py`, `pyproject.toml`
- evidence paths: `plans/phase-plan-v11-PG.md`
- redaction posture: `metadata_only`

---

### Phase 5 — Manifest credential optionality (P5)

**Objective**
Let a manifest entry express "credential required for the vendor endpoint, optional for a self-hosted one", removing the placeholder-secret workaround that #114 documents.

**Exit criteria**
- [ ] EC-P5-1 — A manifest entry can declare that its credential is optional under a named condition, and the requirement is evaluated by **one shared predicate** that every enforcement path calls — not re-implemented per call site.
- [ ] EC-P5-2 — A server whose credential is genuinely required still fails closed at **every** gate; no server becomes reachable without auth that was not already.
- [ ] EC-P5-3 — With the new field in use, an operator reaches a self-hosted endpoint with **no placeholder credential** anywhere, and it works through **all six consumers**: eager startup, provision/install, lifecycle connect, `pmcp secrets check` diagnostics, and capability discovery (`gateway.catalog_search` / `gateway.request_capability` must not report the server as key-missing or advise `auth_connect`). Passing only the connect path does not satisfy this.
- [ ] EC-P5-4 — Existing manifest entries are unaffected (the field is optional and defaults to today's behaviour); full suite, ruff, mypy green.

**Scope notes**
- **`requires_api_key` is enforced in FIVE independent places** — eager startup (`src/pmcp/config/loader.py`, around `:1038`), install (`src/pmcp/manifest/installer.py` `check_api_key`, around `:541`), lifecycle connect (`src/pmcp/tools/handlers.py`, around `:3200`), provisioning (`src/pmcp/tools/handlers.py`, around `:4058`), **diagnostics** (`src/pmcp/cli_commands/secrets.py`, around `:93` — `pmcp secrets check`), and **capability discovery** (`_get_server_env_metadata`, `src/pmcp/tools/handlers.py:3355`, consumed at `:1403`, `:3643`, `:3730`, `:3840` — this is what makes `gateway.catalog_search` and `gateway.request_capability` report a server as key-missing and tell operators to run `gateway.auth_connect`) — spanning 6 files and ~26 references. Successive review rounds found this list at four, then five, then six; each time the omitted consumer would have kept demanding the placeholder while every other path was fixed. Patching a subset produces a server that connects manually but fails auto-start, provisioning, or diagnostics. **Centralize first, then consume — and enumerate by grepping, not from memory.**
- This is the same shape as the credential-namespacing work in #95: a requirement evaluated on several independent axes, where fixing one member of the class looks like fixing the class. Enumerate every call site before writing code.
- Decompose into 3 lanes. Lane A owns `src/pmcp/manifest/loader.py` (the field plus the shared predicate) and publishes its signature as a day-1 intra-phase freeze. Lane B owns **all six** consumers (`config/loader.py`, `manifest/installer.py`, `tools/handlers.py`, `cli_commands/secrets.py`) and converts every gate to the predicate. Lane C owns `tests/`, including a test that fails if a new `requires_api_key` check appears outside the predicate — that test is what keeps the count honest.
- #114 lists three candidate designs and explicitly rejects inferring from a URL-shaped override as a heuristic. Pick an **explicit** signal.
- Independent of the protocol migration — touches the manifest/config path, not the protocol path — so it can run concurrently with P2.
- Security-sensitive: the failure mode is a server reachable without auth. Weight verification toward EC-P5-2.

**Non-goals**
- Changing how credentials are stored or namespaced.

**Key files**
- src/pmcp/manifest/loader.py
- src/pmcp/manifest/installer.py
- src/pmcp/config/loader.py
- src/pmcp/tools/handlers.py
- src/pmcp/cli_commands/secrets.py
- tests/
- CHANGELOG.md

**Depends on**
- (none)

**Produces**
- IF-0-P5-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/manifest/loader.py`, `tests/`
- evidence paths: `plans/phase-plan-v11-P5.md`
- redaction posture: `metadata_only`

### Post-execution amendments

- **Consumer count revised from six (above) to SEVEN.** Execution
  (`plans/phase-plan-v11-P5.md`) independently re-derived the enforcement
  surface with two sweeps rather than trusting this section's count, because
  the count had already drifted 4 → 5 → 6 across planning rounds — each
  omission would have left one path still demanding the placeholder while
  every other gate was fixed. Sweep 1 (`rg -c requires_api_key src/**/*.py`,
  AST-classified into reads/writes/definitions) found the six consumers
  listed above. Sweep 2 (an AST walk of every `ast.If` whose test mentions
  `env_var` but not `requires_api_key`) found the seventh: `pmcp init`
  (`src/pmcp/cli.py:1528`, `run_init`) gates on `if server.env_var:` alone —
  it contains no `requires_api_key` token at all, which is exactly why a
  token-only sweep never found it across six review rounds. The counting
  method is recorded here so it stops moving; see
  `plans/phase-plan-v11-P5.md`'s "Sweep 1" / "Sweep 2" for the full
  methodology and the four producer sites (keyword *writes*) deliberately
  excluded from the consumer count.
- **`Produces` was incomplete.** This section lists only `IF-0-P5-1`.
  Execution additionally froze `IF-0-P5-2` (`ServerConfig.api_key_optional_when`
  parsing), `IF-0-P5-3` (`_get_server_env_metadata`'s effective-value
  contract, feeding the ninth downstream reporting sites unchanged), and
  `IF-0-P5-4` (the child-environment consistency invariant — a relaxed gate
  must imply the spawned child actually receives the relaxer, not merely that
  the gate opened). `IF-0-P5-4` closes a distinct security gap found during
  planning: a predicate that read `os.environ` instead of the manifest's
  `extra_env` would relax a gate for a variable `sanitized_subprocess_env`
  then strips before the child spawns, silently reaching the vendor endpoint
  unauthenticated.
- **EC-P5-3's "all six consumers" wording is stale.** The phase plan's
  EC-P5-3 (`plans/phase-plan-v11-P5.md`) correctly enumerates and proves all
  seven, including `pmcp init`.
- No interface freeze in `plans/phase-plan-v11-P5.md` itself proved wrong
  during execution — all four (`IF-0-P5-1` through `IF-0-P5-4`) shipped with
  the signatures and semantics as frozen.

---

### Phase 6 — Deferred v10 robustness remnants (P6CLEAN)

**Objective**
Finish the one item from `specs/phase-plans-v10.md` Phase 6 that never landed, so the closed v10 roadmap has no silent leftovers.

**Exit criteria**
- [ ] EC-P6CLEAN-1 — `tests/test_manifest.py:1775` polls job state instead of `asyncio.sleep(0.3)`; the test passes with no wall-clock dependency and does not regress under load.
- [ ] EC-P6CLEAN-2 — Full suite, ruff, and mypy green; CHANGELOG entry recording that this closes out v10 P6.

**Scope notes**
- **Single lane, justified.** This is one test-file change; a second lane would contend on the same file for no benefit. This is the preamble/cleanup exception to the ≥2-lane rule, not an oversight.
- **Task-registry eviction was REMOVED from this phase at review — it is already done.** An earlier draft claimed it was missing, having searched `src/pmcp/tools/handlers.py` because that is where v10's P6 text pointed. The registry is actually in `src/pmcp/client/manager.py` and already has bounded terminal-record eviction (`_max_terminal_tasks`, `:507-512`), the done-callback `pop` (`:597`), and a regression test (`tests/test_client_manager.py:1588`). **Do not re-plan it.** The lesson generalises: verify a "missing" item against the code, not against the roadmap prose that described it.
- Fully disjoint from every other v11 phase, so it can run concurrently with all of them.

**Non-goals**
- Any other item from v10 P6 — the rest were verified as landed when v10 was closed on 2026-08-05.

**Key files**
- tests/test_manifest.py
- CHANGELOG.md

**Depends on**
- (none)

**Produces**
- (none)

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/tools/handlers.py`, `tests/test_manifest.py`
- evidence paths: `plans/phase-plan-v11-P6CLEAN.md`, `specs/phase-plans-v10.md`
- redaction posture: `metadata_only`

---

## Phase Dependency DAG

```
  P1 ──► P2 ──► P3B

  PG        (root — parallel with P1/P2, EC-PG-4 verifies after P2)

  P5        (root — parallel with everything, disjoint file set)

  P6CLEAN   (root — parallel with everything; closes out v10 P6)


  Critical path:  P1 → P2 → P3B         (P3B is the riskiest phase)
  Concurrent:     P1, PG, P5, and P6CLEAN may all be planned and started at once.
                  server/discover needs no phase — the SDK auto-registers it.
```

---

## Execution Notes

Plan and execute:

```
/claude-plan-phase P1     &&  /claude-execute-phase p1
/claude-plan-phase PG     &&  /claude-execute-phase pg     # concurrent with P1
/claude-plan-phase P5     &&  /claude-execute-phase p5     # concurrent with P1
/claude-plan-phase P2     &&  /claude-execute-phase p2     # after P1
/claude-plan-phase P3B    &&  /claude-execute-phase p3b    # after P2
/claude-plan-phase P6CLEAN && /claude-execute-phase p6clean # concurrent with anything
```

**P1, PG, P5, and P6CLEAN can be planned concurrently** — they share no DAG ancestor and touch disjoint file sets across two repositories.

**Relationship to v10.** `specs/phase-plans-v10.md` was closed on 2026-08-05 with a status header recording file:line evidence that six of its seven phases shipped across the 1.19.x–1.20.0 line — outside this pipeline, as ordinary PRs, which is why it has no lane plans or manifest entries. P6CLEAN carries its two unfinished items so nothing is silently dropped. Do not plan or execute any phase from v10.

**Disposition of Dependabot PR #112.** Leave it **open and unmerged** through P1. It is currently the best live evidence that `install-smoke` works: seven checks green, only that one red, on a real unported cap raise. Treat it as a standing canary. When P2 lands and raises the cap deliberately, close #112 as superseded with a comment pointing at this roadmap — do not merge it, because its cap raise carries none of the port. If Dependabot reopens it before P2, re-close it; do not disable the update.

**Release cadence.** P1 is a patch. P2 is a minor (`1.22.0`) — new library, same client-visible behaviour. **P3B is a MAJOR version** — retiring a documented endpoint is a compatibility break and semver should say so; "explicitly-flagged breaking minor" was proposed and rejected at review. PG is a patch on its own repo.

**Review history.** This roadmap went through three advisor-board rounds before any phase was planned. Three legs responded; two returned DISAGREE with five blocking findings, all independently confirmed against the code and reconciled. A second board on the reconciled draft found more: the Streamable HTTP client lost `headers`/`timeout` (not a rename), `DiscoverResult` cannot carry proxied inventory (so P3A was DELETED), the modern envelope was never proven against the six proxied handlers, and credential enforcement has FIVE consumers, not four. All are reconciled here. A third round was **not a usable board** — only 2 of 4 seats returned (gemini EMPTY, claude UNAVAILABLE), below the independence floor — so its verdict is not a pass. Its one substantive leg still found three real defects, all confirmed against the code and reconciled: a SIXTH credential consumer (capability discovery via `_get_server_env_metadata`), modern-envelope criteria that could pass over stdio while `/mcp` stayed broken, and the old HTTP client's redirect-following and client-ownership behaviour that 2.x hands back to the caller.

**Read the trend before trusting this document.** Round 1 found 5 defects, round 2 found 5 more on the reconciled draft, round 3 found 3 more. The findings shrank in structural severity — round 2 deleted a phase, round 3 only tightened criteria — but they have not stopped. The credential-consumer count alone went four → five → six across rounds. Treat any 'complete' enumeration here as a floor, not a ceiling, and grep before trusting a list.

**Service safety.** The gateway runs as a live systemd user service on `127.0.0.1:3344`. Every phase must leave it working; test on a spare port. After each phase merges, reinstall from PyPI and restart the unit before starting the next phase, so no phase is validated against a stale build.

---

## Verification

End-to-end, after the last phase merges:

```bash
# 1. Both first-party packages install clean from PyPI and start
uv venv /tmp/v11 && VIRTUAL_ENV=/tmp/v11 uv pip install pmcp pangram-mcp
VIRTUAL_ENV=/tmp/v11 uv pip list | grep '^mcp '        # expect 2.x
/tmp/v11/bin/pmcp --version
/tmp/v11/bin/python -c "import pmcp.server, pmcp.client.manager, pangram_mcp.server"

# 2. The gateway starts and serves (the check the unit suite cannot make)
/tmp/v11/bin/pmcp --transport http --host 127.0.0.1 --port 3399 -l info &
sleep 15 && ss -ltn | grep 3399                         # must be LISTENING

# 3. Version negotiation is real, per era
#    Downstream servers are reached via `initialize`, whose ceiling is
#    2025-11-25 — NO downstream server should ever report 2026-07-28.
#    gateway.health must show handshake-era values only (<= 2025-11-25).
#    The 2026-07-28 era is proven separately, upward, by EC-P2-5/EC-P2-7.

# 4. A downstream 1.x server still works through the 2.x gateway
#    gateway.connect_server firecrawl  → online
#    gateway.invoke firecrawl::firecrawl_scrape → returns content

# 5. server/discover advertises what the gateway actually accepts (verified in P2)
# 6. subscriptions/listen delivers a filtered notification and cancels cleanly (P3B)

# 7. Guards still green on both repos, unmodified
uv run pytest tests/ -q
```

The roadmap has delivered when: both packages run on `mcp` 2.x, clients negotiate `2026-07-28`, **every downstream server that worked before still works**, and no placeholder credential is required to reach a self-hosted endpoint.
