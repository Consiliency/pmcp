---
phase_loop_plan_version: 1
phase: P5
roadmap: specs/phase-plans-v11.md
roadmap_sha256: 2f03c6f3c01d903e55b87bdbe4ca8b9b25fcbb318a48a30161d35cd6b76b3be0
---

# P5: Manifest credential optionality

## Context

A manifest entry declares `requires_api_key: true` unconditionally
(`src/pmcp/manifest/loader.py:49`, parsed at `:423`). Servers that support
self-hosting need a credential for the vendor endpoint and none for a
self-hosted one, but the manifest cannot say so. The only way through is a
placeholder secret (`FIRECRAWL_API_KEY=self-hosted-no-auth`) that later reads
as a real credential to whoever finds it. Tracked as Consiliency/pmcp#114.

### The enforcement surface — enumerated by grep, not memory

**Counting method (stated so the count stops moving).** The consumer count
drifted 4 → 5 → 6 → 7 across successive reviews because each round grepped a
single token. Two sweeps are required, because the seventh consumer contains no
occurrence of `requires_api_key` at all:

- **Sweep 1 — `requires_api_key`.** `rg -c requires_api_key src/**/*.py` = **26**
  textual hits (20 in `handlers.py`). Classified by AST rather than by eye: **10**
  `ast.Attribute` reads in `Load` context, **8** `ast.keyword` writes, **3**
  field/dict-key definitions, remainder comments. The 10 reads are the gates.
- **Sweep 2 — `env_var` used as a credential-instruction gate.** AST-walk every
  `ast.If` whose test mentions `env_var` but not `requires_api_key`: **28** hits.
  Almost all concern remote-header `missing_env_vars` (a different mechanism) or
  `auth_connect` argument validation. Exactly one instructs an operator to supply
  a manifest-declared credential: `run_init` at `src/pmcp/cli.py:1528`.

Deliberately examined and **excluded** from the consumer list, so the next
reviewer does not rediscover them as an eighth: `handlers.py:4457-4507`
(`auth_connect` — the remedy path, which must keep working when relaxed, and does,
because IF-0-P5-3 preserves `env_var`); `config/loader.py:636` and `:960`
(credential *resolution* into config env — when relaxed they simply find nothing
and leave the var unset, which is correct); `cli_commands/doctor.py:112` and the
`missing_env_vars` sites (remote-header auth, an unrelated mechanism).

**Seven independent gate/report consumers.** Every one must be converted, or the
omitted path keeps demanding the placeholder while the others are fixed:

| # | Consumer | Site | What it does when unconverted |
|---|---|---|---|
| 1 | eager startup | `src/pmcp/config/loader.py:1038` | server is skipped with `StartupSkipReason.MISSING_AUTH`; never auto-starts |
| 2 | install / provision preflight | `src/pmcp/manifest/installer.py:547` (`check_api_key`) | raises `MissingApiKeyError` before install |
| 3 | lifecycle connect | `src/pmcp/tools/handlers.py:3200` | `gateway.connect_server` returns `auth_state="missing_auth"` — the exact failure in #114 |
| 4 | provisioning | `src/pmcp/tools/handlers.py:4058` | `gateway.provision` returns `needs_api_key=True` |
| 5 | diagnostics | `src/pmcp/cli_commands/secrets.py:95` | `pmcp secrets check` reports the credential var as missing |
| 6 | capability discovery | `src/pmcp/tools/handlers.py:3365` (`_get_server_env_metadata`) | consumed at `:1403` (`gateway.catalog_search` candidates), `:3643`/`:3661` (name-match message), `:3729`/`:3743` + tiering at `:3759`/`:3768`/`:3770`/`:3775`, `:3840`/`:3861` — makes `gateway.request_capability` report the server as key-missing and advise `gateway.auth_connect` |
| 7 | `pmcp init` | `src/pmcp/cli.py:1528` (`run_init`) | gates on `if server.env_var:` — **not** on `requires_api_key`, which is why six rounds of grepping this token never found it. Because IF-0-P5-3 deliberately preserves `env_var` when relaxed, a self-hosted operator is still told to run `pmcp secrets set FIRECRAWL_API_KEY …` — the obsolete instruction that #114 exists to delete |

Consumers 1–6 match the roadmap's list. Consumer 7 is new to this plan.
Consumer 6 is a single function feeding nine downstream sites, so converting it
is one edit that fixes all nine — that leverage is why it is a lane-3 task, not
nine tasks.

Note the interaction that produced consumer 7: preserving `env_var` when relaxed
is correct for `auth_connect` (an operator can still add a real key later), but
any site keying off `env_var` alone inherits the stale instruction. Sweep 2 above
is what bounds that class.

**Four producers (keyword *writes*) that must NOT change.** These construct
`requires_api_key` rather than read it, and none of them has a manifest entry
behind it, so relaxation does not apply:

- `src/pmcp/manifest/loader.py:423` — the manifest parse (extended by SL-1).
- `src/pmcp/tools/handlers.py:2977` — MCP-Registry candidate card, derived from
  the registry entry's own `env_vars`/`headers`. No `ServerConfig` exists.
- `src/pmcp/tools/handlers.py:3336` — synthesized entry for plain `.mcp.json`
  configured servers; hardcoded `False`.
- `src/pmcp/tools/handlers.py:4954`/`:4979` — `register_discovered_server`
  derives it from the parsed package's env vars.

`src/pmcp/types.py:1020` is the `CapabilityCandidate.requires_api_key` **wire
field**, not a gate. It is deliberately untouched; SL-3 feeds it the effective
value instead of the declared one.

### Existing machinery this design reuses

- `credential_storage_key(server)` / `credential_lookup_keys(server)`
  (`src/pmcp/manifest/loader.py:82`, `:96`) are duck-typed over "any object with
  `secret_key`/`env_var`" — they already serve both manifest `ServerConfig` and
  `self._discovered_server_configs` entries. The new predicate follows that
  contract exactly, so gates 3 and 4 keep working for discovered servers
  (which get the field's `[]` default and therefore today's behaviour).
- `ServerConfig.extra_env` (`:59`) holds manifest-declared non-secret vars, and
  an overlay's `server_env:` patch key merges into it (`:644`). This is where a
  self-hosted base URL already lands, so it is the natural place for the
  predicate to look.
- **`build_install_child_env`** (`src/pmcp/manifest/installer.py:509`) — the
  real symbol; there is no `build_server_env` in this repo. It returns
  `sanitized_subprocess_env({**extra_env, **credential})`, i.e. it already
  performs the sanitization, so callers must NOT wrap it again. Used by
  `installer.py:136`/`:609`/`:653` and `handlers.py:4814`.
- There are **two** env-construction paths and `config/loader.py:951` says so in
  a comment. Path A is `build_install_child_env` (install / provision / update).
  Path B is `_manifest_server_to_config` (`config/loader.py:956`), which seeds
  `env` from `extra_env` and hands it to `LocalMcpServerConfig.env`, which
  `manager.py:1277` passes through `sanitized_subprocess_env`. Path B is what
  every restart, refresh, and lazy reconnect goes through. **Any invariant about
  the child environment must be asserted on BOTH**, or it proves the thing works
  on first install and silently regresses on every subsequent start.

### The gate-relaxes-but-child-starves inversion (drives the IF-0-P5-1 signature)

An earlier draft had the predicate read `os.environ` as well as `extra_env`.
That is **unsafe**, and the failure is the exact inverse of the phase's intent:

1. `main()` loads the PMCP secret stores into `os.environ`
   (`src/pmcp/cli.py:2617-2619`, `load_dotenv(~/.config/pmcp/pmcp.env)`).
2. So an operator who runs `pmcp secrets set FIRECRAWL_API_URL http://host:3002`
   puts that key in `os.environ`, and an `os.environ`-reading predicate relaxes.
3. But `sanitized_subprocess_env` (`src/pmcp/env_store.py:139-141`) does
   `env.pop(key, None)` for **every** key in `managed_secret_keys()` — which is
   literally "every key in the PMCP stores", including that URL — before the
   child is spawned (`src/pmcp/client/manager.py:1277`, the stdio path).
4. Only `own_env` (`extra_env` + the resolved credential) is re-applied after
   the strip.

Net effect: the gate opens, the child never receives `FIRECRAWL_API_URL`, and
`firecrawl-mcp` falls back to the **vendor** endpoint with **no credential**.
PMCP would have relaxed a gate specifically so a server could reach an
unauthenticated vendor API. Security-relevant, and invisible to any test that
only asserts the gate opened.

**Resolution: the predicate reads `extra_env` only — never `os.environ`.**
`extra_env` is exactly what becomes `own_env`, and `own_env` is applied *after*
the strip, so "the gate relaxed" ⟺ "the child receives the variable" becomes a
structural invariant rather than a runtime coincidence. Cost: a bare
`export FIRECRAWL_API_URL=…` in the operator's shell no longer relaxes the gate;
the operator must declare it in the manifest entry's `extra_env` or an overlay
`server_env:` patch. That is fail-closed and it is the behaviour we want — a
shell export is invisible to the manifest and unauditable.

Rejected alternative: have the predicate call `build_install_child_env` to evaluate the
real child environment. It is circular (`installer.py` imports from
`manifest/loader.py`), it makes a pure predicate do filesystem I/O via
`managed_secret_keys`, and it buys nothing over restricting the source.

### The configured-duplicate override (second route to the same bad end state)

`extra_env` is not always what the child receives. A server listed in
`.mcp.json` that duplicates a manifest entry goes through
`_merge_manifest_defaults`, which adds a manifest `extra_env` key **only when
the configured env does not already have it** (`src/pmcp/config/loader.py:616-634`,
`if key not in (merged.env or {})`). The comment is explicit that this is
deliberate: "A value the config already sets wins — an explicit user entry is a
genuine override." It shipped in Consiliency/pmcp#109 and **must not be changed
here.**

Failing scenario: `.mcp.json` sets `FIRECRAWL_API_URL` to `""` or to a dead
`${FIRECRAWL_API_URL}` placeholder. A predicate reading the *manifest's*
`extra_env` relaxes, but the child receives the configured placeholder — so the
server has neither a working self-hosted URL nor a credential, and calls the
**vendor** endpoint unauthenticated. That is the same end state as the env-strip
inversion, reached by a different route.

Reconciliation is at the predicate, not at the merge: the relaxer is judged
against the value the child will actually receive (`child_env`, clause 2a), and
placeholder/empty values are not usable (clause 4). Every consumer holding a
`ResolvedServerConfig` passes `child_env=resolved.config.env`; the manifest-only
consumers pass nothing and get `extra_env`, which is identical for them.
- `_parse_server_config` reads every key with `.get(...)`, so an unknown or
  misspelled field is silently ignored — a typo in the new field therefore
  **fails closed** (credential stays required).

### Design decision — the explicit signal

Adopt #114 option (1): a manifest field naming the variables whose presence
makes the credential optional.

```yaml
firecrawl:
  requires_api_key: true
  env_var: "FIRECRAWL_API_KEY"
  api_key_optional_when: ["FIRECRAWL_API_URL"]
```

Chosen over option (2) (overlay-level relaxation) and option (3) (infer from a
URL-shaped override, explicitly rejected by #114 as a heuristic) because:

- It requires **two independent parties to agree**: the manifest author states
  the server supports keyless self-hosting and names the deciding variable; the
  operator supplies the variable. An operator alone cannot relax a gate for a
  server whose entry never declared it relaxable. Option (2) grants that
  unilaterally.
- It is a named variable, not a shape test, so a self-hosted deployment that
  *does* require auth is unaffected — the operator simply does not set the var
  (or the entry never declares one).
- Option (2) is unnecessary: overlay `servers:` is whole-entry replace, so an
  operator who genuinely needs a local-only relaxation can already restate the
  entry with the field. Adding a second narrow patch key is vocabulary without
  new capability. Recorded as a non-goal, not a gap.

## Interface Freeze Gates

- [ ] IF-0-P5-1 — `src/pmcp/manifest/loader.py` exports the credential
  predicate and its result type, frozen day 1 and consumed unchanged by SL-2
  and SL-3:

  ```python
  @dataclass(frozen=True)
  class CredentialRequirement:
      required: bool          # effective requirement after relaxation
      declared: bool          # server.requires_api_key as shipped
      relaxed_by: str | None  # extra_env var name that relaxed it, else None

  def credential_requirement(
      server: Any, *, child_env: Mapping[str, str] | None = None
  ) -> CredentialRequirement: ...

  def requires_credential(
      server: Any, *, child_env: Mapping[str, str] | None = None
  ) -> bool: ...
  ```

  Frozen semantics (all seven consumers depend on these being identical):
  1. `server` is duck-typed on `requires_api_key`, `env_var`, `secret_key`,
     `extra_env`, `api_key_optional_when` — accepts manifest `ServerConfig`,
     discovered-server configs, and `None` (returns not-required).
  2. **The predicate never reads `os.environ`,** the secret store, per-request
     arguments, or a URL's shape. See the inversion analysis above — this is the
     clause that keeps gate-relaxation and child-environment in lockstep, and it
     must not be relaxed for testing convenience.
  2a. `child_env`, when given, is the **post-merge environment the child will
     actually receive** — i.e. `ResolvedServerConfig.config.env` after
     `_merge_manifest_defaults` — and nothing else. **Passing `os.environ` is a
     contract violation** and the SL-4.1 guard rejects it textually. When
     `child_env` is `None` the source is `server.extra_env` (the manifest-only
     path, where the two are identical by construction).
  2b. Every caller that can be looking at a **configured duplicate** MUST pass
     `child_env`. See the configured-duplicate override analysis below.
  3. `declared is False` ⇒ `required is False`, `relaxed_by is None`. No other
     branch runs.
  4. Otherwise, let `source` be `child_env` when given, else `server.extra_env`.
     For each name in `api_key_optional_when` in declared order, the first name
     whose `source[name]` is **usable** yields `required=False,
     relaxed_by=<name>`. A value is usable when it is present, non-empty after
     `.strip()`, and **not an unexpanded `${VAR}`/`$VAR` placeholder** — local
     stdio env is passed verbatim with no expansion (`config/loader.py:640-644`
     documents this for credentials), so a placeholder reaches the child as a
     dead literal and must not relax the gate.
  5. **Self-relaxation is impossible**: a name equal to the server's own
     `env_var` or `secret_key` is ignored by the predicate *and* dropped with a
     warning at parse time.
  6. No list, empty list, name absent from the source, empty value, or
     placeholder value ⇒ `required=True`. Fails closed.

- [ ] IF-0-P5-4 — **Child-environment consistency invariant.** For any server,
  `credential_requirement(server).relaxed_by is not None` implies that name is
  present with the same non-empty value in
  **all three** child-env constructions, asserted against the real symbols with
  their real signatures: path A `build_install_child_env(server)`; path B
  `sanitized_subprocess_env(_manifest_server_to_config(server, env_lookup).config.env)`
  — note `_manifest_server_to_config` is `(server, env_lookup: Callable[[str],
  str | None])` (`config/loader.py:920-923`) and **both arguments are required**;
  pass `{}.get` when the test wants no credential resolved. Path C is the
  configured-duplicate merge, `_merge_manifest_defaults`, where a configured
  value overrides the manifest's. `build_install_child_env` already sanitizes
  internally — do not wrap it. This holds structurally because `extra_env` seeds `own_env`, which
  `sanitized_subprocess_env` applies *after* its `managed_secret_keys` strip —
  but it is asserted as a regression test (SL-4.6), not left to inspection,
  because it is the property that fails silently and dangerously if a future
  refactor reorders the strip or reintroduces an `os.environ` source.

- [ ] IF-0-P5-2 — `ServerConfig.api_key_optional_when: list[str]` (default
  `field(default_factory=list)`), parsed by `_parse_server_config` from the
  optional `api_key_optional_when:` YAML key. Non-list value ⇒ `[]` + warning;
  non-string members dropped; members equal to `env_var`/`secret_key` dropped
  with a warning. Absent key ⇒ `[]` ⇒ today's behaviour byte-for-byte.

- [ ] IF-0-P5-3 — `_get_server_env_metadata`
  (`src/pmcp/tools/handlers.py:3355`) keeps its `(bool, str | None, str | None)`
  return shape and its signature. Its first element becomes the **effective**
  requirement (`requires_credential(manifest_server)`); `env_var` and
  `env_instructions` are returned **unchanged** even when relaxed, so
  `gateway.auth_connect` still works for an operator who later wants a real
  key. All nine downstream sites are unmodified by this change.

## Lane Index & Dependencies

SL-1 — Predicate and manifest field (preamble)
  Depends on: (none)
  Blocks: SL-2, SL-3, SL-4, SL-5
  Parallel-safe: no

SL-2 — Startup, install, diagnostics, and `pmcp init` gates
  Depends on: SL-1
  Blocks: SL-4
  Parallel-safe: yes

SL-3 — Handler gates and capability discovery
  Depends on: SL-1
  Blocks: SL-4
  Parallel-safe: yes

SL-4 — Shipped manifest data, centralization guard, end-to-end and live-boot verification
  Depends on: SL-2, SL-3
  Blocks: SL-5
  Parallel-safe: no

SL-5 — Documentation & spec reconciliation (SL-docs)
  Depends on: SL-1, SL-2, SL-3, SL-4
  Parallel-safe: no

## Lanes

### SL-1 — Predicate and manifest field (preamble)

- **Scope**: Add `api_key_optional_when` to `ServerConfig` and publish the one
  shared credential predicate that every enforcement path will call.
- **Owned files**: `src/pmcp/manifest/loader.py`, `tests/test_credential_requirement.py`
- **Interfaces provided**: `CredentialRequirement`, `credential_requirement`, `requires_credential`, `ServerConfig.api_key_optional_when`
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (preamble — SL-2, SL-3, SL-4 all import from it)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/test_credential_requirement.py` | IF-0-P5-1 clauses 1–6 and IF-0-P5-2 parse rules: declared-false short circuit, `extra_env`-supplied relax, **`monkeypatch.setenv` of the relaxer does NOT relax when `extra_env` lacks it** (clause 2 — the inversion guard), empty-string and whitespace-only `extra_env` values rejected, self-relax via `env_var` ignored, self-relax via `secret_key` ignored, absent field ⇒ required, non-list ⇒ `[]`, non-string members dropped, `None` server, duck-typed object without `api_key_optional_when`, `credential_requirement` signature accepts no `env` kwarg | `uv run pytest tests/test_credential_requirement.py` |
| SL-1.2 | impl | SL-1.1 | `src/pmcp/manifest/loader.py` | — | — |
| SL-1.3 | verify | SL-1.2 | `src/pmcp/manifest/loader.py`, `tests/test_credential_requirement.py` | all SL-1 tests | `uv run pytest tests/test_credential_requirement.py && uv run ruff check src/pmcp/manifest/loader.py && uv run mypy src/pmcp` |

### SL-2 — Startup, install, diagnostics, and `pmcp init` gates

- **Scope**: Convert consumers 1, 2, 5, and 7 to the predicate — the four gates
  that live outside `handlers.py`.
- **Owned files**: `src/pmcp/config/loader.py`, `src/pmcp/manifest/installer.py`, `src/pmcp/cli_commands/secrets.py`, `src/pmcp/cli.py`, `tests/test_credential_gates_startup.py`
- **Interfaces provided**: (none)
- **Interfaces consumed**: `requires_credential` from `src/pmcp/manifest/loader.py` (SL-1)
- **Parallel-safe**: yes (disjoint from SL-3; both depend only on SL-1)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/test_credential_gates_startup.py` | Per gate, both directions: relaxed server passes, genuinely-required server still fails closed. Gate 1 — `resolve_startup_config` places a relaxed eager server in `eager_configs` and a required one in `skipped` with `StartupSkipReason.MISSING_AUTH`. Gate 2 — `check_api_key` returns for relaxed, raises `MissingApiKeyError` for required. Gate 5 — assert on the **returned `output["ok"]`**, never on process exit status: `cli.py:2578` only prints the result and never maps `ok` to exit code, so an exit-status assertion cannot detect this failure. `run_secrets_check` must return `ok is True` for a relaxed server, with neither the credential var nor the relaxer key in `missing_keys`; a required server still returns `ok is False` listing the credential var. Gate 7 — `run_init` on a relaxed server emits no `pmcp secrets set` instruction and names the relaxer instead; on a required server the existing instruction is unchanged byte-for-byte | `uv run pytest tests/test_credential_gates_startup.py` |
| SL-2.2 | impl | SL-2.1 | `src/pmcp/config/loader.py` | — | — |
| SL-2.3 | impl | SL-2.1 | `src/pmcp/manifest/installer.py` | — | — |
| SL-2.4 | impl | SL-2.1 | `src/pmcp/cli_commands/secrets.py` | — | — |
| SL-2.5 | impl | SL-2.1 | `src/pmcp/cli.py` | — | — |
| SL-2.6 | verify | SL-2.5 | `src/pmcp/config/loader.py`, `src/pmcp/manifest/installer.py`, `src/pmcp/cli_commands/secrets.py`, `src/pmcp/cli.py` | all SL-2 tests | `uv run pytest tests/test_credential_gates_startup.py tests/test_config_loader.py tests/test_startup_resolver.py tests/test_install_command.py tests/test_cli.py && uv run ruff check . && uv run mypy src/pmcp` |

Conversion detail (all three are one-line substitutions; keep the surrounding
`credential_lookup_keys` availability logic untouched):

- `config/loader.py:1038` — `manifest_server.requires_api_key` →
  `requires_credential(manifest_server, child_env=_local_env(config))`, where
  `_local_env` returns `config.config.env` for a `LocalMcpServerConfig` and
  `None` otherwise. **This call site sees configured duplicates**, so per clause
  2b it must pass `child_env` — omitting it is the override bug.
- `installer.py:547` — `if not server_config.requires_api_key:` →
  `if not requires_credential(server_config):`.
- `secrets.py:95` — **two changes, not one.** The one-line predicate swap is
  necessary but leaves consumer 5 functionally broken:
  1. `manifest_server.requires_api_key` →
     `requires_credential(manifest_server, child_env=cfg.config.env)`. This call
     site iterates configured servers, so per clause 2b it must pass `child_env`.
     When relaxed, `credential_var` stays `None`, so the credential key is never
     added to `server_keys`.
  2. **The relaxer itself is then reported missing, flipping `ok` to false.**
     `_manifest_server_to_config` merges `extra_env` into the resolved config env
     (`config/loader.py:617` for configured servers, `:956` for manifest ones),
     and the `env_map` loop at `secrets.py:105` adds **every** key in that env to
     `required_keys` unconditionally. Satisfaction is then checked only against
     secret-store values (`secrets.py:254`, `_satisfied` reads `effective`,
     which is the user+project env files) — so the literal overlay URL is
     reported missing and `ok` becomes `False`. Fix: in the `env_map` loop, skip
     keys whose value came from the manifest server's `extra_env` (same key,
     same literal value) — those are manifest-supplied non-secrets, not
     credentials the operator must store. Keep requiring `${VAR}` references
     found by `ENV_REF_PATTERN`; those are genuine indirections and are handled
     by a separate branch.
- `cli.py:1528` — `if server.env_var:` → `if requires_credential(server) and
  server.env_var:`, with an `elif server.env_var:` branch printing the relaxed
  form (name the relaxer via `credential_requirement(server).relaxed_by`, e.g.
  "no credential needed — FIRECRAWL_API_URL selects a self-hosted endpoint").
  Do **not** simply drop the branch: silence would read as "PMCP forgot", and
  the operator needs to see that the relaxation was recognised.

### SL-3 — Handler gates and capability discovery

- **Scope**: Convert consumers 3, 4, and 6 — the two `handlers.py` gates and the
  discovery-metadata function that feeds nine reporting sites.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_credential_gates_handlers.py`
- **Interfaces provided**: IF-0-P5-3 (`_get_server_env_metadata` effective-value contract)
- **Interfaces consumed**: `requires_credential` from `src/pmcp/manifest/loader.py` (SL-1)
- **Parallel-safe**: yes (single-writer on `handlers.py`; disjoint from SL-2)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-3.1 | test | — | `tests/test_credential_gates_handlers.py` | Gate 3 — `gateway.connect_server` on a relaxed server does not return `auth_state="missing_auth"`; on a required server it still does, with `next_step` naming `gateway.auth_connect`. Gate 4 — `gateway.provision` on a relaxed server does not return `needs_api_key=True`; required server still does. Gate 6 — `gateway.catalog_search` candidate for a relaxed server has `requires_api_key is False`; `gateway.request_capability` name-match message says "No API key required" and its `recommendation` contains no `auth_connect`; category tiering sorts the relaxed server into the no-key-required group; required servers keep all three behaviours. Producer invariants — a registry candidate (`:2977`), a synthesized `.mcp.json` entry (`:3336`), and a `register_discovered_server` entry (`:4979`) each report unchanged values | `uv run pytest tests/test_credential_gates_handlers.py` |
| SL-3.2 | impl | SL-3.1 | `src/pmcp/tools/handlers.py` | — | — |
| SL-3.3 | verify | SL-3.2 | `src/pmcp/tools/handlers.py` | all SL-3 tests | `uv run pytest tests/test_credential_gates_handlers.py tests/test_tools.py tests/test_lazy_start.py tests/test_offline_discovery.py && uv run ruff check . && uv run mypy src/pmcp` |

Conversion detail — exactly three reads change in this file:

- `:3200` — `if server_config.requires_api_key and server_config.env_var:` →
  `if requires_credential(server_config) and server_config.env_var:`.
- `:4058` — same substitution.
- `:3365` — return `requires_credential(manifest_server)` as the tuple's first
  element; leave `env_var` and `env_instructions` unchanged per IF-0-P5-3.

Consumers 3, 4, and 6 resolve a **manifest** `ServerConfig` (or a discovered
one) and never see a configured duplicate at these sites, so they call
`requires_credential(server_config)` with no `child_env` — clause 2a makes that
identical to `extra_env`. The same holds for consumer 2 (`check_api_key`) and
consumer 7 (`run_init`) in SL-2. Only consumers 1 and 5 iterate configured
servers, and only those two pass `child_env`.

Do **not** edit `:2977`, `:3336`, `:4954`, or `:4979` — those are producers, and
`src/pmcp/types.py` is not in this lane's scope.

### SL-4 — Shipped manifest data, centralization guard, end-to-end and live-boot verification

- **Scope**: Ship the `firecrawl` entry that closes #114, add the guard test
  that fails if a seventh `requires_api_key` gate ever appears outside the
  predicate, prove the full seven-consumer path end to end in both directions,
  prove IF-0-P5-4 on a live child process, and reconcile any pre-existing test
  that the conversion disturbed.
- **Owned files**: `src/pmcp/manifest/manifest.yaml`, `tests/test_credential_predicate_guard.py`, `tests/test_credential_optionality_e2e.py`, `tests/test_credential_child_env.py`, `tests/test_credential_boot.py`, `tests/test_manifest.py`, `tests/test_manifest_provision.py`, `tests/test_manifest_overlay.py`, `tests/test_config_loader.py`, `tests/test_startup_resolver.py`, `tests/test_tools.py`, `tests/test_install_command.py`, `tests/test_integration.py`, `tests/test_offline_discovery.py`, `tests/test_lazy_start.py`, `tests/test_provision_validation.py`, `tests/test_phase4_e2e.py`, `tests/test_cli.py`
- **Interfaces provided**: (none)
- **Interfaces consumed**: `requires_credential`, `CredentialRequirement`, `credential_requirement().relaxed_by` (SL-1); converted gates in `src/pmcp/config/loader.py`, `src/pmcp/manifest/installer.py`, `src/pmcp/cli_commands/secrets.py`, `src/pmcp/cli.py` (SL-2); converted gates and `_get_server_env_metadata` in `src/pmcp/tools/handlers.py` (SL-3); `sanitized_subprocess_env` (`src/pmcp/env_store.py:124`), `build_install_child_env` (`src/pmcp/manifest/installer.py:509`), and `_manifest_server_to_config` (`src/pmcp/config/loader.py:956`), all read-only
- **Parallel-safe**: no (reducer — verifies the union of SL-2 and SL-3)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-4.1 | test | — | `tests/test_credential_predicate_guard.py` | Centralization guard, four checks — see the guard spec below the table | `uv run pytest tests/test_credential_predicate_guard.py` |
| SL-4.2 | test | — | `tests/test_credential_optionality_e2e.py` | Relaxed direction (EC-P5-3): manifest fixture whose `firecrawl` entry carries `api_key_optional_when: ["FIRECRAWL_API_URL"]`, an overlay `server_env` supplying that URL, and **no** `FIRECRAWL_API_KEY` anywhere. Assert **positive outcomes**, not merely the absence of two error markers: eager startup yields a `ResolvedServerConfig` in `eager_configs` and `skipped` contains no entry for the server; `check_api_key` returns `None`; connect returns `ok is True` with `auth_state` not in `{"missing_auth", "policy_denied"}`; provision returns `ok is True` and `needs_api_key is False`; `run_secrets_check` returns `output["ok"] is True` (NOT exit status — `cli.py:2578` never maps `ok` to it) with both `FIRECRAWL_API_KEY` and `FIRECRAWL_API_URL` absent from `missing_keys`; `catalog_search` candidate has `requires_api_key is False` and sorts into the no-key tier; `request_capability` message and recommendation contain no `auth_connect`; `run_init` emits no `pmcp secrets set`. Fail-closed direction (EC-P5-2): URL unset; a second entry with no `api_key_optional_when`; a third naming its own `FIRECRAWL_API_KEY` as relaxer; a fourth whose relaxer is set **only** in `os.environ` — all four still fail closed at all seven gates | `uv run pytest tests/test_credential_optionality_e2e.py` |
| SL-4.3 | test | — | `tests/test_credential_optionality_e2e.py` | Shipped-manifest assertion (not the fixture): `load_manifest().get_server("firecrawl").api_key_optional_when == ["FIRECRAWL_API_URL"]`, and `requires_credential` on the shipped entry is `True` with an empty `extra_env`. Proves the line ships, and that shipping it relaxes nothing on its own | `uv run pytest tests/test_credential_optionality_e2e.py -k shipped` |
| SL-4.4 | impl | SL-4.3 | `src/pmcp/manifest/manifest.yaml` | — | — |
| SL-4.5 | impl | SL-4.1 | `tests/test_manifest.py`, `tests/test_manifest_provision.py`, `tests/test_manifest_overlay.py`, `tests/test_config_loader.py`, `tests/test_startup_resolver.py`, `tests/test_tools.py`, `tests/test_install_command.py`, `tests/test_integration.py`, `tests/test_offline_discovery.py`, `tests/test_lazy_start.py`, `tests/test_provision_validation.py`, `tests/test_phase4_e2e.py`, `tests/test_cli.py` | — | — |
| SL-4.6 | test | — | `tests/test_credential_child_env.py` | IF-0-P5-4 regression, parametrized over **three** env-construction paths using the real symbols with their real signatures — path A `build_install_child_env(cfg)` (already sanitized; do not wrap); path B `sanitized_subprocess_env(_manifest_server_to_config(cfg, {}.get).config.env)` (**two required args**, `config/loader.py:920-923` — calling it with `cfg` alone raises `TypeError` before asserting anything); path C the configured-duplicate merge via `_merge_manifest_defaults`. For a relaxed server assert `credential_requirement(cfg, child_env=...).relaxed_by` is present with an identical usable value in each. Path C must additionally cover the **override** cases: `.mcp.json` setting the relaxer to `""` and to `"${FIRECRAWL_API_URL}"` — in both, the merge keeps the configured value, the child gets a dead literal, and the predicate MUST report `required=True`. A first assertion imports all three symbols by name and calls each with its real arity, so a rename or signature change breaks the test loudly instead of it silently testing nothing. Run it with the relaxer **also** registered as a PMCP-managed secret key (write it into a tmp `pmcp.env` via `monkeypatch`), which is the configuration that strips it — this test fails on the pre-fix `os.environ`-reading predicate and on any future refactor that re-orders the `managed_secret_keys` strip after `own_env` | `uv run pytest tests/test_credential_child_env.py` |
| SL-4.7 | verify | SL-4.6 | `src/pmcp/**`, `tests/**` | full suite + live boot | `uv run pytest && uv run ruff check . && uv run mypy src/pmcp`, then the spare-port boot check below |

**Guard spec (SL-4.1)** — the naive "only `credential_requirement` may read the
attribute" rule **cannot pass**, because four intentionally-preserved reads of
the *wire* field survive at `handlers.py:3759`, `:3768`, `:3770`, `:3775`
(`c.requires_api_key` on a `CapabilityCandidate`, the no-key/key-ready/key-missing
tiering). AST cannot distinguish those from a manifest read by type. Four checks:

1. **Manifest reads.** Every `ast.Attribute` load of `requires_api_key` under
   `src/pmcp/` must have an enclosing function qualname in an explicit
   allowlist: `credential_requirement` (`manifest/loader.py`) plus the four
   wire-field consumers, allowlisted **by enclosing function name**, not line
   number, so the list survives reformatting. The allowlist is a module-level
   constant with a comment explaining each entry; adding to it requires editing
   the test, which is the point.
1a. **`child_env` misuse.** Flag any call passing `os.environ` (or
   `os.environ.copy()`) as `child_env` to `credential_requirement` /
   `requires_credential` anywhere under `src/pmcp/`. Clause 2a forbids it, and
   it silently reintroduces the env-strip inversion.
2. **Dynamic reads.** Flag `getattr(x, "requires_api_key", …)`,
   `x["requires_api_key"]`, and `x.get("requires_api_key")` anywhere under
   `src/pmcp/` outside `manifest/loader.py`. This closes the string-literal
   bypass that defeats check 1.
3. **Allowlist liveness.** Assert every allowlisted function still exists and
   still contains a matching read, so a stale entry cannot silently widen the
   guard after the code it named is deleted.

Known residual: a read reached through a variable alias
(`f = operator.attrgetter("requires_api_key")`) evades all three. The guard is a
tripwire for the realistic regression — a new `if cfg.requires_api_key:` gate —
not a proof. That is why every gate also carries a behavioural fail-closed test
in SL-2/SL-3 and why EC-P5-2 is proven by those, not by the guard.

SL-4.4 adds one line to the `firecrawl` entry at `src/pmcp/manifest/manifest.yaml:603-605`:
`api_key_optional_when: ["FIRECRAWL_API_URL"]`. This relaxes nothing until an
operator supplies the URL via `extra_env`/`server_env`, so shipping it is inert
for every existing install — SL-4.3 asserts exactly that. No other manifest
entry is touched this phase.

**Spare-port boot check (SL-4.7)** — a unit-green gateway may still fail to
boot, so acceptance is not in-process only. **Isolation recipe copied verbatim
from P6CLEAN's verified fix (`cbe98be`); do not re-derive it.** My two earlier
attempts were both wrong: `--config` alone is insufficient (the shipped manifest
layers additively), and redirected `HOME` alone is ALSO insufficient —
`load_manifest()` starts from the package-shipped `manifest.yaml`
(`src/pmcp/manifest/loader.py:601-602`), so a probe under a redirected `HOME`
still resolved 106 lazy servers into the kill set. Every constraint below is
mandatory:

- **`--policy` is the real isolation layer.** It is the only mechanism that
  actually bounds the kill set: `is_server_allowed` threads into
  `resolve_startup_configs` (`src/pmcp/server.py:569`) and only survivors reach
  `_kill_orphan_processes` (`:604`). Use a deny-all/allow-fixture-only policy.
- **Choose a non-colliding fixture.** The orphan fingerprint keys on
  `(Path(command).name, tuple(args))` (`src/pmcp/server.py:700`) and BOTH must
  match. Pick a command basename matching no real server, and put a fresh
  `mktemp -d` path in the args so collision is impossible by construction.
- **Invoke `.venv/bin/pmcp` directly, not `uv run`** — under a redirected `HOME`,
  uv chases an absent cache.
- Redirect **`HOME` and `XDG_CONFIG_HOME`**; pass `--lock-dir`, `--config`,
  `--project`, `--policy`, and a spare port. **Never 3344.**
- **Assert `lazy + eager == 1` (the fixture, and nothing else).** Do NOT abort
  on "server counts in the hundreds" — that tripwire is inverted. The shipped
  manifest always contributes its 106 entries, so a *correctly* isolated run
  reports `eager=0, lazy=1, skipped=106, policy_denied=106`; hundreds in the
  skipped/denied columns is the CORRECT output and aborting on it would fail
  every good run. `lazy + eager` is the set that actually reaches
  `_kill_orphan_processes`, so it is the only number that proves isolation.
- **Assert the live gateway is untouched**: before and after, `:3344` still
  answers and the child set is byte-identical. Obtain `LIVE_PID` from the
  listening socket, never by name-matching the process:

  ```bash
  LIVE_PID=$(ss -ltnpH 'sport = :3344' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
  [ -n "$LIVE_PID" ] || { echo "FAIL: could not resolve live gateway pid"; exit 1; }
  # pgrep -P exits 1 when there are zero children, which is a VALID state —
  # under `set -euo pipefail` a bare capture would abort the run. `|| true`
  # applies ONLY here; the LIVE_PID guard above must keep failing hard.
  CHILDREN_BEFORE=$(pgrep -P "$LIVE_PID" | sort || true)
  ```

  `pgrep -f "pmcp --transport http"` **self-matches the invoking shell** and
  produces a bogus child-set delta — P1 hit exactly that and briefly believed
  the live gateway had lost children. The `-n` guard is mandatory: an empty
  `LIVE_PID` silently turns the assertion into two empty sets comparing equal,
  which looks like a pass while proving nothing — the same failure shape as the
  hardcoded `/health` literal.
- **`/health` is not functional proof.** `transport/http.py:436` returns
  `"ok": True` as a hardcoded literal — it proves the HTTP server answers and
  nothing more. Functional proof requires one real `gateway.invoke` against the
  fixture server with a non-error result.
- Steps: boot the relaxed fixture with no `FIRECRAWL_API_KEY` anywhere → confirm
  the startup summary shows only the fixture → issue one real `gateway.invoke`
  and assert a non-error result → assert from the spawned child's
  `/proc/<pid>/environ` that `FIRECRAWL_API_URL` is present and
  `FIRECRAWL_API_KEY` is absent (IF-0-P5-4 observed on a live process) →
  shut down → re-assert the live gateway's child set.
- Marked `@pytest.mark.live` so the default `-m 'not live'` suite is unaffected;
  run explicitly in SL-4.7 via `uv run pytest -m live tests/test_credential_boot.py`.

SL-4.5 expects to be a **no-op**: the field defaults to `[]` and the predicate
short-circuits to the declared value, so no pre-existing test should change.
The lane owns those files so that, if one does move, the ownership is
unambiguous rather than contended. Any edit here must be recorded in the commit
message with the reason, because a pre-existing test changing shape is evidence
the default was not actually preserved.

### SL-5 (SL-docs) — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, update cross-cutting documentation
  touched or invalidated by this phase's impl lanes, and append any
  post-execution amendments to phase specs whose interface freezes turned out
  wrong.
- **Owned files** (read `.claude/docs-catalog.json` for the authoritative list; a minimum set is below, but the catalog is canonical): `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SPEC_COMPLIANCE.md`, `AGENTS.md`, `CLAUDE.md`, `docs/**`, `.claude/docs-catalog.json`, `specs/phase-plans-v11.md`
- **Interfaces provided**: (none)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-1, SL-2, SL-3, SL-4

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | — | `.claude/docs-catalog.json` | Rescan: `python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/scaffold_docs_catalog.py" --rescan`. If the helper is absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and proceed. |
| SL-docs.2 | docs | SL-docs.1 | per catalog | Document `api_key_optional_when` in `CONTRIBUTING.md` (beside the `requires_api_key`/`env_var` field table at `:55-56`) and in the `README.md` manifest-entry examples at `:668` and `:928`, stating the two-party rule and that it fails closed. Add a `SECURITY.md` note that the field can only relax a credential the manifest author declared relaxable. Add the v1.22.0 `CHANGELOG.md` entry citing Consiliency/pmcp#114 and the placeholder-secret removal. Append the current phase alias to each file's `touched_by_phases`. Record intentionally-skipped files in the commit message. |
| SL-docs.3 | docs | SL-docs.2 | `specs/phase-plans-v11.md`, prior plans | Append `### Post-execution amendments` to the P5 section if any IF-0-P5-* freeze proved wrong. Note in the P5 scope notes that the consumer count was independently re-derived by two sweeps and revised from six to SEVEN, and record the counting method so it stops moving. |
| SL-docs.4 | verify | SL-docs.3 | — | Run any repo doc linters (`markdownlint`, `vale`, `prettier --check`, Mermaid render check). If none configured, no-op. |

## Execution Notes

- **Single-writer files**: `src/pmcp/manifest/loader.py` → SL-1 only.
  `src/pmcp/tools/handlers.py` → SL-3 only (this is why the seven consumers
  split 4/3 rather than one lane each — three live in one file that cannot be
  co-owned). `src/pmcp/cli.py` → SL-2 only.
  `src/pmcp/manifest/manifest.yaml` → SL-4 only. Every pre-existing
  `tests/test_*.py` file → SL-4 only; SL-1/2/3 each write a new test file whose
  name does not collide.
- **Cross-phase shared files — these serialize on merge, they are not
  parallel-safe across phases.** P5 writes `CHANGELOG.md` and
  `.claude/docs-catalog.json` (both SL-5); **P1 and P6CLEAN write both too**.
  P5 also writes `tests/test_manifest.py` (SL-4.5, expected no-op); **P6CLEAN
  writes it as well**. Whichever phase merges second rebases onto the first —
  none of the three may assume its version is the base. The `CHANGELOG.md`
  collision is an append-at-top conflict every time and should be resolved by
  keeping both entries, not by taking either side wholesale. If P5's
  `tests/test_manifest.py` edit stays a no-op as predicted, that collision
  disappears entirely, which is a second reason to keep SL-4.5 empty.
- **Lane count deviates from the roadmap's suggested 3.** The roadmap proposed
  A=loader, B=all six consumers, C=tests (and the roadmap's count of six was itself low). `tests/` cannot be a single lane's
  property, because the skill's task rules require each impl lane to write its
  own failing tests first — so tests are split by new-file ownership (SL-1/2/3)
  with all pre-existing test files reserved to the reducer (SL-4). Lane B is
  split into SL-2 and SL-3 along the `handlers.py` single-writer boundary,
  which is free parallelism at no ownership cost. The roadmap's Lane C
  responsibilities (guard test, honest count) live in SL-4.
- **Known destructive changes**: none — every lane is purely additive. SL-2 and
  SL-3 replace expressions in place; no file or symbol is deleted.
- **Expected add/add conflicts**: none. SL-1 stubs nothing that a later lane
  replaces.
- **SL-0 re-exports**: not applicable — there is no SL-0 preamble touching an
  `__init__.py`. SL-1's symbols are imported directly as
  `from pmcp.manifest.loader import requires_credential`, matching how
  `credential_lookup_keys` and `credential_storage_key` are already consumed by
  `config/loader.py:1039` and `installer.py`. Do **not** add them to
  `src/pmcp/manifest/__init__.py`.
- **Security posture — the failure mode is a server reachable without auth.**
  Three properties carry that weight and must not be traded away for
  convenience: (a) the manifest author and the operator must both act, so the
  field alone relaxes nothing; (b) a server cannot name its own credential as
  its relaxer, enforced twice (parse-time drop and predicate-time skip); (c)
  every unknown, malformed, or absent input yields `required=True`; and (d) a
  relaxed gate implies the child actually receives the relaxer (IF-0-P5-4),
  without which relaxation silently redirects a keyless server to the vendor
  endpoint. SL-4.2's
  fail-closed direction is the acceptance evidence for all three, and it is not
  optional.
- **Worktree naming**: `claude-execute-phase` allocates unique worktree names
  via `scripts/allocate_worktree_name.sh`. This plan does not spell out lane
  worktree paths.
- **Environment**: run `uv sync --all-extras`, never bare `uv sync`. Never bind
  anything to port 3344 — a live gateway runs there. Every test in this phase is
  in-process; no lane needs to start a gateway.
- **Stale-base guidance** (copy verbatim): Lane teammates working in isolated
  worktrees do not see sibling-lane merges automatically. If a lane finds its
  worktree base is pre-SL-1, it MUST stop and report rather than committing —
  the orchestrator will re-spawn or rebase. Silent `git reset --hard` or
  `git checkout HEAD~N -- …` in a stale worktree produces commits that destroy
  peer-lane work on `--no-ff` merge. SL-4 specifically must verify its base
  contains both SL-2 and SL-3 before running SL-4.7; a green full suite on a
  base missing either one is meaningless.

## Acceptance Criteria

- [ ] EC-P5-1 — proven by `uv run pytest tests/test_credential_requirement.py tests/test_credential_predicate_guard.py`; the guard's four checks (allowlisted manifest reads, `child_env=os.environ` misuse, dynamic-read detection, allowlist liveness) plus unit tests pinning all six IF-0-P5-1 clauses (clause 2, the no-`os.environ` rule, included).
- [ ] EC-P5-2 — proven by `uv run pytest tests/test_credential_optionality_e2e.py -k fail_closed tests/test_credential_gates_startup.py tests/test_credential_gates_handlers.py`; four servers — no `api_key_optional_when`, relaxer unset, relaxer naming its own credential, and relaxer present only in `os.environ` — each still fail closed at all **seven** gates.
- [ ] EC-P5-3 — proven by `uv run pytest tests/test_credential_optionality_e2e.py -k 'relaxed or shipped'` and `uv run pytest -m live tests/test_credential_boot.py`; with `FIRECRAWL_API_KEY` absent everywhere the fixture server passes all seven gates with **positive** outcomes (`ok is True`, present in `eager_configs`, `needs_api_key is False`), the shipped `firecrawl` entry is asserted to carry the field, and a spare-port gateway boots and completes one real downstream call.
- [ ] EC-P5-4 — proven by `uv run pytest && uv run ruff check . && uv run mypy src/pmcp`, plus the exit-status-bearing compatibility check in `## Verification` step 5 (a bare `git diff | grep -v` never fails a build, so it is written as a `test … -eq 0`).
- [ ] EC-P5-2, EC-P5-3 — additionally proven by `uv run pytest tests/test_credential_child_env.py`; IF-0-P5-4's gate-relaxed-implies-child-receives-it invariant, asserted under the managed-secret-key configuration that strips it.

## Verification

Run from the phase's merged branch after all lanes land:

```bash
uv sync --all-extras

# 1. Predicate contract and centralization guard (EC-P5-1)
uv run pytest tests/test_credential_requirement.py tests/test_credential_predicate_guard.py -v

# 2. Fail-closed at all seven gates (EC-P5-2) — weight verification here
uv run pytest tests/test_credential_optionality_e2e.py tests/test_credential_gates_startup.py tests/test_credential_gates_handlers.py -v

# 3. Seven-consumer relaxed path + shipped-manifest assertion (EC-P5-3)
uv run pytest tests/test_credential_optionality_e2e.py -k 'relaxed or shipped' -v

# 4. Child-environment invariant IF-0-P5-4 (EC-P5-2, EC-P5-3)
uv run pytest tests/test_credential_child_env.py -v

# 5. No pre-existing test moved — EXIT-STATUS BEARING (EC-P5-4).
#    `git diff | grep -v` always exits 0 and proves nothing; this fails the build.
test "$(git diff --name-only main -- tests/ \
  | grep -vcE '^tests/test_credential_(requirement|gates_startup|gates_handlers|predicate_guard|optionality_e2e|child_env|boot)\.py$')" -eq 0

# 6. Full suite, lint, types (EC-P5-4)
uv run pytest
uv run ruff check .
uv run mypy src/pmcp

# 7. Live boot on a spare port (EC-P5-3). Isolation recipe is P6CLEAN's
#    verified one (cbe98be). --config alone does NOT isolate (shipped manifest
#    layers additively); redirected HOME alone does NOT either (load_manifest
#    starts from the packaged manifest.yaml -> 106 lazy servers still enter the
#    kill set). --policy is the only real bound. NEVER 3344.
TMP=$(mktemp -d) && mkdir -p "$TMP/home" "$TMP/xdg"
HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/xdg" \
  .venv/bin/python -m pytest -m live tests/test_credential_boot.py -v
# harness invokes .venv/bin/pmcp (not `uv run`) with --policy allow-fixture-only
# --lock-dir "$TMP/lock" --config "$TMP/mcp.json" --project "$TMP" --port 3399,
# asserts lazy+eager == 1 (NOT "abort on hundreds" — 106 skipped/denied is the
# correct output for a properly isolated run), and that :3344 still answers
# with a byte-identical child set:
#   LIVE_PID=$(ss -ltnpH 'sport = :3344' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
#   [ -n "$LIVE_PID" ] || { echo "FAIL: could not resolve live gateway pid"; exit 1; }
#   CHILDREN_BEFORE=$(pgrep -P "$LIVE_PID" | sort || true)   # exits 1 on zero children

# 8. Residual-gate sweep — human cross-check only, not the gate
rg -n 'requires_api_key' src/pmcp/            # expect 26 hits: 10 reads, 8 writes, 3 defs, rest comments
rg -n 'server\.env_var|\.env_var\b' src/pmcp/cli.py
```

Steps 1 and 8 are complementary: step 8 is a human-read cross-check that an
attribute rename would defeat, so the enforcing gate is
`tests/test_credential_predicate_guard.py`. Step 8's second command exists
because consumer 7 contained no `requires_api_key` token at all — a sweep on
that token alone is what let it hide through six review rounds.

## Execution Policy
- execute: effort=medium
- SL-1: effort=high, reason=predicate semantics are security-load-bearing and every gate inherits them
- SL-2: effort=medium
- SL-3: effort=medium
- SL-4: effort=high, reason=fail-closed verification across seven gates plus the child-environment invariant is the phase's whole risk surface
- SL-5: effort=low, reason=docs sweep only

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/manifest/loader.py`, `src/pmcp/manifest/manifest.yaml`, `tests/`
- evidence paths: `plans/phase-plan-v11-P5.md`
- redaction posture: `metadata_only`
- downstream handling: `none`
