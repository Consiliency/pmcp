# Phase roadmap v5

## Context

PMCP's original progressive-disclosure goal included a CLI-first path: when PMCP
first exposes relevant capabilities, it should also tell the model when a local
CLI is the better invocation surface. The model can then use the normal shell
tool directly, as long as the compact CLI guidance remains in context, instead
of repeatedly routing simple operations back through PMCP MCP tools.

The current implementation has partial scaffolding but not the product behavior.
`CLIAlternative`, manifest `cli_alternatives`, `CLIResolution`, `use_cli`, CLI
probing, and a CLI-preferring manifest matcher exist. However,
`gateway.catalog_search` returns only MCP `CapabilityCard` results, and the
active `gateway.request_capability` handler probes CLIs without returning the
`use_cli` path. The result is that PMCP can know about local CLIs, but it does
not reliably expose compact, actionable CLI information at first capability
discovery.

## Architecture North Star

PMCP should remain the progressive gateway and discovery broker, not a wrapper
around every shell command. For simple local tasks where an installed CLI is the
right tool, PMCP should expose a compact, structured hint once and then let the
model use Bash/direct CLI calls. For tasks that need MCP-specific state,
structured tool schemas, remote APIs, auth brokering, or task lifecycle support,
PMCP should continue recommending MCP servers and `gateway.invoke`.

The CLI hint contract should be intentionally small: command name, availability,
path when known, description, help command, curated examples, and the reason it
was recommended. PMCP should not dump full `--help` output by default because
that would recreate the context bloat this feature is meant to avoid.

## Assumptions

- The "CLI" in this roadmap means native installed command-line tools such as
  `git`, `docker`, `jq`, `curl`, `python3`, and `node`, not a new general
  `pmcp invoke` CLI transport.
- Gateway tools keep their existing names and remain backward compatible through
  additive response fields.
- CLI availability is derived from `available_clis`, prior
  `gateway.sync_environment`, or bounded probing of manifest CLI alternatives.
- Curated CLI examples in the manifest are non-secret, deterministic, and small
  enough to include in discovery responses.
- `prefer_mcp_for` is the mechanism for suppressing CLI preference when an MCP
  server is more appropriate for a matching domain, such as GitHub issues or
  pull requests.

## Non-Goals

- Do not add a general `pmcp invoke <tool>` command in this roadmap.
- Do not stream or cache full CLI help output in discovery responses by default.
- Do not execute CLI commands on behalf of the model from PMCP.
- Do not replace `gateway.invoke` for downstream MCP tools.
- Do not require live external services or credentials for tests.
- Do not make CLI hints look like MCP tools invokable through `gateway.invoke`.

## Cross-Cutting Principles

- Compact first: responses must expose enough to act without dumping manuals.
- Structured first: agents should use fields such as `cli_hints`,
  `help_command`, `examples`, `available`, and `reason`, not parse prose.
- MCP stays authoritative for MCP tools: CLI hints must be separate from
  `CapabilityCard` results.
- Prefer CLI only when it is installed and appropriate; explicit server names and
  `prefer_mcp_for` phrases should preserve MCP recommendations.
- Centralize matching and hint construction so `catalog_search` and
  `request_capability` cannot drift.
- Keep tests at the real gateway handler layer, not only manifest helpers.

## Top Interface-Freeze Gates

- IF-0-CLIHINT-1 — PMCP has one compact, additive CLI hint response contract
  shared by capability discovery surfaces.
- IF-0-CLIMATCH-2 — CLI matching and preference logic is centralized,
  deterministic, availability-aware, and respects MCP preference overrides.
- IF-0-REQCLI-3 — `gateway.request_capability` returns actionable `use_cli`
  guidance when an installed CLI is the correct first choice.
- IF-0-CATALOGCLI-4 — `gateway.catalog_search` surfaces matching CLI hints
  during first tool discovery without mixing them into MCP tool cards.
- IF-0-CLISOAK-5 — Tests and docs prove the model can learn a direct CLI path
  from one PMCP MCP call while preserving the context-bloat boundary.

## Phases

### Phase 1 — CLI Hint Contract and Manifest Examples (CLIHINT)

**Objective**

Define the compact CLI hint model and enrich manifest CLI alternatives with
curated examples so all discovery surfaces can expose the same small,
actionable contract.

**Exit criteria**

- [ ] A dedicated CLI hint model exists with command name, description,
  availability, path, check command, help command, examples, preference metadata,
  and reason fields.
- [ ] `CLIAlternative` supports curated `examples` loaded from manifest YAML.
- [ ] Existing manifests include 2-4 compact examples for each built-in CLI
  alternative.
- [ ] Existing response schemas remain backward compatible through additive
  fields only.
- [ ] Unit tests cover manifest parsing of examples and default behavior for
  older manifest entries without examples.

**Scope notes**

- Likely lanes:
  - Type/model contract and serialization tests.
  - Manifest schema/data enrichment and parser tests.
- Prefer a single reusable model such as `CLIHint` over overloading
  `CLIResolution`, because catalog search needs hints without returning
  `status="use_cli"`.
- Keep examples generic and non-destructive, such as `git status --short`,
  `docker ps`, `jq '.items[]' file.json`, and `curl -I https://example.com`.

**Non-goals**

- Do not add query matching or gateway behavior in this phase beyond what is
  needed to serialize the new fields.
- Do not include full help output.

**Key files**

- `src/pmcp/types.py`
- `src/pmcp/manifest/loader.py`
- `src/pmcp/manifest/manifest.yaml`
- `tests/test_manifest.py`
- `tests/test_tools.py`

**Depends on**

- (none)

**Produces**

- IF-0-CLIHINT-1 — PMCP has one compact, additive CLI hint response contract
  shared by capability discovery surfaces.

### Phase 2 — Central CLI Matching and Hint Builder (CLIMATCH)

**Objective**

Centralize CLI matching, availability resolution, and hint construction so
`gateway.request_capability`, `gateway.catalog_search`, and future discovery
surfaces share one deterministic CLI preference implementation.

**Exit criteria**

- [ ] A shared helper returns ranked CLI hints for a query using name,
  description, keywords, examples, and `prefer_mcp_for`.
- [ ] The helper marks CLI hints as available only when the CLI is detected,
  provided through `available_clis`, or already cached from
  `gateway.sync_environment`.
- [ ] CLI path information is preserved when probe results provide it.
- [ ] `prefer_mcp_for` can suppress or downgrade an otherwise matching CLI hint
  with a structured reason.
- [ ] Tests prove installed CLIs are preferred for matching local tasks and MCP
  preference overrides win for configured phrases.

**Scope notes**

- Likely lanes:
  - Matching/scoring helper and probe-info plumbing.
  - Override semantics and regression tests.
- The existing `pmcp.manifest.matcher` can be reused or replaced, but the active
  gateway handlers must call the same logic.
- Store enough probe metadata in `GatewayTools` to include CLI paths without
  rerunning probes repeatedly.

**Non-goals**

- Do not change the public gateway tool list.
- Do not call an LLM or external service for matching.

**Key files**

- `src/pmcp/manifest/matcher.py`
- `src/pmcp/manifest/environment.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/types.py`
- `tests/test_manifest.py`
- `tests/test_tools.py`

**Depends on**

- IF-0-CLIHINT-1

**Produces**

- IF-0-CLIMATCH-2 — CLI matching and preference logic is centralized,
  deterministic, availability-aware, and respects MCP preference overrides.

### Phase 3 — Request Capability CLI Resolution (REQCLI)

**Objective**

Make `gateway.request_capability` return the modeled `use_cli` path when an
installed CLI is the best answer, while preserving existing MCP server
recommendations for explicit server requests and MCP-preferred domains.

**Exit criteria**

- [ ] `gateway.request_capability({"query": "git commits",
  "available_clis": ["git"]})` returns `status="use_cli"` with actionable CLI
  details.
- [ ] The `use_cli` response includes compact examples, help command,
  description, path when known, and a message that directs the model to use
  Bash/direct CLI.
- [ ] Explicit MCP server name matches continue to return server candidates.
- [ ] Queries matching `prefer_mcp_for`, such as GitHub issues or pull requests,
  do not incorrectly return `git` as the primary answer.
- [ ] Handler-level tests cover `available_clis`, cached `sync_environment`
  state, probe fallback, no-match behavior, and MCP override behavior.

**Scope notes**

- Likely lanes:
  - `request_capability` flow integration and response shape.
  - Handler-level tests for CLI win, MCP win, and no-match paths.
- Insert CLI resolution after explicit server name matching unless the phase plan
  finds a stronger compatibility reason to do it before name matching.
- Continue to avoid full CLI help output in normal responses.

**Non-goals**

- Do not provision or start MCP servers when returning `use_cli`.
- Do not execute the recommended CLI command.

**Key files**

- `src/pmcp/tools/handlers.py`
- `src/pmcp/types.py`
- `src/pmcp/manifest/matcher.py`
- `tests/test_tools.py`
- `README.md`

**Depends on**

- IF-0-CLIMATCH-2

**Produces**

- IF-0-REQCLI-3 — `gateway.request_capability` returns actionable `use_cli`
  guidance when an installed CLI is the correct first choice.

### Phase 4 — Catalog Search CLI Hints (CATALOGCLI)

**Objective**

Add compact CLI hints to `gateway.catalog_search` so the first tool browsing
response can expose direct CLI options alongside MCP tool cards without making
CLIs look like gateway-invokable tools.

**Exit criteria**

- [ ] `CatalogSearchOutput` includes an additive `cli_hints` field.
- [ ] `gateway.catalog_search({"query": "git"})` returns matching CLI hints when
  `git` is available or already provided through cached environment state.
- [ ] CLI hints remain separate from `results` and cannot be mistaken for
  `CapabilityCard` entries.
- [ ] Queryless catalog search does not add broad CLI noise unless a deliberately
  bounded default policy is documented and tested.
- [ ] Tests cover matching CLI hints, non-matching queries, offline catalog mode,
  and response compatibility when no hints exist.

**Scope notes**

- Likely lanes:
  - Output model and handler integration.
  - Catalog-specific tests and README progressive-disclosure docs.
- Start with query-only CLI hints to keep catalog responses compact.
- Keep `total_available` semantics tied to MCP tools unless an additive
  `total_cli_hints` field is explicitly introduced and documented.

**Non-goals**

- Do not add CLI hints to `CapabilityCard.results`.
- Do not change `gateway.describe`, which remains scoped to MCP tool IDs.

**Key files**

- `src/pmcp/types.py`
- `src/pmcp/tools/handlers.py`
- `tests/test_tools.py`
- `README.md`

**Depends on**

- IF-0-CLIMATCH-2

**Produces**

- IF-0-CATALOGCLI-4 — `gateway.catalog_search` surfaces matching CLI hints
  during first tool discovery without mixing them into MCP tool cards.

### Phase 5 — CLI Exposure Soak, Docs, and Release Gate (CLISOAK)

**Objective**

Prove and document the end-to-end CLI-first discovery behavior so PMCP's release
notes can accurately claim that models can learn direct CLI paths from one PMCP
MCP call.

**Exit criteria**

- [ ] README documents the intended flow: request capability or catalog search,
  use CLI directly when PMCP returns CLI guidance, otherwise provision/invoke MCP.
- [ ] Tests prove one PMCP MCP call returns enough CLI information for direct
  Bash usage without a second PMCP MCP call.
- [ ] Tests prove compactness boundaries: no full help dumps in normal
  discovery responses.
- [ ] Existing progressive-disclosure and offline catalog tests pass with the
  additive CLI fields.
- [ ] CHANGELOG records the CLI-first discovery behavior when release-bound.
- [ ] Full release verification passes before version bump or publish.

**Scope notes**

- Likely lanes:
  - End-to-end/contract tests across request and catalog discovery.
  - README/CHANGELOG/release closeout.
- Use deterministic fake CLI availability in tests instead of requiring host
  tools or live external services.
- The release claim should be precise: PMCP exposes native CLI hints for
  installed CLIs; it does not execute shell commands itself.

**Non-goals**

- Do not introduce unrelated gateway tools or CLI commands during soak.
- Do not require a live Docker daemon, cloud CLI credentials, or network access
  for CI.

**Key files**

- `tests/test_tools.py`
- `tests/test_manifest.py`
- `tests/test_phase4_e2e.py`
- `README.md`
- `CHANGELOG.md`
- `specs/phase-plans-v5.md`

**Depends on**

- IF-0-REQCLI-3
- IF-0-CATALOGCLI-4

**Produces**

- IF-0-CLISOAK-5 — Tests and docs prove the model can learn a direct CLI path
  from one PMCP MCP call while preserving the context-bloat boundary.

## Phase Dependency DAG

```text
CLIHINT -> CLIMATCH -> REQCLI ----\
                     \-> CATALOGCLI -> CLISOAK
```

## Execution Notes

- Phase 1 should run first because it freezes the additive response and manifest
  contract used everywhere else.
- Phase 2 should run after Phase 1 and before handler integration so the active
  gateway paths share one matching implementation.
- Phase 3 and Phase 4 can be planned after Phase 2. They can be implemented in
  parallel if ownership of `src/pmcp/tools/handlers.py` changes is coordinated
  carefully; otherwise run them serially to avoid response-shape conflicts.
- Phase 5 is the release gate. It should not introduce new runtime contracts
  unless tests expose a spec gap that must feed back into earlier phases.
- Suggested first next command:
  `codex-plan-phase specs/phase-plans-v5.md Phase 1`

## Verification

Run these after implementation phases, not during roadmap planning:

```bash
uv run pytest tests/test_manifest.py -k "cli or manifest or keyword"
uv run pytest tests/test_tools.py -k "request_capability or catalog_search or sync_environment or cli"
uv run pytest tests/test_phase4_e2e.py -k "catalog or capability or cli"
```

Whole-roadmap release verification:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest -q
uv build
```
