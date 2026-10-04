# Detailed plan: an overlay never hides another server from discovery — weights from the base manifest, and one entry never takes down the others (Consiliency/pmcp#342)

> Written on main `c9206a9` (dev0, a team host), worktree `pmcp-342`, branch
> `plan/342-catalog-overlay`. `origin/main` was re-fetched for revision 2 and is still
> `c9206a9`. Every number below was measured this session on that tree, on the commit
> before the manifest cache (`a622ec5`), on the rev-1 or rev-2 spike, or on the rev-3 spike applied
> to `c9206a9`. The spike was then removed, and this PR carries only this file.
>
> **Bounded-plan verdict: within threshold, larger than rev 1.**
> - Source: seven files change (+576/−181: `manifest/loader.py` +282/−20, `manifest/matcher.py` +105/−62, `tools/handlers.py` +101/−72, `config/loader.py` +44/−2, `manifest/environment.py` +26/−8, `client/manager.py` +16/−1, `server.py` +2/−16).
> - Tests: one module is new (`tests/test_catalog_overlay_discovery.py`, 131 tests).
>   Three existing tests are migrated, because the entries they load are now skipped or no
>   longer named in a log (D4, D9).
> - Docs: one CHANGELOG bullet and one README paragraph.
> - It is still one conceptual change: an overlay can add to discovery, and one entry can
>   never take the rest down.
>
> **Revision 3** (2026-10-04): board round 2 on `bc68f23` (Consiliency/pmcp#343).
> Claude partially agreed with nothing blocking, gemini agreed and grok was degraded. Codex
> raised two blocking defects. R1 held again: codex measured 329 candidate-losing queries on
> main against 0.
> - **B5** (codex): D9 still leaked. The `api_key_optional_when` self-reference warning
>   printed the variable, and `probe_clis` printed an overlay CLI's name at DEBUG and INFO.
>   - The class is now covered on every path this plan touches: loading, discovery, CLI
>     probing, startup and refresh skip lines, and lazy registration.
>   - Two repro tests and a sentinel sweep over those paths prove it.
>   - The promise is stated as exactly that scope (D9, claude N2).
> - **B6** (codex): moving `keyword_weights` de-duplicated *after* normalising, while main
>   de-duplicates raw keywords, so the weights changed with no overlay at all. Main's
>   counting is restored exactly. A differential test against main's verbatim function runs
>   over 40 generated alias-heavy keyword sets, for hand-built and explicit-path manifests,
>   plus codex's repro (D2).
> - **Claude N1**: the `ServerConfig` and `CLIAlternative` annotation checks were stricter
>   than any consumer. They dropped `transport: stdio` with no `url`, which main starts as
>   local, so a user override reverted to the shipped command. Rev 3 runs what the consumers
>   run and nothing stricter. Must-load tests cover 11 values main accepted and used (D4).
> - **Claude N3**: the touched test files are listed by name, and the mutation driver's
>   basetemp moved off the `pmcp-342-bt` tree.
>
> **Revision 2** (2026-10-03): board round 1 on `bd5c25f`, summarised below. Claude, grok
> and codex found R2's hand-written field list incomplete: it missed `transport`, CLI
> alternatives and the startup/refresh view, and it logged overlay keys. Rev 2 replaced the
> list with consumer-model checks and per-consumer guards. It treated `null` as absent and
> reworded the docs to "within the result limit".


## Round 2 board findings (Consiliency/pmcp#343 @ `bc68f23`)

| finding | seat | reproduced (rev-3 tests run on the rev-2 patch) | resolution |
|---|---|---|---|
| **B5** values and names still in logs: the `api_key_optional_when` self-reference warning prints `('{item}')`, and `probe_clis` prints `Detected CLI: <name>` (DEBUG) and `Detected N CLIs: …` (INFO) | codex | yes: `test_a_self_relaxing_credential_is_refused_without_its_value`, `test_probe_clis_never_logs_an_overlay_cli_name` and `test_no_overlay_name_or_value_reaches_any_log_on_these_paths` all fail on rev 2 | D9 rev 3: value removed; CLI probe, `Matched CLI hints`, startup/refresh skip lines (shared `startup_skip_message`) and `Registered lazy server` use the labels; skip lines print a credential variable only when pmcp's own entry declares it. Mutants L1–L6 |
| **B6** weights change with no overlay: dedupe after normalising | codex | yes: `test_the_round_2_alias_repro_picks_mains_match` and 11 of 40 generated differential cases fail on rev 2 | `keyword_weights` restored to main's counting (a list over `set(raw keywords)`). Mutant M17 |
| N1 `transport: stdio` (no `url`) drops a working entry; `status`, `source`, `replacement`, `discovery_diagnostics`, `auto_start` stricter than any consumer | claude | yes: `test_values_main_accepted_and_used_still_load` (11 cases) and the `stdio` override test fail on rev 2 | D4 rev 3: the annotation checks are removed. The check runs the consumers' own code: name and keyword scoring, credential lookups, `CapabilityCandidate`, the startup conversion, and `_rank_one_cli`. Mutants M6, M10, M11 |
| N2 the name guarantee holds for skipped entries only | claude | = B5 | D9's scope is stated exactly, and kept entries are covered by the sweep |
| N3 the touched-suite files are not named, and the mutant basetemp sits under the tree the plan warns about | claude | by reading | the files are listed by name (*The test surface this touches*); the basetemp is `$PMCP342_MUT_BASETEMP`, default `/mnt/workspace/users/viperjuice/pmcp-342-bt/mut` |

Round 2's R1 numbers were re-measured on rev 3: the copycat test and the live gateway
(Verification 4 and 5).

## Round 1 board findings (Consiliency/pmcp#343 @ `bd5c25f`)

| finding | seat | reproduced on the rev-1 spike | resolution |
|---|---|---|---|
| **B1** non-string `transport` (`on`, `5`, list, dict) raises for any query ranking the entry, which hid `playwright` on `screenshot` | claude F2, grok 1, codex 1 | yes: the rev-2 field walk on the rev-1 patch fails `[transport]` with `ValidationError ... CapabilityCandidate` | D4: the entry is built into `ServerConfig`'s annotations (`transport` is a `Literal`) and into the `CapabilityCandidate`; it is skipped. Guard G3 |
| **B2** an overlay `cli_alternatives` entry (`check_command: 5`, int `description`, `keywords: [5]`, `examples: [5]`, `prefer_mcp_for`) raises on every query; also an override of shipped `git` | claude F1, grok 2 | yes: all 6 `test_every_cli_field_…` cases fail on rev 1 (`AttributeError`, `TypeError`, `IndexError`, `CLIHint` `ValidationError`) | D4: `CLIAlternative`'s annotations plus the `CLIHint` that `rank_cli_hints` builds, and a non-empty `check_command`, because `probe_clis` reads slot 0. Guards G4 and G5 |
| **B3** `args: null`, a non-string `command`, int or list `headers`, an int metadata URL: `resolve_startup_configs` raises and aborts `GatewayServer.initialize` and `gateway.refresh` for every server | grok 3 | yes: rev 1 fails with `LocalMcpServerConfig` / `RemoteMcpServerConfig` `ValidationError` (10 field cases) | D4: the entry is converted by the same `_manifest_server_to_config` startup uses, at parse time. D8: `null` is absent. Guard G6 |
| **B4** the skip warning prints the overlay key verbatim, so a pasted secret used as a key reaches the log | codex | yes: 11 rev-2 walk cases on rev 1 fail on the name sentinel in `Skipping invalid server entry '…'` | D9: every overlay-entry log line names the entry with `_server_label` / `_cli_label` (shipped names only), and the reason names only a field and an error type |
| F3 a blank `description:` drops the whole entry, so an override reverts to shipped | claude | yes, by test | D8: `null` means absent for every field. `test_null_fields_take_their_defaults_and_keep_the_entry` |
| F4 five overlay servers tied on `screenshot` hide `playwright` at limit 5 (ties break by name) | claude | by reading | D10: docs say "within the result limit". The tie-break is unchanged |
| F5 raw overlay key in the skip warning (pre-existing) | claude | = B4 | D9 |
| F6 the mutation driver counted setup errors as RED | claude | yes (round 1's first run) | the driver now requires `failed > 0` and `errors == 0`, and creates the basetemp parent |

**Taken from the seats' measurements:**
- R1 was re-attacked seven ways by claude and replayed by codex and grok. The results
  stand, and this revision does not change R1's code.
- Shipped-only outputs were byte-identical across 1250 queries, comparing main against the
  patch with no overlay.

## Task

Consiliency/pmcp#342: with a user overlay (`~/.pmcp/manifest.yaml`) and a project overlay
approved with `pmcp trust approve <path>`, `gateway.catalog_search` on a real
`pmcp --transport http` gateway returned no manifest candidates at all. It did not return the
user overlay's server or the shipped servers. After `pmcp trust revoke` they came back. The
reviewer did not trace the cause. The issue names three candidates: the project overlay
replacing the whole `servers` map, a swallowed validation error, or a path or consent
interaction in `catalog_search`'s manifest lookup. It asks for a regression test: user
overlay plus approved project overlay gives candidates from the shipped, user and project
sources.

Asked of this plan:
- reproduce on main and on the commit before the cache (Consiliency/pmcp#339);
- find the exact cause, with evidence;
- fix the class, and include or rule out every consumer of the merged manifest;
- end-to-end tests through the real loader, consent gate and CLI;
- one mutant per rule.

## Research summary

### Reproduction (real gateway, real CLI, both commits)

Setup (`$WORKTREE_ROOT/pmcp-342-repro`, on the workspace volume, not `/tmp`):
- `HOME=…/home`, with a user overlay holding one server, `useronly`, with keywords
  `[widget, demo]`;
- a project at `…/proj`, with a `.pmcp/manifest.yaml` holding one server, `projonly`, with
  keywords `[gadget, demo, screenshot]`;
- the gateway started in `proj` with `pmcp --transport http --port …`;
- `catalog_search` called over streamable HTTP with the MCP client
  (`{"query": q, "include_offline": true}`);
- approve and revoke run with the real `pmcp trust` CLI, against the running gateway, with
  no restart.

| query | unapproved | **approved (main `c9206a9`)** | **approved (`a622ec5`, before #339)** | revoked | approved (spike) |
|---|---|---|---|---|---|
| `demo` | `[useronly]` | **`[]`** | **`[]`** | `[useronly]` | `[projonly, useronly]` |
| `screenshot` | `[playwright]` | **`[]`** | **`[]`** | `[playwright]` | `[playwright, projonly]` |
| `widget` | `[useronly]` | `[useronly]` | `[useronly]` | `[useronly]` | `[useronly]` |
| `gadget` | `[]` | `[projonly]` | `[projonly]` | `[]` | `[projonly]` |
| (no query) | `[]` | `[]` | `[]` | `[]` | `[]` |

- **It reproduces on both commits with identical output,** so the manifest cache is ruled
  out.
- **It does not reproduce with keywords that no other server shares.** A first attempt gave
  `projonly` `[gadget, projonly]` and `useronly` `[widget, useronly]`. There, approving left
  every candidate in place: `widget → [useronly]`, `gadget → [projonly]`,
  `playwright → [playwright]`.
- **What triggers it is a keyword the approved overlay makes non-unique.** The reviewer's
  overlay is not recorded, but a test overlay that reuses a word like "demo" or "test" in
  both files, or that reuses a shipped keyword, gives exactly the reported symptom: nothing
  from any source, and everything back on revoke.
- **No query returns no manifest candidates in every state.** That is by design: Consiliency/pmcp#78
  matches a query against keywords, and `_manifest_candidates_for_query` returns `[]` for
  an empty query. It does not depend on overlays.

### Root cause R1: discovery weights are counted over the merged manifest

`catalog_search` (`tools/handlers.py:1316`) calls `_manifest_candidates_for_query`
(`:1121`). That function computes `keyword_weights = _manifest_keyword_weights(manifest)`
(`matcher.py:90`) over **every server in the merged manifest**:

```python
frequencies[k] = number of servers declaring k
weight[k] = max(1.0 / frequency, 0.5)
score = sum(weight of each matched keyword) / 3
eligible iff score >= 0.2  (or a normalized name match)
```

A single-keyword query on a keyword that one server declares scores 1.0 / 3 = **0.333**,
which passes. Once a second server declares the same keyword, the weight is 0.5 and the
score is **0.167**, which fails, **for both servers**. The floor of 0.5 means one shared
keyword can never pass the 0.2 threshold on its own. So adding a server is not neutral: it
removes every other server sharing a keyword from that keyword's results.

Traced with the real loader and gate (`trace.py`, cwd `proj`):

```
== unapproved   servers 108  projonly False
  q='demo'       useronly:   weight 1.0  score 0.333
  q='screenshot' playwright: weight 1.0  score 0.333
== approved     servers 109  projonly True
  q='demo'       useronly:   weight 0.5  score 0.167   <- below 0.2
  q='demo'       projonly:   weight 0.5  score 0.167   <- below 0.2
  q='screenshot' projonly:   weight 0.5  score 0.167
  q='screenshot' playwright: weight 0.5  score 0.167   <- shipped server lost
```

**How wide the class is** (`copycat.py`): approve a project overlay with one server that
declares all 569 shipped keywords, then query each shipped keyword with the limit lifted.

| | main | spike |
|---|---|---|
| keyword queries that surfaced ≥ 1 server before the overlay | 440 | 440 |
| …that lose a candidate after it | **329** | **0** |
| shipped servers that become undiscoverable by some keyword | **91** | **0** |

**The three candidate causes in the issue are ruled out by measurement:**
- *Overlay replacing the `servers` map:* no. `load_manifest()` approved has 109 servers =
  107 shipped + `useronly` + `projonly`, and unapproved has 108.
- *Swallowed validation error:* no. The valid overlays log no warning, and every server is
  present.
- *Consent/path, or a cwd/project-root mismatch between CLI and gateway:* no. Approved,
  `gadget → [projonly]` through the same gateway, so the gate admitted the very file the
  CLI approved, and `get_server` sees it.

### Root cause R2: one entry's wrong type takes down every other entry

No overlay entry was type-checked (`_parse_server_config`, `_parse_cli_alternative`), and
the consumers iterate **every** entry. Main and the rev-1 spike, measured with the rev-2
field walk (`test_every_*_field_with_every_bad_shape_is_contained`). The walk covers every
field of `ServerConfig` and `CLIAlternative`, and these shapes:
- null;
- a wrong scalar: int, bool, str;
- a list;
- a dict;
- a 100 KB string;
- `10**40`.

Each case drives every consumer:
- `catalog_search` with `include_offline` true and false;
- `request_capability`;
- `resolve_startup_configs` on the real `load_manifest().servers`;
- `gateway.refresh` on the real loader.

| consumer | what one bad entry did (main / rev 1) | entry fields involved |
|---|---|---|
| `catalog_search` keyword table and scoring (every query) | `TypeError` / `AttributeError` for every query | server key (int, bool), `keywords` |
| `catalog_search` candidate build (`CapabilityCandidate`) | `ValidationError` for any query ranking the entry, which hides shipped servers on shared keywords | `description`, `transport`, `url`, `package`, `server_card_url`, `declared_*`, `env_var`, `secret_key`, `env_instructions` |
| `rank_cli_hints` (every query) and `probe_clis` | `TypeError`, `AttributeError`, `IndexError`, `CLIHint` `ValidationError` for every query | every `CLIAlternative` field |
| `resolve_startup_configs`: gateway startup (`server.py:750`) and `gateway.refresh` (`handlers.py:1926`) | `LocalMcpServerConfig` / `RemoteMcpServerConfig` `ValidationError` aborts resolution **for every server** | `args`, `command`, `headers`, `url`, the five metadata URLs, `declared_scopes`, `supports_url_elicitation`, `extra_env` values |
| the skip warning itself | rev 1 logs the overlay key verbatim | the key |

Totals with the rev-2 test module:
- **main:** 60 of 77 fail. Most R2 cases fail on main for R1 reasons too, because the bad
  entry shares `screenshot`.
- **rev-1 patch:** 44 fail; the `ValidationError`/`TypeError` mix is in the table above.
- **rev-2 spike:** all 77 pass.

`load_manifest()` itself never raised in any case. So the call sites that wrap it in
`try/except` and fall back to empty cannot fire on these causes, and every failure was in a
consumer.

### Every consumer of the merged manifest, included or ruled out (measured)

R1: `consumers.py` snapshots each consumer with the project overlay approved and then
revoked, through the real CLI, on main and the spike back to back. R2: the field walk
drives every consumer listed here with every bad shape.

| consumer | how it reads the manifest | R1 (approved vs revoked, main) | R2 (one bad entry, main) | verdict |
|---|---|---|---|---|
| `catalog_search` manifest candidates (`handlers.py:1121`) | corpus-weighted keyword score over all servers; builds `CapabilityCandidate` | `demo` and `screenshot` lose every candidate | raises for every query (keys, keywords) or for any query ranking it | **fixed:** R1 by D2; R2 by D4 + G1–G3 |
| `catalog_search` CLI hints: `rank_cli_hints`, `probe_clis` | every CLI alternative, every query | — | raises for every query | **fixed:** D4 + G4, G5 |
| `match_capability` / `_keyword_match` (exported, no in-tree caller) | same weights | `demo` and `screenshot` → no match | raises | **fixed:** D2 + G7 |
| `request_capability` tier 1 (name map, name-match candidate) and tier 2 (`get_servers_in_category`, category candidates) | names; category IDF over the fixed `_CATEGORY_MAP`; builds `CapabilityCandidate` | identical for all 9 queries | raises: name map (int/bool key), category keywords, candidate build (`description`, `env_var`, …) | R1 ruled out; **R2 fixed:** D4 + G8–G11 |
| gateway startup (`server.py:750`) and `gateway.refresh` (`handlers.py:1926`), both through `resolve_startup_configs` → `_manifest_server_to_config` | builds `Local`/`RemoteMcpServerConfig` for every manifest server | name set ± `projonly` | `ValidationError` aborts resolution **for every server** | **R2 fixed:** D4 + G6 |
| `describe`, `provision`, `auth_connect`, `update_server` / `pmcp update`, `_resolve_lifecycle_target`, `_finalize_server_ready`, `sync_environment`, `_materialised_pin`, `_auth_env_options` | `get_server(name)` | `get_server` differs for **0** shared names | per-entry by nature: a lookup of one name. D4 skips the bad entry before any of them sees it | ruled out (name lookup) |
| `config_status`, `get_startup_policy`, `pmcp config`, `load_configs` defaults, `pmcp init`, `secrets`, `manifest/sync.py`, refresher | name sets, or per-name fields | same name set ± `projonly`; `load_manifest` never raised | — | ruled out |
| `gateway.health` | client-manager statuses | — | — | ruled out |
| `Manifest.search_by_keyword` | substring over keywords | no in-tree caller | D4 keeps `keywords` a list of str | ruled out |

### The test surface this touches

- **New:** `tests/test_catalog_overlay_discovery.py` (131 tests).
- **Migrated** (verbatim patch below):
  - `tests/test_version_pin.py`:
    - `test_a_pin_on_a_malformed_entry_costs_only_that_entry` (3 cases). An int in `args`,
      `command` or an install argv now skips the entry, because the startup conversion
      rejects it, and on main it aborted startup and refresh for every server.
    - `test_no_refused_pin_or_credential_reaches_a_log_or_update_output`. `c-bad` is
      skipped whole, so there are 9 refusal lines instead of 10.
  - `tests/test_manifest_overlay.py::test_server_env_unknown_server_warns_and_skips`. The
    warning no longer shows the overlay's own key.
- **Unchanged and passing:** `tests/test_manifest.py`'s IDF tests (hand-built manifests,
  main's weighting) and `tests/test_manifest_cache.py` (47).
- **Touched suites, by name (23 files):** `tests/test_catalog_overlay_discovery.py`, `tests/test_client_manager.py`, `tests/test_client_manager_reconnect.py`, `tests/test_config_loader.py`, `tests/test_credential_gates_handlers.py`, `tests/test_credential_gates_startup.py`, `tests/test_credential_optionality_e2e.py`, `tests/test_env_overlay_provenance.py`, `tests/test_lazy_start.py`, `tests/test_manifest.py`, `tests/test_manifest_cache.py`, `tests/test_manifest_overlay.py`, `tests/test_manifest_provision.py`, `tests/test_offline_discovery.py`, `tests/test_phase4_e2e.py`, `tests/test_project_source_consent_manifest.py`, `tests/test_scoped_advisor_audit.py`, `tests/test_server.py`, `tests/test_startup_policy_reapproval.py`, `tests/test_startup_resolver.py`, `tests/test_tools.py`, `tests/test_trust_cli.py`, `tests/test_version_pin.py`.
  Result: **2329 passed, 1 skipped, 19 deselected, 0 failed** (185.48 s).

### Cost

- `load_manifest()` cache hit, median of 200 calls, 3 rounds alternating (rev 1, unchanged
  by rev 2, because the checks run only when an overlay is parsed): main 0.93–1.03 ms,
  spike 0.95–1.02 ms.
- The rev-3 consumer check costs 1.2 ms for all 107 shipped servers and 12 CLIs, or
  about 0.01 ms per entry. Shipped entries are not checked at load; they are checked by a
  test. An overlay pays that once per entry per cache miss.

## Design decisions (made explicitly)

### D1. The class is "one overlay entry changes whether a different server is discoverable"

The issue names the instance: approving a project overlay. The same defect hits a user
overlay and a `$PMCP_MANIFEST_PATH` overlay; the probe's env-overlay rows lose `playwright`
on `screenshot` the same way. It hits both weighted scorers, `catalog_search` and the
exported `match_capability`. And through R2, one bad entry hides every server, not only
those sharing a keyword. The rules below are stated over every overlay source and both
scorers. The class test is derived from the shipped vocabulary (all 569 keywords), not from
the issue's two examples.

### D2. R1: keyword weights are a property of the base manifest, frozen before any overlay

`_build_manifest` computes `keyword_weights(base servers)` right after parsing the base
document, before the overlay loop. It stores the result on
`Manifest.base_keyword_weights` (`dict[str, float] | None`, default `None`,
`compare=False, repr=False`). `_manifest_keyword_weights(manifest)` returns a copy of it
when it is set; otherwise it computes from the manifest's own servers.

Consequences, each tested:
- **Overlays never change a weight.** For user, project and env sources alike, the
  weights with a copycat overlay equal the shipped weights, on a cache miss and on a hit.
- **A keyword that only an overlay declares** is absent and scores at the existing default
  of 1.0. So an overlay server is discoverable by its own unique keywords exactly as a
  shipped one is. Two overlay servers sharing an overlay-only keyword both surface; that
  is the `demo` row.
- **An overlay that replaces a shipped name** leaves that name's shipped keywords in the
  base counts. Weights never move, so replacement cannot hide a neighbour either.
- **An explicit `load_manifest(path)`** has no overlays, so its base is the file itself,
  identical to main.
- **A hand-built `Manifest`** (tests, ad-hoc callers) has no base and keeps main's corpus
  weighting byte for byte. That is why `tests/test_manifest.py` needs no change.

The invariant: adding or approving an overlay never removes a non-overlay server from a
query's candidates. The only exception is top-N displacement (`limit`, 5 by default), which
is ranking: an operator's server that scores higher ranks first.

**Alternatives rejected:**
- *Lower the threshold or raise the 0.5 floor* (the instance fix). A shared keyword would
  pass, but generic keywords stop sinking, which the existing
  `test_keyword_match_generic_api_alone_stays_below_threshold` forbids. Mutant M4 is
  exactly this change, and it is red.
- *Gate on an unweighted match and rank by IDF.* This changes shipped-only behaviour:
  `browser` (5 servers) would start returning 5 candidates. That is a search-quality
  decision outside this issue, and it also breaks the generic-api test.
- *Read the shipped file's weights directly, ignoring the `Manifest` passed in.* This
  breaks hand-built manifests and explicit-path loads, which have nothing to do with the
  shipped file.
- *Record overlay provenance and skip those names when counting.* This is equivalent for
  added names, but a replaced shipped name would then drop out of the counts and shift
  other weights. Freezing the base weights is simpler and exact.

**Counting is main's, exactly (round 2, B6).** The function moved from `matcher.py` with
only the per-server guard added. A keyword repeated verbatim in one server counts once, and
two spellings that normalise alike (`alpha-beta`, `alpha_beta`) count twice, as on main. A
differential test checks it against main's verbatim function: 40 generated keyword sets
with `-`/`_`/space/case aliases and repeats, for hand-built and explicit-path manifests.
Codex's repro (`a-other: [alpha]`, `z-dupe: [alpha-beta, alpha_beta]`, query
`alpha beta`) weighs 0.5 and picks `a-other`, as main does (M17).

### D3. Carry the base weights through `_build_manifest_with_config_servers`

`request_capability` builds a merged view with `.mcp.json`-only servers
(`handlers.py:3310`). It does not score keywords today. But a fresh `Manifest(...)` there
would silently drop back to corpus weights, letting configured servers dilute shipped ones
for any future scorer that takes the view. One line passes `base_keyword_weights` through
(M3).

### D4. R2: an entry is skipped at parse time if, and only if, a consumer would fail on it

Rev 1 used a hand-written list of fields, which missed things (round 1). Rev 2 checked each
entry against `ServerConfig` / `CLIAlternative`'s own annotations, which was stricter than
any consumer and dropped working entries (round 2, N1). Rev 3 runs **what the consumers
run, and nothing else**, inside the existing per-entry `try/except` of
`_parse_overlay_document`:

| entry | check (the consumer's own code) | consumer |
|---|---|---|
| server | `normalized_server_name(server.name)` — the function the handlers now share | `catalog_search` name match, `request_capability` name tier |
| server | `matcher._keyword_match_score("probe", server.keywords)` | every keyword scorer (catalog, match_capability; the category tier normalises the same way) |
| server | `requires_credential(server)`, and `os.environ.get(key)` for each `credential_lookup_keys(server)` key | every candidate's credential metadata (`_get_server_env_metadata`, `_auth_env_options`) |
| server | `CapabilityCandidate(**manifest_candidate_fields(server), …)` — the function the handler builds from | `catalog_search` |
| server | `config.loader._manifest_server_to_config(server, lambda _: None)` — the same function | startup, `gateway.refresh`, provisioning, lazy connects |
| CLI | `matcher._rank_one_cli(...)` with `min_score=-1`, which runs every text scorer and builds the `CLIHint` | `rank_cli_hints`, every query |
| CLI | `check_command` names a program | `probe_clis` → `check_cli` reads `check_command[0]` |

Consequences:
- **Not stricter than main's consumers.** The following load and work as on main (11
  must-load tests, each also starting as local):
  - `transport: stdio`, `Local` or `carrier-pigeon` with no `url` (the startup conversion
    ignores `transport` without a `url`);
  - `status: 5`, `source: [x]`, `replacement: {…}`, `discovery_diagnostics: [1]`, which no
    consumer types;
  - `auto_start: maybe`, `requires_api_key: maybe` and `supports_url_elicitation: maybe`,
    which consumers use for truthiness (with no `url`);
  - `keywords: "zzodd"`, which the scorers iterate as characters, as main does.

  A user override of `github` with `transport: stdio` keeps the override.
- **Strict exactly where a consumer fails.** The field walk (every field × 8 shapes ×
  every consumer) passes with the guards present, and every one of the projection mutants
  M6–M11 is red. With a `url`, `transport: carrier-pigeon` is still skipped, because the
  startup conversion rejects it and main aborted startup on it.
- **Derived, not listed.** Making a consumer model stricter (`CapabilityCandidate`, the
  startup conversion, `CLIHint`) is enforced at parse time with no loader change. The three
  derivation tests and their control remain.
- **Overlay entries only.** The shipped entries are held to the same checks by a test (all
  107 servers and 12 CLIs pass).

### D5. No change to consent or the cache key

The defect is downstream of the gate. The cache key is unchanged: the weights are a pure
function of the base bytes, which are already in the key. The parse-time check runs inside
the existing per-entry `try/except`, during a build, so its warnings follow the existing
once-per-transition rule. The only logging changes are D9's.

### D6. No-query behaviour is unchanged

`catalog_search` without a query returns no manifest candidates in every consent state.
That is by design: a candidate needs a query to match. A test pins it.

### D7. The second line: every per-entry consumer guards each entry

The parse-time check covers overlays. A `Manifest` can also be built by hand (tests, and
`_build_manifest_with_config_servers`) or from a future source. So each consumer that
iterates entries skips one it cannot use, logs a WARNING naming the entry safely (D9) and
the exception class, and carries on:

| guard | where | mutant |
|---|---|---|
| G1 | `keyword_weights`: one server's keywords | G1 |
| G2 | `_manifest_candidates_for_query`: scoring | G2 |
| G3 | `_manifest_candidates_for_query`: candidate build. It fills the limit from the next-best entry, not with a short list | G3 |
| G4 | `rank_cli_hints`: each CLI (the body moved, unchanged, into `_rank_one_cli`) | G4 |
| G5 | `probe_clis`: each `check_command` | G5 |
| G6 | `resolve_startup_configs`: each manifest server. This covers gateway startup **and** `gateway.refresh`, which both call it | G6 |
| G7 | `_keyword_match` (`match_capability`): each server | G7 |

Each guard has a test that bypasses the parse-time check with a hand-built bad entry, and a
mutant that narrows its `except` to `ZeroDivisionError`.

### D8. A YAML `null` means "absent", for every field

In rev 1, `description: null` rejected the entry while `url: null` was accepted. A blank
`description:` in a user override of `github` therefore reverted it to the shipped command
(claude F3). Rev 2 drops `null` fields before parsing, for servers and CLI alternatives
alike, so each takes its default:
- `description` becomes `""`;
- `keywords`, `args` and `declared_*` become `[]`;
- `help_command` becomes `[name, "--help"]`;
- `transport` comes from `url`.

The rule is uniform because an overlay entry is a whole-entry replacement: there is no
"clear this field" meaning that `null` could carry and absence could not. M13.

### D9. No overlay name or value in a log line on the paths this plan touches

**The promise, exactly.** No log record emitted by the following carries an overlay entry's
key (unless pmcp ships that name) or a value from an overlay entry:
- manifest loading and overlay parsing;
- discovery: `catalog_search`, `request_capability`, `match_capability`, `rank_cli_hints`;
- CLI probing: `probe_clis`, `check_cli`, `get_cli_help`;
- startup and refresh resolution: the shared skip lines;
- lazy registration (`Registered lazy server`);
- the guards (D7).

**Out of scope, said so.** Once a server is connected, started or provisioned, the client
manager's per-server lifecycle lines name it, as they name every `.mcp.json` server: about
28 sites in `client/manager.py`, plus the provisioning flow. Those act on a server the
operator chose to run, and naming it is how the operator follows it. Hiding it there is a
separate decision about operability.

**How:**
- Overlay-entry lines name the entry with `_server_label` / `_cli_label`, which show a name
  only if pmcp ships it.
- `entry_log_name(name, manifest_derived=…)` names a `.mcp.json` entry as before and a
  manifest-derived one by its label. It is used by `startup_skip_message` (one function now
  shared by gateway startup and `gateway.refresh`) and by `Registered lazy server`.
- A skip line prints a credential variable's name only when pmcp's own shipped entry
  declares it (`shipped_env_var`), or for a configured remote entry's own header variables.
- The self-reference warning no longer prints the variable.
- The skip reason (`_rejection_reason`) keeps only a field name and an error type.
- `probe_clis` / `check_cli` log the label and the exception class.

**Tests:**
- the field walk plants a name and a value sentinel in **rejected** entries;
- `test_no_overlay_name_or_value_reaches_any_log_on_these_paths` plants them in **kept**
  entries, then drives:
  - load;
  - `catalog_search` with the real CLI probe;
  - `request_capability`;
  - `match_capability`;
  - startup resolution with a missing-auth skip, logged through `startup_skip_message`;
  - `gateway.refresh`;
  - the real `ClientManager.register_lazy_configs`.

  It asserts that no record at DEBUG carries either sentinel.
- Mutants L1–L6 each reintroduce one leak, and each is red.

### D10. Ties and the result limit (claude F4)

Five approved overlay servers sharing `screenshot` with `playwright` all score 0.333. The
sort is `(-score, name)`, so they can fill the default limit of 5 by name. This is ranking,
not removal: `playwright` is still a candidate at a larger limit. The class test lifts the
limit for that reason. The plan keeps the tie-break as it is: preferring shipped servers on
ties is a ranking-policy change of its own, and nothing in Consiliency/pmcp#342 asks for it.
The CHANGELOG and README wording now says "within the result limit".

## Changes

### `src/pmcp/manifest/loader.py` (modify)
- R1: the `Manifest.base_keyword_weights` field, `keyword_weights()` (moved from
  `matcher.py`, with guard G1), and the base weights computed before the overlay loop.
- R2:
  - `_without_nulls` (D8), and `_EntryRejected`;
  - `manifest_candidate_fields` and `cli_hint_fields`, the shared projections;
  - `normalized_server_name` (shared with the handlers), `entry_log_name` and
    `shipped_env_var` (D9);
  - `_check_server_for_consumers` and `_check_cli_for_consumers` (D4);
  - `_rejection_reason`, `_shipped_cli_names` and `_cli_label` (D9);
  - `_category_keyword_norms` (G10).
- `_parse_overlay_document` runs both checks inside the existing per-entry `try/except`
  and logs the labelled, value-free reason.
- Every overlay-entry log line names the entry by label.

### `src/pmcp/manifest/matcher.py` (modify)
- `_manifest_keyword_weights` returns the base weights.
- `rank_cli_hints`' body moves unchanged into `_rank_one_cli`, which builds its `CLIHint`
  from `cli_hint_fields`. Each CLI is guarded (G4).
- `_keyword_match` guards each server (G7).

### `src/pmcp/tools/handlers.py` (modify)
- `_build_manifest_with_config_servers` carries the base weights (D3).
- `_manifest_candidates_for_query` builds its candidate from `manifest_candidate_fields`,
  guards scoring (G2) and the build (G3), and fills the limit from the next-best entry.
- `request_capability`'s name map (G8), name-match candidate (G9) and category candidates
  (G11) are guarded.
- `_log_unusable_manifest_entry`: one helper for all of the guards' warnings.
- Both name matches use `normalized_server_name`. `Matched CLI hints` logs labels, and
  the refresh skip lines go through `startup_skip_message` (D9).

### `src/pmcp/manifest/environment.py` (modify)
- `probe_clis` guards each `check_command` (G5). `probe_clis`, `check_cli` and
  `get_cli_help` log CLI labels and exception classes, never names or messages (D9).

### `src/pmcp/config/loader.py` (modify)
- `resolve_startup_configs` guards each manifest server (G6). This covers gateway startup
  and `gateway.refresh`.
- `startup_skip_message`: one skip line for gateway startup and refresh (D9).

### `src/pmcp/server.py` (modify)
- The startup skip lines go through `config.loader.startup_skip_message` (D9), and the
  now-unused `StartupSkipReason` import is dropped.

### `src/pmcp/client/manager.py` (modify)
- `Registered lazy server` names a manifest-derived server by its label (`_lazy_log_name`,
  D9).

### `tests/test_catalog_overlay_discovery.py` (new, 131 tests; verbatim below)
- **R1, as in rev 1:**
  - the end-to-end approve and revoke through the real CLI, loader and consent gate;
  - no query in any state;
  - every overlay source;
  - the copycat class test over all 569 shipped keywords;
  - `match_capability`, hand-built manifests, the config-server view and explicit paths.
- **R2:**
  - the field walk: every `ServerConfig` field (37) and every `CLIAlternative` field (6),
    each × 8 shapes, driving `catalog_search` (`include_offline` true and false),
    `request_capability`, startup and `gateway.refresh`, with name and value sentinels;
  - non-string keys;
  - the named board reproductions;
  - the approved-project CLI case;
  - null as absent;
  - the shipped entries under the same checks;
  - annotation-only fields;
  - the three "stricter consumer" derivation tests and their control.
- **The second line:** one test per guard, G1–G11, each with a hand-built bad entry that
  bypasses the parse-time check.

### `tests/test_version_pin.py`, `tests/test_manifest_overlay.py` (migrate, +18/−7; verbatim below)
- See *The test surface this touches*.

## Documentation impact

- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed` (`CHANGELOG.md:439` on
  `c9206a9`, verified to sit under `[Unreleased]`):

  > **An overlay no longer hides other servers from discovery, and one bad overlay entry no
  > longer takes down the others.** Discovery weighs a keyword by how many servers declare
  > it, and it counted over the merged manifest. So an approved project overlay (or a user
  > or `PMCP_MANIFEST_PATH` overlay) whose server shared a keyword pushed every server with
  > that keyword below the match threshold. `gateway.catalog_search` then returned no
  > candidate, not even the shipped or user server. Keyword weights now come from the
  > shipped manifest alone, so an overlay can add candidates and never removes one, within
  > the result limit.
  >
  > Separately, a wrongly typed field in one overlay entry made `catalog_search` fail for
  > every query, or stopped gateway startup and `gateway.refresh` for every server.
  > Examples: `keywords: null`, a non-string `transport`, an int in `args`, or a bad
  > `cli_alternatives` entry. An overlay entry that any part of pmcp would reject is now
  > skipped when the overlay is read; an entry nothing would fail on still loads as
  > before. The warning names the field, never its value, and never shows an overlay
  > entry's name. Loading, discovery, CLI probing and the startup and refresh skip lines no
  > longer log an overlay entry's name or values either. A blank (`null`) field now means "not set" and
  > takes its default, instead of dropping the entry. See
  > [Consiliency/pmcp#342](https://github.com/Consiliency/pmcp/issues/342).

  Never put a closing keyword next to the number.
- `README.md` § "Private manifest overlay", the "fail-soft" paragraph (`README.md:1157`).
  Replace "a malformed file or a single bad entry logs a warning and is skipped" with:

  > "a malformed file logs a warning and is skipped; so does a single entry that pmcp could
  > not use: a field some part of pmcp would fail on, such as `keywords: [1]`, an `args`
  > holding a number, or a CLI alternative with an empty `check_command`. Fields pmcp only
  > tests for truth or ignores (`transport` without a `url`, `auto_start`, `status`) are
  > accepted as before. The
  > warning names the field, not its value, and does not show the entry's name unless pmcp
  > ships it. A blank field (`description:` with nothing after it) means 'not set'."

  Then add:

  > "An overlay's servers are found by their own keywords. Sharing a keyword with another
  > server never hides either one from `gateway.catalog_search` (within its result limit;
  > ties rank by name)."
- No `SECURITY.md` change. Consent semantics are unchanged (D5). D9 strengthens an existing
  property, that overlay keys and values are not logged, and adds no claim the security
  checker would need to track. Run `scripts/check_security_claims.py`.

## Dependencies & order

1. Applies to `c9206a9` as is (re-fetched for rev 2). Consiliency/pmcp#298 is already on
   main (PR 337). The other open change in this area is Consiliency/pmcp#297
   (`origin/plan/297-validation-echo` @ `48b7a89`, compared in rev 1):
   - Its `loader.py` hunks are the YAML parse sites, `_shipped_manifest_entries`, and
     `{exc}` → `exception_text(exc)` in the overlay skip lines.
   - Rev 2 rewrites those two skip lines (D9). Whichever lands second takes rev 2's
     wording, and may wrap `_rejection_reason(exc)` in `exception_text`, which leaves an
     already value-free string unchanged.
   - Nothing else overlaps: #297 has no hunk in `_parse_server_config`, `Manifest`,
     `matcher.py`, `config/loader.py`, `environment.py` or the handler loops changed here.
   - Whichever lands second re-runs M14–M16 and the field walk.
2. Within the plan, apply the source patch, the new module and the test migration
   together: the migration fails without the source patch, and the source patch fails the
   three migrated tests without it.
3. Docs last.

## Verification (measured this session on the rev-3 spike; the implementer re-runs each step)

Run from the worktree. Before running:
- A fresh worktree needs `uv sync --all-extras -p 3.10` first.
- On dev0, `unset npm_config_cache npm_config_store_dir pnpm_config_store_dir`.
- Keep `--basetemp` off `/tmp` and outside the checkout.
- Detach long runs with `setsid nohup … < /dev/null &`. A plain `nohup … & disown` mutation
  run died mid-mutant once this session and left a mutated file. It was restored from its
  saved copy.

```bash
# 1. The new module, red on main and on rev 1, green on rev 2
uv run pytest tests/test_catalog_overlay_discovery.py -q -p no:cacheprovider --no-cov --cov-fail-under=0
#   rev-3 spike: 131 passed
#   same file on main c9206a9:      73 failed, 58 passed  (the 58 include the must-load and
#                                   main-weighting tests, which pin main's behaviour)
#   same file on the rev-2 patch:   28 failed, 103 passed (exactly round 2's findings: B5, B6, N1)

# 2. Lint, format, types (CI's own commands)
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
#   -> All checks passed! / already formatted / Success: no issues found in 52 source files

# 3. Touched suites (23 files, named in "The test surface this touches")
#   -> **2329 passed, 1 skipped, 19 deselected, 0 failed** (185.48 s)

# 4. The real gateway, end to end (call.py; R1 table): unchanged from rev 1 on the rev-3 spike
#   -> unapproved demo→[useronly], screenshot→[playwright]; approved demo→[projonly, useronly], screenshot→[playwright, projonly], gadget→[projonly]; revoked as unapproved. The gateway log names neither overlay server: of its 108 `Registered lazy server` lines, the overlay one reads 'an overlay server (name not shown)'

# 5. R1 class and consumer differentials
python copycat.py      # main: 329 of 440 queries lose a candidate, 91 servers; rev 2: 0 of 440, 0 servers
python consumers.py    # rev 2: only additions (projonly) differ approved vs revoked

# 6. Mutation driver (34 mutants, appendix). RED requires failed > 0 and errors == 0.
#    Basetemp: $PMCP342_MUT_BASETEMP (default /mnt/workspace/users/viperjuice/pmcp-342-bt/mut)
python mutants.py "$PWD"
#   -> 34 of 34 mutants RED (a real test failure and no errors), every restore byte-identical (table below)

# 7. The full suite, once, alone, detached (CI command minus -v)
setsid nohup uv run pytest tests/ -q --tb=short --cov --cov-report= -p no:cacheprovider \
  --basetemp=$WORKTREE_ROOT/pmcp-342-bt/full2 > full2.log 2>&1 < /dev/null &
#   -> 5652 passed, 3 skipped, 80 deselected, 2 failed in 673.78s; coverage gate met. The 2 failures are environmental: tests/runtime/test_hang_diagnostics.py's two async-hang tests run a child pytest whose rootdir walk hits the unreadable /mnt/workspace/users (PermissionError), because --basetemp was under /mnt/workspace/users/viperjuice as asked. The same 2 fail on main c9206a9 with that basetemp (2 failed, 8 passed), and the module passes 10/10 on the spike with --basetemp under $WORKTREE_ROOT

# 8. Gates
python3 scripts/check_security_claims.py
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-342-catalog-overlay-*.md
#   -> OK, 129 cited node ids / blocking inconsistencies: 0

# 9. Embedding proof: extract the source patch, the test module and the migration patch from
#    THIS file; on a clean c9206a9 tree: git apply --check, git apply, write the module, run it
#   -> apply-check clean; source, module and migration cmp-identical to the spike; on clean c9206a9 the module plus the two migrated modules: 315 passed
```

## Acceptance criteria

- [ ] **R1 (unchanged from rev 1):**
  - With a user overlay and an approved project overlay sharing a keyword with each other
    and with a shipped server, `catalog_search` returns shipped + user + project
    candidates, and after `pmcp trust revoke` it returns shipped + user. Measured through
    the real CLI, loader and consent gate, and through a real gateway.
  - No overlay source changes any keyword weight (M1, M2).
  - The copycat removes no shipped server from any shipped-keyword query: 0 of 440 (M1,
    M2).
  - `match_capability` keeps its match.
  - Hand-built and explicit-path manifests keep main's weighting, and the floor mutant M4
    is red.
  - The config-server view keeps the base weights (M3).
- [ ] **R2, parse time:**
  - For every field of `ServerConfig` and `CLIAlternative`, and for every shape (null, int,
    bool, str, list, dict, a 100 KB string, `10**40`), no consumer raises:
    `catalog_search` (`include_offline` true and false), `request_capability`, startup and
    `gateway.refresh`.
  - The sibling entries and the shipped servers stay present.
  - No log record, at DEBUG, carries the value sentinel or the name sentinel (M5, M6, M9,
    M10, M12, M14–M16).
  - Nothing stricter than a consumer: the 11 must-load values main accepted and used still
    load and start as local, and a `transport: stdio` override keeps the override.
- [ ] **Logs (D9, exactly its scope):** with kept entries named and valued by sentinels,
  no record from loading, discovery, CLI probing, startup or refresh skip lines, or lazy
  registration carries either (L1–L6).
- [ ] **Main's weighting:** `keyword_weights` equals main's verbatim function on 40
  generated alias-heavy keyword sets, hand-built and explicit-path, and codex's repro picks
  `a-other` (M17).
- [ ] **Derived, not listed:** a stricter `CapabilityCandidate`, startup conversion or
  `CLIHint` is enforced at parse time with no change to the loader (M7, M8, M11; M6
  and M10 for the scoring and credential projections), and the
  control test shows the same entries load otherwise.
- [ ] **Null is absent** for every field. A blank `description:` keeps a user override of
  `github` (M13).
- [ ] **The second line:** each of G1–G11 contains a hand-built bad entry on its own, and
  each guard mutant is red.
- [ ] Every shipped server and CLI passes the parse-time checks.
- [ ] Three existing tests are migrated exactly as in the patch below; no other existing
  test changes.
- [ ] No query gives no manifest candidates in every consent state.
- [ ] The full suite passes; ruff, ruff format and mypy (CI's commands) are clean;
  `check_security_claims.py` is OK; `check_plan_consistency.py` reports 0 blocking.
- [ ] The CHANGELOG and README text is as above, with "within the result limit" and no
  closing keyword.

## Mutation table

Each mutant is an exact-text replacement that must match exactly once, in the file its rule
lives in. The driver (`mutants.py`, appendix):
1. applies the mutant;
2. runs `tests/test_catalog_overlay_discovery.py` (M4 also runs the existing generic-api
   test);
3. restores the file from a saved copy, never with git, and checks it with `cmp`.

A mutant is **RED only when at least one test fails and none errors** (round 1, F6). The
guard mutants narrow the guard's `except` to `ZeroDivisionError`. Run on the rev-3 spike
text embedded below (Python 3.10.21).

| id | mutant (the rule it breaks) | result | first failure | failing tests (first 4) |
|---|---|---|---|---|
| M1 | R1: scoring ignores the base weights (counts the merged manifest) | **RED** 37 failed, 94 passed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable, test_no_overlay_source_changes_a_keyword_weight[user], test_no_overlay_source_changes_a_keyword_weight[project], test_no_overlay_source_changes_a_keyword_weight[env], +33 more |
| M2 | R1: base weights computed after the overlays are merged | **RED** 37 failed, 94 passed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable, test_no_overlay_source_changes_a_keyword_weight[user], test_no_overlay_source_changes_a_keyword_weight[project], test_no_overlay_source_changes_a_keyword_weight[env], +33 more |
| M3 | R1: request_capability's config-server view drops the base weights | **RED** 1 failed, 130 passed (restored=True) | assert None is not None | test_the_config_server_view_keeps_the_base_weights |
| M4 | instance fix instead of the class: raise the IDF floor so a shared keyword passes | **RED** 40 failed, 92 passed (restored=True) | assert {'api': 0.7} == {'api': 0.5} | test_a_hand_built_manifest_is_still_weighted_by_its_own_servers, test_an_explicit_path_load_weights_that_file_alone, test_weights_equal_mains_for_hand_built_and_explicit_path_manifests[0], test_weights_equal_mains_for_hand_built_and_explicit_path_manifests[1], +36 more |
| M5 | R2: overlay servers not checked against their consumers | **RED** 26 failed, 105 passed (restored=True) | catalog_search: skipping an unusable manifest entry (server 'puppeteer'): ValidationError | test_every_server_field_with_every_bad_shape_is_contained[description], test_every_server_field_with_every_bad_shape_is_contained[keywords], test_every_server_field_with_every_bad_shape_is_contained[command], test_every_server_field_with_every_bad_shape_is_contained[args], +22 more |
| M7 | R2: server check skips the CapabilityCandidate catalog_search builds | **RED** 9 failed, 122 passed (restored=True) | catalog_search: skipping an unusable manifest entry (server 'puppeteer'): ValidationError | test_every_server_field_with_every_bad_shape_is_contained[description], test_every_server_field_with_every_bad_shape_is_contained[env_instructions], test_every_server_field_with_every_bad_shape_is_contained[transport], test_every_server_field_with_every_bad_shape_is_contained[declared_scopes], +5 more |
| M8 | R2: server check skips the startup/refresh conversion | **RED** 12 failed, 119 passed (restored=True) | Startup: skipping an unusable manifest entry (server 'puppeteer'): ValidationError | test_every_server_field_with_every_bad_shape_is_contained[command], test_every_server_field_with_every_bad_shape_is_contained[args], test_every_server_field_with_every_bad_shape_is_contained[transport], test_every_server_field_with_every_bad_shape_is_contained[headers], +8 more |
| M9 | R2: overlay CLI alternatives not checked against their consumers | **RED** 12 failed, 119 passed (restored=True) | rank_cli_hints: skipping an unusable entry (cli_alternative 'git'): TypeError | test_every_cli_field_with_every_bad_shape_is_contained[keywords], test_every_cli_field_with_every_bad_shape_is_contained[check_command], test_every_cli_field_with_every_bad_shape_is_contained[help_command], test_every_cli_field_with_every_bad_shape_is_contained[description], +8 more |
| M12 | R2: CLI check accepts an empty check_command (probe needs slot 0) | **RED** 2 failed, 129 passed (restored=True) | assert not ({'cli-check-empty', 'cli-check-int', 'cli-description-int', 'cli-examples-ints | test_the_board_repros_are_skipped_with_a_value_free_warning, test_a_cli_is_skipped_only_where_a_consumer_fails[entry3-False] |
| M13 | R2: a YAML null is a value, not absent | **RED** 1 failed, 130 passed (restored=True) | assert 'npx' == 'my-github-fork' | test_null_fields_take_their_defaults_and_keep_the_entry |
| M14 | R2: the skip reason quotes pydantic's text (values reach the log) | **RED** 20 failed, 111 passed (restored=True) | Skipping invalid server entry (server 'puppeteer') in overlay /mnt/workspace/users/viperju | test_every_server_field_with_every_bad_shape_is_contained[description], test_every_server_field_with_every_bad_shape_is_contained[env_instructions], test_every_server_field_with_every_bad_shape_is_contained[transport], test_every_server_field_with_every_bad_shape_is_contained[url], +16 more |
| M15 | R2: the server skip warning names the overlay entry | **RED** 22 failed, 109 passed (restored=True) | Skipping invalid server entry ('tok-NAME-sentinel-5f1c9a') in overlay /mnt/workspace/users | test_every_server_field_with_every_bad_shape_is_contained[description], test_every_server_field_with_every_bad_shape_is_contained[keywords], test_every_server_field_with_every_bad_shape_is_contained[install], test_every_server_field_with_every_bad_shape_is_contained[command], +18 more |
| M16 | R2: the CLI skip warning names the overlay entry | **RED** 7 failed, 124 passed (restored=True) | Skipping invalid cli_alternative ('tok-NAME-sentinel-5f1c9a') in overlay /mnt/workspace/us | test_every_cli_field_with_every_bad_shape_is_contained[keywords], test_every_cli_field_with_every_bad_shape_is_contained[check_command], test_every_cli_field_with_every_bad_shape_is_contained[help_command], test_every_cli_field_with_every_bad_shape_is_contained[description], +3 more |
| G1 | guard: keyword_weights lets one server's keywords fail the table | **RED** 3 failed, 128 passed (restored=True) | 'NoneType' object is not iterable | test_guard_keyword_weights_skip_an_unusable_server, test_guard_catalog_scoring_skips_an_unusable_server, test_guard_match_capability_skips_an_unusable_server |
| G2 | guard: catalog_search scoring lets one server raise | **RED** 1 failed, 130 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_catalog_scoring_skips_an_unusable_server |
| G3 | guard: catalog_search candidate build lets one server raise | **RED** 1 failed, 130 passed (restored=True) | 1 validation error for CapabilityCandidate | test_guard_catalog_candidate_build_skips_an_unusable_server |
| G4 | guard: rank_cli_hints lets one CLI raise | **RED** 1 failed, 130 passed (restored=True) | 'NoneType' object is not iterable | test_guard_rank_cli_hints_skips_an_unusable_cli |
| G5 | guard: probe_clis lets one command raise | **RED** 1 failed, 130 passed (restored=True) | list index out of range | test_guard_probe_clis_treats_an_unusable_command_as_not_detected |
| G6 | guard: startup/refresh let one manifest server abort resolution | **RED** 2 failed, 129 passed (restored=True) | 1 validation error for LocalMcpServerConfig | test_guard_startup_skips_an_unusable_server, test_guard_refresh_survives_an_unusable_server |
| G7 | guard: match_capability lets one server raise | **RED** 1 failed, 130 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_match_capability_skips_an_unusable_server |
| G8 | guard: request_capability name tier lets one name raise | **RED** 1 failed, 130 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_request_capability_name_tier_skips_an_unusable_name |
| G9 | guard: request_capability name match lets its entry raise | **RED** 1 failed, 130 passed (restored=True) | str expected, not int | test_guard_request_capability_name_match_skips_an_unusable_entry |
| G10 | guard: request_capability category keywords let one server raise | **RED** 1 failed, 130 passed (restored=True) | 'NoneType' object is not iterable | test_guard_request_capability_category_keywords_skip_an_unusable_server |
| G11 | guard: request_capability category candidates let one server raise | **RED** 1 failed, 130 passed (restored=True) | 1 validation error for CapabilityCandidate | test_guard_request_capability_category_candidate_skips_an_unusable_server |
| M6 | R2: check skips the name/keyword scoring every discovery path runs | **RED** 2 failed, 129 passed (restored=True) | catalog_search: skipping an unusable manifest entry (server 'puppeteer'): TypeError | test_every_server_field_with_every_bad_shape_is_contained[keywords], test_the_board_repros_are_skipped_with_a_value_free_warning |
| M10 | R2: check skips the credential lookups every candidate runs | **RED** 2 failed, 129 passed (restored=True) | catalog_search: skipping an unusable manifest entry (server 'puppeteer'): TypeError | test_every_server_field_with_every_bad_shape_is_contained[secret_key], test_the_board_repros_are_skipped_with_a_value_free_warning |
| M11 | R2: CLI check skips the _rank_one_cli scoring and CLIHint build | **RED** 11 failed, 120 passed (restored=True) | rank_cli_hints: skipping an unusable entry (cli_alternative 'git'): TypeError | test_every_cli_field_with_every_bad_shape_is_contained[keywords], test_every_cli_field_with_every_bad_shape_is_contained[check_command], test_every_cli_field_with_every_bad_shape_is_contained[help_command], test_every_cli_field_with_every_bad_shape_is_contained[description], +7 more |
| M17 | R1: keyword counting de-duplicates after normalising (not main's) | **RED** 12 failed, 119 passed (restored=True) | assert {'db': 0.5, '...ch': 0.5, ...} == {'db': 0.5, '...ch': 0.5, ...} | test_weights_equal_mains_for_hand_built_and_explicit_path_manifests[6], test_weights_equal_mains_for_hand_built_and_explicit_path_manifests[7], test_weights_equal_mains_for_hand_built_and_explicit_path_manifests[8], test_weights_equal_mains_for_hand_built_and_explicit_path_manifests[13], +8 more |
| L1 | logs: the self-relaxing credential warning echoes the variable | **RED** 2 failed, 129 passed (restored=True) |  | test_a_self_relaxing_credential_is_refused_without_its_value, test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| L2 | logs: probe_clis names each detected CLI | **RED** 2 failed, 129 passed (restored=True) | assert ['Detected CL...ame-77ab-cli'] == [] | test_probe_clis_never_logs_an_overlay_cli_name, test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| L3 | logs: probe_clis' summary names the detected CLIs | **RED** 2 failed, 129 passed (restored=True) | assert ['Detected 9 ...ame-77ab-cli'] == [] | test_probe_clis_never_logs_an_overlay_cli_name, test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| L4 | logs: lazy registration names an overlay server | **RED** 1 failed, 130 passed (restored=True) | assert ['Registered ...EL-name-77ab'] == [] | test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| L5 | logs: startup/refresh skip lines name an overlay server | **RED** 1 failed, 130 passed (restored=True) |  | test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| L6 | logs: startup/refresh skip lines print an overlay env_var | **RED** 1 failed, 130 passed (restored=True) | assert ['Skipping st...ager startup'] == [] | test_no_overlay_name_or_value_reaches_any_log_on_these_paths |

**34 of 34 mutants RED (a real test failure and no errors), every restore byte-identical.**

## Non-goals

- **Search quality for generic keywords.** `browser` returns no candidate even for
  `playwright`, because 5 servers share it. This is shipped-only and overlay-independent.
- **Breaking ties in favour of shipped servers.** Five tied overlay servers can fill the
  default limit by name (D10). Changing that is a ranking-policy change of its own.
- **Coercing lax values.** `requires_api_key: "no"` passes pydantic's lax `bool` check and
  keeps today's truthy behaviour. Normalising it would change already-valid entries'
  behaviour (D4).
- **Checking the shipped manifest at load time.** Its parse has no per-entry skip, so a
  failure there would take the gateway down. A test holds it to the same rule instead (D4).
- **Running an overlay CLI's `check_command`.** `probe_clis` executes it, as it does on
  main; an approved project overlay can already name any command. This plan only makes a
  malformed one harmless. Whether probing overlay CLIs should need its own consent is a
  separate question.
- **Making overlay-only servers reachable by `request_capability`'s category tier.** The
  category map is a fixed list of shipped names, and approval does not change it.

## Unverified

- **The reviewer's exact overlay.** It was not recorded. Both causes produce the reported
  symptom, and both are fixed and tested.
- **Python 3.11 and 3.12.** The new code builds pydantic models and runs pmcp's own
  scorers, and the tests use `field_validator`. These behave the same across the CI matrix.
  The module was run on 3.10.21 only.
- **`tests/runtime/test_hang_diagnostics.py` under the requested basetemp.** Its two
  async-hang tests run a child pytest whose rootdir walk hits the unreadable
  `/mnt/workspace/users`. They fail on main and on the spike alike with
  `--basetemp=/mnt/workspace/users/viperjuice/…`, and pass 10/10 on the spike with
  `--basetemp` under `$WORKTREE_ROOT`. The implementer's full-suite run should use a
  basetemp whose ancestors are all readable and contain no `.git`.
- **A real gateway with a malformed overlay.** R2 was measured through `GatewayTools`,
  `resolve_startup_configs` and `gateway.refresh` on the real loader, not through a
  started `pmcp --transport http`. R1 was measured through both.

## Execution Policy

- execute: effort=medium.
- reason: five source files, about 760 changed lines, on the discovery, startup and
  refresh paths. There is one behaviour change an operator can see: an overlay entry with a
  wrong type is now skipped instead of loading and failing later. Its warning names the
  field.
- Apply the source patch, the new module and the test migration verbatim. Re-run
  Verification 1–3, 6 and 7 against the real tree, and measure main in the same window
  before claiming any number.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below as `342-src.patch` and `git apply` it on `c9206a9`.
2. Write the test module below to `tests/test_catalog_overlay_discovery.py`.
3. `git apply` the test-migration patch below.
4. Add the CHANGELOG and README text by hand, as in *Documentation impact*.

### Patch — source (`src/pmcp/manifest/{loader,matcher,environment}.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/config/loader.py`)

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index cae5645..021fd7f 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -70,6 +70,21 @@ except ImportError:
 
 logger = logging.getLogger(__name__)
 
+
+def _lazy_log_name(config: ResolvedServerConfig) -> str:
+    """A lazily registered server's name in a log line (Consiliency/pmcp#342).
+
+    A manifest-derived server is named only if pmcp ships that name; an
+    overlay key may be anything an operator pasted. A ``.mcp.json`` entry is
+    the operator's own config and is named as before.
+    """
+    from pmcp.manifest.loader import entry_log_name
+
+    return entry_log_name(
+        config.name, manifest_derived=getattr(config, "source", None) == "manifest"
+    )
+
+
 #: Executables that fetch and run the package they are given at spawn time,
 #: by NORMALIZED name (`normalized_executable_name`), so ``UVX.EXE``,
 #: ``pnpx.cmd`` and ``C:\\tools\\npx.cmd`` are runners without being listed.
@@ -1269,7 +1284,7 @@ class ClientManager:
                 status=ServerStatusEnum.LAZY,
                 tool_count=0,
             )
-            logger.info(f"Registered lazy server: {name}")
+            logger.info(f"Registered lazy server: {_lazy_log_name(config)}")
 
     def prune_lazy_configs(self, keep_names: set[str]) -> None:
         """Drop on-demand (lazy) configs whose name is not in ``keep_names``.
diff --git a/src/pmcp/config/loader.py b/src/pmcp/config/loader.py
index c71e7f8..9e0ea3b 100644
--- a/src/pmcp/config/loader.py
+++ b/src/pmcp/config/loader.py
@@ -1349,6 +1349,37 @@ def is_legacy_manifest_auto_start_enabled(
     return values.get("PMCP_LEGACY_MANIFEST_AUTOSTART") == "1"
 
 
+def startup_skip_message(phase: str, skipped: StartupSkip) -> str:
+    """The log line for one skipped startup/refresh entry.
+
+    Shared by gateway startup and ``gateway.refresh``. A manifest-derived entry
+    (``manifest``, ``provisioned``) is named only if pmcp ships it, and a
+    credential variable is named only when pmcp's own entry declares it: an
+    overlay's key and ``env_var`` are free text (Consiliency/pmcp#342, D9).
+    """
+    from pmcp.manifest.loader import entry_log_name, shipped_env_var
+
+    manifest_derived = skipped.source in ("manifest", "provisioned")
+    who = entry_log_name(skipped.name, manifest_derived=manifest_derived)
+    head = f"Skipping {phase} entry {who} from {skipped.source}: "
+    if skipped.reason == StartupSkipReason.MISSING_AUTH:
+        # A configured remote entry's missing header variables come from the
+        # operator's own .mcp.json; any other variable name is shown only if
+        # pmcp's own entry declares it.
+        if skipped.source == "configured" and skipped.missing_env_vars:
+            shown: str | None = skipped.env_var
+        else:
+            shown = shipped_env_var(skipped.name, skipped.env_var)
+        target = shown or "its credential variable"
+        return f"{head}missing_auth; set {target} to enable eager startup"
+    if skipped.reason == StartupSkipReason.UNKNOWN_AUTO_START:
+        return (
+            f"{head}unknown_auto_start; add a matching mcpServers entry or "
+            "remove it from autoStart"
+        )
+    return f"{head}{skipped.reason.value}"
+
+
 def _coerce_manifest_servers(
     manifest_servers: Mapping[str, "ManifestServerConfig"]
     | Iterable["ManifestServerConfig"]
@@ -1627,11 +1658,22 @@ def resolve_startup_configs(
         # so eager/lazy/refresh spawns would launch without the credential.
         # Resolving here keeps both this path and the connect path symmetric, so
         # the refresh diff (issue #79) still sees no spurious env change.
-        config = _manifest_server_to_config(server, os.environ.get)
         source: Literal["manifest", "provisioned"] = (
             "provisioned" if name in provisioned else "manifest"
         )
-        add_config(config, eager=eager, source=source, manifest_server=server)
+        # One manifest entry must never abort startup or gateway.refresh for
+        # every server (Consiliency/pmcp#342). The loader already skips an
+        # overlay entry this conversion would reject; this is the second line.
+        try:
+            config = _manifest_server_to_config(server, os.environ.get)
+            add_config(config, eager=eager, source=source, manifest_server=server)
+        except Exception as exc:
+            from pmcp.manifest.loader import _server_label
+
+            logger.warning(
+                f"Startup: skipping an unusable manifest entry "
+                f"({_server_label(name)}): {type(exc).__name__}"
+            )
 
     known_names = configured_names | set(manifest_by_name)
     for name in sorted(enabled - known_names):
diff --git a/src/pmcp/manifest/environment.py b/src/pmcp/manifest/environment.py
index 613559f..23bb159 100644
--- a/src/pmcp/manifest/environment.py
+++ b/src/pmcp/manifest/environment.py
@@ -58,6 +58,13 @@ def detect_platform() -> Platform:
         return "linux"
 
 
+def _label(name: object) -> str:
+    """A CLI's name in a log line: shown only if pmcp ships it (Consiliency/pmcp#342)."""
+    from pmcp.manifest.loader import _cli_label
+
+    return _cli_label(name)
+
+
 async def check_cli(name: str, check_command: list[str]) -> CLIInfo | None:
     """Check if a CLI is available and get its info."""
     # First check if command exists in PATH
@@ -84,10 +91,10 @@ async def check_cli(name: str, check_command: list[str]) -> CLIInfo | None:
             return CLIInfo(name=name, path=path)
 
     except asyncio.TimeoutError:
-        logger.debug(f"Timeout checking CLI: {name}")
+        logger.debug(f"Timeout checking CLI: {_label(name)}")
         return CLIInfo(name=name, path=path)
     except Exception as e:
-        logger.debug(f"Error checking CLI {name}: {e}")
+        logger.debug(f"Error checking CLI {_label(name)}: {type(e).__name__}")
         return None
 
 
@@ -108,10 +115,10 @@ async def get_cli_help(
         return "\n".join(lines)
 
     except asyncio.TimeoutError:
-        logger.debug(f"Timeout getting help for: {name}")
+        logger.debug(f"Timeout getting help for: {_label(name)}")
         return None
     except Exception as e:
-        logger.debug(f"Error getting help for {name}: {e}")
+        logger.debug(f"Error getting help for {_label(name)}: {type(e).__name__}")
         return None
 
 
@@ -120,8 +127,17 @@ async def probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
     detected: dict[str, CLIInfo] = {}
 
     async def check_one(name: str, config: dict) -> tuple[str, CLIInfo | None]:
-        check_cmd = config.get("check_command", [name, "--version"])
-        result = await check_cli(name, check_cmd)
+        # One unusable entry (an empty or non-string command) is "not
+        # detected", never an exception that fails every probe
+        # (Consiliency/pmcp#342).
+        try:
+            check_cmd = config.get("check_command", [name, "--version"])
+            result = await check_cli(name, check_cmd)
+        except Exception as exc:
+            logger.warning(
+                f"probe_clis: skipping an unusable check_command: {type(exc).__name__}"
+            )
+            return name, None
         return name, result
 
     # Check all CLIs in parallel
@@ -131,9 +147,11 @@ async def probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
     for name, info in results:
         if info:
             detected[name] = info
-            logger.debug(f"Detected CLI: {name} at {info.path}")
+            logger.debug(f"Detected CLI: {_label(name)}")
 
-    logger.info(f"Detected {len(detected)} CLIs: {', '.join(detected.keys())}")
+    logger.info(
+        f"Detected {len(detected)} CLIs: {', '.join(_label(n) for n in detected)}"
+    )
     return detected
 
 
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index aa5ec3d..dd30d2a 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -91,6 +91,31 @@ def _shipped_manifest_entries() -> dict[str, dict[str, Any]]:
     }
 
 
+def entry_log_name(name: object, *, manifest_derived: bool) -> str:
+    """How a log line names a server that may come from an overlay.
+
+    A ``.mcp.json`` entry is the operator's own config and is named as before;
+    a manifest-derived one is named only if pmcp ships that name
+    (``_server_label``), because an overlay key may be anything an operator
+    pasted (Consiliency/pmcp#342, D9).
+    """
+    if manifest_derived:
+        return _server_label(name)
+    return f"'{name}'"
+
+
+def shipped_env_var(name: object, env_var: object) -> str | None:
+    """``env_var`` if it is the variable pmcp's own entry for ``name`` declares.
+
+    A log line may print a credential variable's NAME only when pmcp wrote it;
+    an overlay's ``env_var`` is free text (Consiliency/pmcp#342, D9).
+    """
+    if not isinstance(name, str) or not isinstance(env_var, str):
+        return None
+    entry = _shipped_manifest_entries().get(name) or {}
+    return env_var if entry.get("env_var") == env_var else None
+
+
 def _server_label(name: object) -> str:
     """How a version-pin log line names a server (Consiliency/pmcp#294 piece 1).
 
@@ -415,6 +440,25 @@ def requires_credential(
     return credential_requirement(server, child_env=child_env).required
 
 
+def _category_keyword_norms(server: ServerConfig) -> list[str]:
+    """``server``'s normalized keywords, or none if they are unusable.
+
+    One replaced category server with bad keywords must not take down
+    ``request_capability``'s category tier for every query
+    (Consiliency/pmcp#342).
+    """
+    try:
+        return [
+            kw.lower().replace("-", " ").replace("_", " ") for kw in server.keywords
+        ]
+    except Exception as exc:
+        logger.warning(
+            f"request_capability: skipping unusable keywords "
+            f"({_server_label(server.name)}): {type(exc).__name__}"
+        )
+        return []
+
+
 # Category taxonomy used by Manifest.get_category_summary() and get_servers_in_category()
 _CATEGORY_MAP: dict[str, list[str]] = {
     "browser automation": [
@@ -512,6 +556,17 @@ class Manifest:
     cli_alternatives: dict[str, CLIAlternative]
     servers: dict[str, ServerConfig]
     discovery_queue_path: str
+    # Keyword weights of the BASE manifest only -- the shipped document, or the
+    # explicit path -- computed before any overlay is merged
+    # (Consiliency/pmcp#342). Discovery scores a keyword by how many servers
+    # share it; counted over the merged manifest, an overlay server that
+    # shares a keyword halved its weight for every other server and pushed
+    # them all below the match threshold, so approving a project overlay hid
+    # shipped and user servers from `catalog_search`. ``None`` for a Manifest
+    # built by hand, whose weights come from its own servers.
+    base_keyword_weights: dict[str, float] | None = field(
+        default=None, compare=False, repr=False
+    )
 
     def get_auto_start_servers(self) -> list[ServerConfig]:
         """Get servers configured for auto-start."""
@@ -572,8 +627,7 @@ class Manifest:
                 server = self.servers.get(sname)
                 if not server:
                     continue
-                for kw in server.keywords:
-                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
+                for kw_norm in _category_keyword_norms(server):
                     kw_cats.setdefault(kw_norm, set()).add(cat_name)
 
         def _kw_weight(kw_norm: str) -> float:
@@ -601,8 +655,7 @@ class Manifest:
                 server = self.servers.get(sname)
                 if not server:
                     continue
-                for kw in server.keywords:
-                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
+                for kw_norm in _category_keyword_norms(server):
                     if set(kw_norm.split()).issubset(query_words):
                         score += _kw_weight(kw_norm)
 
@@ -642,8 +695,193 @@ class Manifest:
         return matching_clis, matching_servers
 
 
+def keyword_weights(servers: Iterable[ServerConfig]) -> dict[str, float]:
+    """Inverse document frequency of each normalized keyword, floored at 0.5.
+
+    A keyword one server declares weighs 1.0; one that N servers share weighs
+    max(1/N, 0.5). A keyword repeated verbatim within one server counts once;
+    two spellings that normalise alike (``alpha-beta``, ``alpha_beta``) count
+    twice. That is main's counting, kept exactly: the function moved here from
+    ``matcher.py`` unchanged apart from the per-server guard (Consiliency/pmcp#342).
+    """
+    frequencies: dict[str, int] = {}
+    for server in servers:
+        # A server whose keywords are unusable contributes none, rather than
+        # costing every other server its weights (Consiliency/pmcp#342).
+        try:
+            norms = [
+                keyword.lower().replace("-", " ").replace("_", " ")
+                for keyword in set(server.keywords)
+            ]
+        except Exception:
+            continue
+        for keyword_norm in norms:
+            frequencies[keyword_norm] = frequencies.get(keyword_norm, 0) + 1
+    return {
+        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()
+    }
+
+
+class _EntryRejected(ValueError):
+    """An overlay entry a consumer would reject. Its message names fields and
+    types only, never a value or the entry's name."""
+
+
+def _without_nulls(data: Any) -> dict[str, Any]:
+    """``data`` without its ``null`` fields, so each takes its default."""
+    if not isinstance(data, dict):
+        raise _EntryRejected(f"the entry is a {type(data).__name__}, not a mapping")
+    return {key: value for key, value in data.items() if value is not None}
+
+
+def manifest_candidate_fields(server: ServerConfig) -> dict[str, Any]:
+    """The fields a manifest ``CapabilityCandidate`` copies from ``server``.
+
+    ``_manifest_candidates_for_query`` builds its candidates from this, and the
+    overlay check (``_check_server_for_consumers``) builds one from it too, so
+    a field added to the candidate is checked at parse time without a list to
+    keep in step (Consiliency/pmcp#342).
+    """
+    return {
+        "reasoning": server.description,
+        "transport": server.transport,
+        "url": server.url,
+        "package": server.package,
+        "server_card_url": server.server_card_url,
+        "declared_scopes": server.declared_scopes,
+        "declared_capabilities": server.declared_capabilities,
+    }
+
+
+def cli_hint_fields(cli: CLIAlternative) -> dict[str, Any]:
+    """The fields a ``CLIHint`` copies from ``cli`` (shared like the above)."""
+    return {
+        "name": cli.name,
+        "description": cli.description,
+        "check_command": cli.check_command,
+        "help_command": cli.help_command,
+        "examples": cli.examples,
+        "prefer_mcp_for": cli.prefer_mcp_for,
+    }
+
+
+def normalized_server_name(name: str) -> str:
+    """How discovery compares a server name with a query window.
+
+    ``catalog_search``'s name match and ``request_capability``'s name tier both
+    use this, and so does the overlay check, so a key they cannot normalise is
+    skipped at parse time.
+    """
+    return name.lower().replace("-", "").replace("_", "").replace(" ", "")
+
+
+def _check_server_for_consumers(server: ServerConfig) -> None:
+    """Raise if any consumer would fail on ``server``; otherwise do nothing.
+
+    A wrong type in one overlay entry did not stay in that entry: consumers
+    iterate all servers, so it raised in ``catalog_search`` for every query, or
+    aborted startup and ``gateway.refresh`` for every server
+    (Consiliency/pmcp#342). The check runs what the consumers run, and nothing
+    stricter (revision 3: a stricter check dropped working entries, such as
+    ``transport: stdio`` with no ``url``, which main starts as local):
+
+    * the name and keyword scoring every discovery path runs on every server
+      (``normalized_server_name``, ``matcher._keyword_match_score``);
+    * the credential lookups every candidate runs (``requires_credential``,
+      and an environment read of each ``credential_lookup_keys`` key);
+    * the ``CapabilityCandidate`` that ``catalog_search`` builds, from the same
+      ``manifest_candidate_fields`` the handler uses;
+    * the ``Local``/``RemoteMcpServerConfig`` that startup, refresh,
+      provisioning and lazy connects build, through the same
+      ``config.loader._manifest_server_to_config``, with no env value used.
+    """
+    from pmcp.config.loader import _manifest_server_to_config
+    from pmcp.manifest.matcher import _keyword_match_score
+    from pmcp.types import CapabilityCandidate
+
+    normalized_server_name(server.name)
+    _keyword_match_score("probe", server.keywords)
+    requires_credential(server)
+    for key in credential_lookup_keys(server):
+        os.environ.get(key)
+    CapabilityCandidate(
+        name=server.name,
+        candidate_type="server",
+        relevance_score=0.0,
+        env_var=server.env_var,
+        env_instructions=server.env_instructions,
+        **manifest_candidate_fields(server),
+    )
+    _manifest_server_to_config(server, lambda _key: None)
+
+
+def _check_cli_for_consumers(cli: CLIAlternative) -> None:
+    """Raise if any consumer would fail on ``cli`` (see the server check).
+
+    ``rank_cli_hints`` scores every CLI on every query and builds a ``CLIHint``:
+    the check runs that same ``_rank_one_cli``. ``probe_clis`` runs
+    ``check_command`` and needs a program in slot 0.
+    """
+    from pmcp.manifest.matcher import _rank_one_cli
+
+    _rank_one_cli(
+        "probe",
+        "probe",
+        {"probe"},
+        cli.name,
+        cli,
+        is_available=False,
+        detected_infos={},
+        include_suppressed=True,
+        min_score=-1.0,
+    )
+    if not cli.check_command:
+        raise _EntryRejected("'check_command' names no program")
+
+
+def _rejection_reason(exc: BaseException) -> str:
+    """Why an entry was skipped, without any value or name from the entry.
+
+    A pydantic error's text quotes the input, so only the field (the first
+    location element, a model attribute name) and the error type are kept.
+    """
+    from pydantic import ValidationError
+
+    if isinstance(exc, _EntryRejected):
+        return str(exc)
+    if isinstance(exc, ValidationError):
+        parts = []
+        for error in exc.errors(include_url=False, include_input=False):
+            loc = error.get("loc") or ("entry",)
+            head = str(loc[0])
+            if not re.fullmatch(r"[a-z_]{1,64}", head):
+                head = "entry"
+            parts.append(f"'{head}' {error.get('type', 'invalid')}")
+        return "; ".join(sorted(set(parts))) or "invalid"
+    return f"{type(exc).__name__} while parsing"
+
+
+@functools.lru_cache(maxsize=1)
+def _shipped_cli_names() -> frozenset[str]:
+    """Names of pmcp's own shipped CLI alternatives (see ``_server_label``)."""
+    try:
+        data = _parse_trusted_yaml(_SHIPPED_MANIFEST_PATH.read_bytes()) or {}
+        clis = data.get("cli_alternatives") or {}
+    except (OSError, yaml.YAMLError, AttributeError):
+        return frozenset()
+    return frozenset(name for name in clis if isinstance(name, str))
+
+
+def _cli_label(name: object) -> str:
+    """How a log line names a CLI alternative: shipped names only."""
+    if isinstance(name, str) and name in _shipped_cli_names():
+        return f"cli_alternative '{name}'"
+    return "an overlay cli_alternative (name not shown)"
+
+
 def _parse_cli_alternative(name: str, data: dict[str, Any]) -> CLIAlternative:
-    """Parse a CLI alternative from raw YAML data."""
+    """Parse a CLI alternative from raw YAML data (``null`` means absent)."""
+    data = _without_nulls(data)
     return CLIAlternative(
         name=name,
         keywords=data.get("keywords", []),
@@ -671,14 +909,16 @@ def _parse_extra_env(
     if raw is None:
         return {}
     if not isinstance(raw, dict):
-        logger.warning(f"Ignoring '{field_label}' for server '{name}': not a mapping")
+        logger.warning(
+            f"Ignoring '{field_label}' for {_server_label(name)}: not a mapping"
+        )
         return {}
 
     parsed: dict[str, str] = {}
     for key, value in raw.items():
         if not isinstance(key, str) or not key:
             logger.warning(
-                f"Skipping non-string '{field_label}' key for server '{name}'"
+                f"Skipping non-string '{field_label}' key for {_server_label(name)}"
             )
             continue
         if isinstance(value, bool):
@@ -687,7 +927,7 @@ def _parse_extra_env(
             parsed[key] = str(value)
         else:
             logger.warning(
-                f"Skipping '{field_label}' key '{key}' for server '{name}': "
+                f"Skipping a '{field_label}' key for {_server_label(name)}: "
                 f"unsupported value type {type(value).__name__}"
             )
     return parsed
@@ -708,7 +948,7 @@ def _parse_api_key_optional_when(
         return []
     if not isinstance(raw, list):
         logger.warning(
-            f"Ignoring 'api_key_optional_when' for server '{name}': not a list"
+            f"Ignoring 'api_key_optional_when' for {_server_label(name)}: not a list"
         )
         return []
 
@@ -716,13 +956,14 @@ def _parse_api_key_optional_when(
     for item in raw:
         if not isinstance(item, str) or not item:
             logger.warning(
-                f"Skipping non-string 'api_key_optional_when' entry for server '{name}'"
+                f"Skipping non-string 'api_key_optional_when' entry for "
+                f"{_server_label(name)}"
             )
             continue
         if item == env_var or item == secret_key:
             logger.warning(
-                f"Server '{name}' names its own credential variable "
-                f"('{item}') in 'api_key_optional_when'; ignoring — a "
+                f"The entry for {_server_label(name)} names its own credential "
+                f"variable in 'api_key_optional_when'; ignoring it — a "
                 f"credential cannot relax itself"
             )
             continue
@@ -994,7 +1235,14 @@ def _materialize_version_pin_soft(server: ServerConfig) -> ServerConfig:
 
 
 def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
-    """Parse a server config from raw YAML data."""
+    """Parse a server config from raw YAML data.
+
+    A YAML ``null`` means the field is absent and takes its default, for every
+    field (Consiliency/pmcp#342): a blank ``description:`` must not cost an
+    override its entry. Types are not checked here; an overlay entry is checked
+    against its consumers by ``_check_server_for_consumers``.
+    """
+    data = _without_nulls(data)
     install_data = data.get("install", {})
     install: dict[Platform, list[str]] = {}
 
@@ -1217,11 +1465,15 @@ def _parse_overlay_document(
     if isinstance(raw_servers, dict):
         for name, server_data in raw_servers.items():
             try:
-                servers[name] = _parse_server_config(name, server_data)
+                server = _parse_server_config(name, server_data)
+                _check_server_for_consumers(server)
             except Exception as exc:
                 logger.warning(
-                    f"Skipping invalid server entry '{name}' in overlay {path}: {exc}"
+                    f"Skipping invalid server entry ({_server_label(name)}) in "
+                    f"overlay {path}: {_rejection_reason(exc)}"
                 )
+                continue
+            servers[name] = server
     elif raw_servers:
         logger.warning(f"Skipping 'servers' in overlay {path}: not a mapping")
 
@@ -1230,12 +1482,15 @@ def _parse_overlay_document(
     if isinstance(raw_clis, dict):
         for name, cli_data in raw_clis.items():
             try:
-                cli_alternatives[name] = _parse_cli_alternative(name, cli_data)
+                cli = _parse_cli_alternative(name, cli_data)
+                _check_cli_for_consumers(cli)
             except Exception as exc:
                 logger.warning(
-                    f"Skipping invalid cli_alternative '{name}' in overlay "
-                    f"{path}: {exc}"
+                    f"Skipping invalid cli_alternative ({_cli_label(name)}) in "
+                    f"overlay {path}: {_rejection_reason(exc)}"
                 )
+                continue
+            cli_alternatives[name] = cli
     elif raw_clis:
         logger.warning(f"Skipping 'cli_alternatives' in overlay {path}: not a mapping")
 
@@ -1525,6 +1780,11 @@ def _build_manifest(
     for name, server_data in data.get("servers", {}).items():
         servers[name] = _parse_server_config(name, server_data)
 
+    # Weights come from the base alone, before any overlay
+    # (Consiliency/pmcp#342): an overlay may add or replace servers, but it
+    # never changes how much another server's keyword counts.
+    base_weights = keyword_weights(servers.values())
+
     # Merge private/custom overlays over the shipped manifest (default path only).
     if apply_overlays:
         for source in overlays:
@@ -1572,7 +1832,7 @@ def _build_manifest(
                 if name in servers:
                     logger.warning(
                         f"Manifest overlay ({label}) from {overlay_path} overrides "
-                        f"existing server '{name}'"
+                        f"existing {_server_label(name)}"
                     )
             servers.update(overlay_servers)
             cli_alternatives.update(overlay_clis)
@@ -1585,7 +1845,8 @@ def _build_manifest(
                 if existing is None:
                     logger.warning(
                         f"Manifest overlay ({label}) from {overlay_path} has a "
-                        f"'server_env' patch for unknown server '{name}': skipped"
+                        f"'server_env' patch for an unknown server "
+                        f"({_server_label(name)}): skipped"
                     )
                     continue
                 servers[name] = replace(
@@ -1619,6 +1880,7 @@ def _build_manifest(
         discovery_queue_path=data.get(
             "discovery_queue_path", ".mcp-gateway/discovery_queue.json"
         ),
+        base_keyword_weights=base_weights,
     )
 
     logger.info(
diff --git a/src/pmcp/manifest/matcher.py b/src/pmcp/manifest/matcher.py
index f9dd835..4cc222b 100644
--- a/src/pmcp/manifest/matcher.py
+++ b/src/pmcp/manifest/matcher.py
@@ -8,7 +8,15 @@ from dataclasses import dataclass
 from typing import Literal
 
 from pmcp.manifest.environment import CLIInfo
-from pmcp.manifest.loader import CLIAlternative, Manifest, ServerConfig
+from pmcp.manifest.loader import (
+    CLIAlternative,
+    Manifest,
+    ServerConfig,
+    _cli_label,
+    _server_label,
+    cli_hint_fields,
+    keyword_weights,
+)
 from pmcp.types import CLIHint
 
 logger = logging.getLogger(__name__)
@@ -88,15 +96,75 @@ def _keyword_match_score(
 
 
 def _manifest_keyword_weights(manifest: Manifest) -> dict[str, float]:
-    frequencies: dict[str, int] = {}
-    for server in manifest.servers.values():
-        for keyword in set(server.keywords):
-            keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
-            frequencies[keyword_norm] = frequencies.get(keyword_norm, 0) + 1
+    """Keyword weights for discovery scoring.
+
+    A manifest from ``load_manifest`` carries the weights of its base alone, so
+    an overlay server never lowers another server's score (Consiliency/pmcp#342).
+    A keyword only an overlay declares is absent and scores at the default 1.0.
+    A hand-built Manifest has no base and is weighted by its own servers.
+    """
+    if manifest.base_keyword_weights is not None:
+        return dict(manifest.base_keyword_weights)
+    return keyword_weights(manifest.servers.values())
 
-    return {
-        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()
-    }
+
+def _rank_one_cli(
+    query: str,
+    query_norm: str,
+    query_words: set[str],
+    name: str,
+    cli: CLIAlternative,
+    *,
+    is_available: bool,
+    detected_infos: Mapping[str, CLIInfo],
+    include_suppressed: bool,
+    min_score: float,
+) -> CLIHintMatch | None:
+    """Score one CLI alternative; ``None`` when it does not qualify."""
+    score = 0.0
+    score = max(score, _text_match_score(query_norm, query_words, cli.name))
+    score = max(
+        score,
+        _text_match_score(query_norm, query_words, cli.description) * 0.7,
+    )
+    score = max(score, _keyword_match_score(query, cli.keywords))
+    for example in cli.examples:
+        score = max(score, _text_match_score(query_norm, query_words, example) * 0.8)
+
+    matched_prefer_mcp_phrase = None
+    for phrase in cli.prefer_mcp_for:
+        if _text_match_score(query_norm, query_words, phrase) >= 1.0:
+            matched_prefer_mcp_phrase = phrase
+            score = max(score, 1.0)
+            break
+
+    if score < min_score:
+        return None
+
+    suppressed = matched_prefer_mcp_phrase is not None
+    if suppressed and not include_suppressed:
+        return None
+
+    path = detected_infos[name].path if name in detected_infos else None
+    reason = "Available on PATH" if is_available else "CLI is not detected"
+    if suppressed:
+        reason = (
+            "MCP server preferred for "
+            f"'{matched_prefer_mcp_phrase}' despite matching CLI '{name}'."
+        )
+
+    hint = CLIHint(
+        available=is_available,
+        path=path,
+        reason=reason,
+        **cli_hint_fields(cli),
+    )
+    return CLIHintMatch(
+        hint=hint,
+        score=score,
+        suppressed_by_prefer_mcp=suppressed,
+        matched_prefer_mcp_phrase=matched_prefer_mcp_phrase,
+    )
 
 
 def rank_cli_hints(
@@ -122,60 +190,28 @@ def rank_cli_hints(
         is_available = name in available
         if not include_unavailable and not is_available:
             continue
-
-        score = 0.0
-        score = max(score, _text_match_score(query_norm, query_words, cli.name))
-        score = max(
-            score,
-            _text_match_score(query_norm, query_words, cli.description) * 0.7,
-        )
-        score = max(score, _keyword_match_score(query, cli.keywords))
-        for example in cli.examples:
-            score = max(
-                score, _text_match_score(query_norm, query_words, example) * 0.8
-            )
-
-        matched_prefer_mcp_phrase = None
-        for phrase in cli.prefer_mcp_for:
-            if _text_match_score(query_norm, query_words, phrase) >= 1.0:
-                matched_prefer_mcp_phrase = phrase
-                score = max(score, 1.0)
-                break
-
-        if score < min_score:
-            continue
-
-        suppressed = matched_prefer_mcp_phrase is not None
-        if suppressed and not include_suppressed:
-            continue
-
-        path = detected_infos[name].path if name in detected_infos else None
-        reason = "Available on PATH" if is_available else "CLI is not detected"
-        if suppressed:
-            reason = (
-                "MCP server preferred for "
-                f"'{matched_prefer_mcp_phrase}' despite matching CLI '{name}'."
+        # One CLI entry must never take down every query (Consiliency/pmcp#342);
+        # the loader already skips an overlay entry a consumer cannot use.
+        try:
+            match = _rank_one_cli(
+                query,
+                query_norm,
+                query_words,
+                name,
+                cli,
+                is_available=is_available,
+                detected_infos=detected_infos,
+                include_suppressed=include_suppressed,
+                min_score=min_score,
             )
-
-        hint = CLIHint(
-            name=cli.name,
-            description=cli.description,
-            available=is_available,
-            path=path,
-            check_command=cli.check_command,
-            help_command=cli.help_command,
-            examples=cli.examples,
-            prefer_mcp_for=cli.prefer_mcp_for,
-            reason=reason,
-        )
-        matches.append(
-            CLIHintMatch(
-                hint=hint,
-                score=score,
-                suppressed_by_prefer_mcp=suppressed,
-                matched_prefer_mcp_phrase=matched_prefer_mcp_phrase,
+        except Exception as exc:
+            logger.warning(
+                f"rank_cli_hints: skipping an unusable entry ({_cli_label(name)}): "
+                f"{type(exc).__name__}"
             )
-        )
+            continue
+        if match is not None:
+            matches.append(match)
 
     return sorted(matches, key=lambda match: (-match.score, match.hint.name))
 
@@ -225,7 +261,14 @@ def _keyword_match(
     # Check servers
     keyword_weights = _manifest_keyword_weights(manifest)
     for name, server in manifest.servers.items():
-        score = _keyword_match_score(query, server.keywords, keyword_weights)
+        try:
+            score = _keyword_match_score(query, server.keywords, keyword_weights)
+        except Exception as exc:  # Consiliency/pmcp#342: one entry, not all
+            logger.warning(
+                f"match_capability: skipping an unusable entry "
+                f"({_server_label(name)}): {type(exc).__name__}"
+            )
+            continue
         # Slight preference for CLIs, so server needs higher score
         adjusted_score = score * 0.9
         if adjusted_score > best_score:
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index ed00259..75475fd 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -40,7 +40,7 @@ from mcp.types import (
 from pmcp.client.manager import ClientManager
 from pmcp.config.guidance import GuidanceConfig, load_guidance_config
 from pmcp.config.loader import (
-    StartupSkipReason,
+    startup_skip_message,
     build_startup_observation_snapshot,
     is_legacy_manifest_auto_start_enabled,
     load_configs,
@@ -769,21 +769,7 @@ class GatewayServer:
             f"unknown_auto_start={counts['unknown_auto_start']}"
         )
         for skipped in resolution.skipped:
-            if skipped.reason == StartupSkipReason.MISSING_AUTH:
-                logger.info(
-                    f"Skipping startup entry '{skipped.name}' from {skipped.source}: "
-                    f"missing_auth; set {skipped.env_var} to enable eager startup"
-                )
-            elif skipped.reason == StartupSkipReason.UNKNOWN_AUTO_START:
-                logger.info(
-                    f"Skipping startup entry '{skipped.name}' from {skipped.source}: "
-                    "unknown_auto_start; add a matching mcpServers entry or remove it from autoStart"
-                )
-            else:
-                logger.info(
-                    f"Skipping startup entry '{skipped.name}' from {skipped.source}: "
-                    f"{skipped.reason.value}"
-                )
+            logger.info(startup_skip_message("startup", skipped))
 
         # Kill any orphan processes from a previous PMCP crash before registering servers
         self._kill_orphan_processes(resolution.lazy_configs + resolution.eager_configs)
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 09e9f34..5d57036 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -34,6 +34,7 @@ from pmcp.auth import (
 from pmcp.client.manager import ClientManager, _terminate_process_tree
 from pmcp.config.guidance import GuidanceConfig
 from pmcp.config.loader import (
+    startup_skip_message,
     registry_allow_private_from_config,
     StartupObservationSnapshot,
     StartupSkipReason,
@@ -79,7 +80,14 @@ from pmcp.manifest.installer import (
     get_job_manager,
     InstallError,
 )
-from pmcp.manifest.loader import load_manifest, npm_env_may_redirect
+from pmcp.manifest.loader import (
+    _cli_label,
+    _server_label,
+    load_manifest,
+    manifest_candidate_fields,
+    normalized_server_name,
+    npm_env_may_redirect,
+)
 from pmcp.manifest.package_identity import PackageIdentity, resolve_package_identity
 from pmcp.manifest.matcher import (
     _keyword_match_score,
@@ -783,6 +791,14 @@ def _summarize_arg_schema(
     return prop_type, None, ""
 
 
+def _log_unusable_manifest_entry(where: str, name: object, exc: BaseException) -> None:
+    """One manifest entry a consumer could not use, named safely, no values."""
+    logger.warning(
+        f"{where}: skipping an unusable manifest entry ({_server_label(name)}): "
+        f"{type(exc).__name__}"
+    )
+
+
 class GatewayTools:
     """Gateway tool handler implementations."""
 
@@ -1146,16 +1162,23 @@ class GatewayTools:
             if not self._policy_manager.is_server_allowed(name):
                 continue
 
-            score = _keyword_match_score(query, server.keywords, keyword_weights)
-
-            # Normalized name match (e.g. "bright data" -> "brightdata").
-            norm_name = name.lower().replace("-", "").replace("_", "")
-            for window_size in (3, 2, 1):
-                for i in range(len(query_words) - window_size + 1):
-                    window = "".join(query_words[i : i + window_size])
-                    if window == norm_name:
-                        score = max(score, 1.0)
-                        break
+            # One entry must never take down the search (Consiliency/pmcp#342).
+            # The loader already skips an overlay entry no consumer can use;
+            # this guard is the second line, for any manifest that bypassed it.
+            try:
+                score = _keyword_match_score(query, server.keywords, keyword_weights)
+
+                # Normalized name match (e.g. "bright data" -> "brightdata").
+                norm_name = normalized_server_name(name)
+                for window_size in (3, 2, 1):
+                    for i in range(len(query_words) - window_size + 1):
+                        window = "".join(query_words[i : i + window_size])
+                        if window == norm_name:
+                            score = max(score, 1.0)
+                            break
+            except Exception as exc:
+                _log_unusable_manifest_entry("catalog_search", name, exc)
+                continue
 
             if score >= 0.2:  # Same minimum threshold as the matcher
                 scored.append((score, name, server))
@@ -1169,16 +1192,17 @@ class GatewayTools:
         }
 
         candidates: list[CapabilityCandidate] = []
-        for score, name, server in scored[:limit]:
-            requires_api_key, env_var, env_instructions = self._get_server_env_metadata(
-                name, manifest, configured_servers
-            )
-            candidates.append(
-                CapabilityCandidate(
+        for score, name, server in scored:
+            if len(candidates) >= limit:
+                break
+            try:
+                requires_api_key, env_var, env_instructions = (
+                    self._get_server_env_metadata(name, manifest, configured_servers)
+                )
+                candidate = CapabilityCandidate(
                     name=name,
                     candidate_type="server",
                     relevance_score=min(1.0, score),
-                    reasoning=server.description,
                     requires_api_key=requires_api_key,
                     api_key_available=self._check_any_api_key_available(
                         self._auth_env_options(name, env_var)
@@ -1187,18 +1211,16 @@ class GatewayTools:
                     env_instructions=env_instructions,
                     is_running=name in running_servers,
                     source="manifest",
-                    transport=server.transport,
-                    url=server.url,
-                    package=server.package,
-                    server_card_url=server.server_card_url,
-                    declared_scopes=server.declared_scopes,
-                    declared_capabilities=server.declared_capabilities,
                     provisionable=True,
                     provision_tool="gateway.provision",
                     request_capability_tool="gateway.request_capability",
                     auth_tool="gateway.auth_connect",
+                    **manifest_candidate_fields(server),
                 )
-            )
+            except Exception as exc:
+                _log_unusable_manifest_entry("catalog_search", name, exc)
+                continue
+            candidates.append(candidate)
         return candidates
 
     async def catalog_search(self, input_data: dict[str, Any]) -> CatalogSearchOutput:
@@ -1945,21 +1967,7 @@ class GatewayTools:
                 f"unknown_auto_start={counts['unknown_auto_start']}"
             )
             for skipped in resolution.skipped:
-                if skipped.reason == StartupSkipReason.MISSING_AUTH:
-                    logger.info(
-                        f"Skipping refresh entry '{skipped.name}' from {skipped.source}: "
-                        f"missing_auth; set {skipped.env_var} to enable eager startup"
-                    )
-                elif skipped.reason == StartupSkipReason.UNKNOWN_AUTO_START:
-                    logger.info(
-                        f"Skipping refresh entry '{skipped.name}' from {skipped.source}: "
-                        "unknown_auto_start; add a matching mcpServers entry or remove it from autoStart"
-                    )
-                else:
-                    logger.info(
-                        f"Skipping refresh entry '{skipped.name}' from {skipped.source}: "
-                        f"{skipped.reason.value}"
-                    )
+                logger.info(startup_skip_message("refresh", skipped))
 
             pending_requests = self._client_manager.get_pending_requests()
             pending_seen = len(pending_requests)
@@ -3312,6 +3320,7 @@ class GatewayTools:
             cli_alternatives=dict(manifest.cli_alternatives),
             servers=merged_servers,
             discovery_queue_path=manifest.discovery_queue_path,
+            base_keyword_weights=manifest.base_keyword_weights,
         )
 
     def _get_server_env_metadata(
@@ -3589,7 +3598,9 @@ class GatewayTools:
         if cli_hint_matches:
             logger.debug(
                 "Matched CLI hints for future response plumbing: %s",
-                ", ".join(match.hint.name for match in cli_hint_matches[:3]),
+                ", ".join(
+                    _cli_label(match.hint.name) for match in cli_hint_matches[:3]
+                ),
             )
         cli_hint_match = next(
             (match for match in cli_hint_matches if match.hint.available),
@@ -3610,10 +3621,13 @@ class GatewayTools:
         # capability words like "browser" or "search".
         query_lower = parsed.query.lower()
         query_words = query_lower.split()
-        norm_to_server = {
-            n.lower().replace("-", "").replace("_", "").replace(" ", ""): n
-            for n in merged_manifest.servers
-        }
+        norm_to_server: dict[str, str] = {}
+        for n in merged_manifest.servers:
+            # One unusable name never takes down the name tier (Consiliency/pmcp#342).
+            try:
+                norm_to_server[normalized_server_name(n)] = n
+            except Exception as exc:
+                _log_unusable_manifest_entry("request_capability", n, exc)
         name_match: str | None = None
         for window_size in (3, 2, 1):
             for i in range(len(query_words) - window_size + 1):
@@ -3636,28 +3650,39 @@ class GatewayTools:
             cli_hint_match is not None and cli_hint_match.hint.name == name_match
         )
 
+        name_candidate: CapabilityCandidate | None = None
         if (
             name_match
             and self._policy_manager.is_server_allowed(name_match)
             and (explicit_mcp_intent or not name_match_collides_with_cli)
         ):
-            requires_api_key, env_var, env_instructions = self._get_server_env_metadata(
-                name_match, manifest, configured_servers
-            )
-            api_key_available = self._check_any_api_key_available(
-                self._auth_env_options(name_match, env_var)
-            )
-            candidate = CapabilityCandidate(
-                name=name_match,
-                candidate_type="server",
-                relevance_score=1.0,
-                reasoning=f"Explicit name match for '{name_match}' in query.",
-                requires_api_key=requires_api_key,
-                api_key_available=api_key_available,
-                env_var=env_var,
-                env_instructions=env_instructions,
-                is_running=name_match in running_servers,
-            )
+            # An unusable entry is no name match, not a failed request
+            # (Consiliency/pmcp#342).
+            try:
+                requires_api_key, env_var, env_instructions = (
+                    self._get_server_env_metadata(
+                        name_match, manifest, configured_servers
+                    )
+                )
+                api_key_available = self._check_any_api_key_available(
+                    self._auth_env_options(name_match, env_var)
+                )
+                name_candidate = CapabilityCandidate(
+                    name=name_match,
+                    candidate_type="server",
+                    relevance_score=1.0,
+                    reasoning=f"Explicit name match for '{name_match}' in query.",
+                    requires_api_key=requires_api_key,
+                    api_key_available=api_key_available,
+                    env_var=env_var,
+                    env_instructions=env_instructions,
+                    is_running=name_match in running_servers,
+                )
+            except Exception as exc:
+                _log_unusable_manifest_entry("request_capability", name_match, exc)
+
+        if name_match and name_candidate is not None:
+            candidate = name_candidate
             msg = f"Matched '{name_match}' by name."
             if requires_api_key:
                 if api_key_available:
@@ -3727,16 +3752,17 @@ class GatewayTools:
             for scfg in cat_servers:
                 if not self._policy_manager.is_server_allowed(scfg.name):
                     continue
-                requires_api_key, env_var, env_instructions = (
-                    self._get_server_env_metadata(
-                        scfg.name, manifest, configured_servers
+                # One category server never takes down the tier (Consiliency/pmcp#342).
+                try:
+                    requires_api_key, env_var, env_instructions = (
+                        self._get_server_env_metadata(
+                            scfg.name, manifest, configured_servers
+                        )
                     )
-                )
-                api_key_available = self._check_any_api_key_available(
-                    self._auth_env_options(scfg.name, env_var)
-                )
-                all_candidates.append(
-                    CapabilityCandidate(
+                    api_key_available = self._check_any_api_key_available(
+                        self._auth_env_options(scfg.name, env_var)
+                    )
+                    category_candidate = CapabilityCandidate(
                         name=scfg.name,
                         candidate_type="server",
                         relevance_score=1.0,
@@ -3747,7 +3773,10 @@ class GatewayTools:
                         env_instructions=env_instructions,
                         is_running=scfg.name in running_servers,
                     )
-                )
+                except Exception as exc:
+                    _log_unusable_manifest_entry("request_capability", scfg.name, exc)
+                    continue
+                all_candidates.append(category_candidate)
 
             if not all_candidates:
                 category_result = None
````

### File — `tests/test_catalog_overlay_discovery.py`

````python
"""An overlay never hides another server from discovery (Consiliency/pmcp#342).

Two rules, both measured on main ``c9206a9`` and on ``a622ec5`` (before the
manifest cache), through a real ``pmcp --transport http`` gateway:

* **R1, weights from the base.** Discovery weighs a keyword by how many servers
  declare it (``max(1/N, 0.5)``) and needs ``weight / 3 >= 0.2``. A keyword that
  one server declares weighs 1.0 and scores 0.333. Main counted over the MERGED
  manifest, so an approved project overlay whose server shared a keyword halved
  its weight for every other server: 0.5 / 3 = 0.167, below the threshold. A
  query on that keyword then returned no manifest candidate at all, not the
  user overlay's server, not the shipped one, not even the project's own. The
  weights now come from the base manifest alone, before any overlay.
* **R2, one entry never takes down the others.** No overlay entry was
  type-checked, and consumers iterate every entry. ``keywords: null`` made every
  ``catalog_search`` raise; a non-string ``transport`` made any query that
  ranked the entry raise; a bad CLI alternative made every query raise
  (``rank_cli_hints``); ``args``/``command``/``headers``/a metadata URL of the
  wrong type aborted startup and ``gateway.refresh`` for every server. Since
  revision 2, an overlay entry is built into every typed model its consumers
  build, and is skipped at parse time if any rejects it, with a warning that
  carries no value and no overlay name. A YAML ``null`` means "absent" for every
  field. Each consumer also guards each entry, as a second line.

Everything runs through the real loader, the real consent gate, and the real
``pmcp trust approve|revoke`` CLI entry path; only the client manager (no
servers running) is a stub.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pydantic
import pytest
import yaml

from pmcp.cli import async_main, parse_args
from pmcp.config.loader import resolve_startup_configs
from pmcp.manifest import loader
from pmcp.manifest.environment import probe_clis
from pmcp.manifest.loader import (
    _SHIPPED_MANIFEST_PATH,
    CLIAlternative,
    Manifest,
    ServerConfig,
    load_manifest,
)
from pmcp.manifest.matcher import (
    _keyword_match,
    _manifest_keyword_weights,
    rank_cli_hints,
)
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from tests.test_tools import MockClientManager as RefreshClientManager

USER_OVERLAY = """
servers:
  useronly:
    description: User overlay demo server
    keywords: [widget, demo]
    command: npx
    args: ["-y", "useronly-mcp@1.0.0"]
"""

# Shares `demo` with the user overlay and `screenshot` with shipped playwright,
# where each is otherwise declared by exactly one server.
PROJECT_OVERLAY = """
servers:
  projonly:
    description: Project overlay demo server
    keywords: [gadget, demo, screenshot]
    command: npx
    args: ["-y", "projonly-mcp@1.0.0"]
"""


@pytest.fixture(autouse=True)
def _no_env_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _run_cli(*argv: str) -> None:
    """One `pmcp` invocation, dispatched exactly as `main()` would."""
    with patch("sys.argv", ["pmcp", *argv]):
        args = parse_args()
    asyncio.run(async_main(args))


def _gateway() -> GatewayTools:
    """GatewayTools with the real PolicyManager and no running servers."""
    client_manager = MagicMock()
    client_manager.get_all_tools.return_value = []
    client_manager.is_server_online.return_value = False
    client_manager.get_all_server_statuses.return_value = []
    return GatewayTools(client_manager=client_manager, policy_manager=PolicyManager())


def _candidates(tools: GatewayTools, query: str) -> list[str]:
    result = asyncio.run(
        tools.catalog_search({"query": query, "include_offline": True})
    )
    return sorted(c.name for c in result.manifest_candidates)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user overlay, and a project overlay the gateway's cwd discovers."""
    _write(Path.home() / ".pmcp" / "manifest.yaml", USER_OVERLAY)
    project_dir = tmp_path / "proj"
    overlay = _write(project_dir / ".pmcp" / "manifest.yaml", PROJECT_OVERLAY)
    monkeypatch.chdir(project_dir)
    return overlay


# --- R1: the end-to-end the issue asks for -----------------------------------


def test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable(
    project: Path,
) -> None:
    """approve -> shipped + user + project candidates; revoke -> shipped + user."""
    tools = _gateway()

    # Unapproved: the gate keeps the project overlay out entirely.
    assert _candidates(tools, "demo") == ["useronly"]
    assert _candidates(tools, "screenshot") == ["playwright"]
    assert _candidates(tools, "gadget") == []

    _run_cli("trust", "approve", str(project))
    manifest = load_manifest()
    assert {"useronly", "projonly", "playwright"} <= set(manifest.servers)
    # main: [] and [] -- the shared keyword sank every server below 0.2.
    assert _candidates(tools, "demo") == ["projonly", "useronly"]
    assert _candidates(tools, "screenshot") == ["playwright", "projonly"]
    assert _candidates(tools, "widget") == ["useronly"]
    assert _candidates(tools, "gadget") == ["projonly"]

    _run_cli("trust", "revoke", str(project))
    assert "projonly" not in load_manifest().servers
    assert _candidates(tools, "demo") == ["useronly"]
    assert _candidates(tools, "screenshot") == ["playwright"]
    assert _candidates(tools, "gadget") == []


def test_no_query_returns_no_manifest_candidates_whatever_the_consent(
    project: Path,
) -> None:
    """Without a query there is nothing to match (#78's design), approved or not."""
    tools = _gateway()

    def no_query() -> list[str]:
        result = asyncio.run(tools.catalog_search({"include_offline": True}))
        return [c.name for c in result.manifest_candidates]

    assert no_query() == []
    _run_cli("trust", "approve", str(project))
    assert no_query() == []


# --- R1: the class -- every source, every shipped keyword ----------------------


def _base_weights() -> dict[str, float]:
    # An explicit path applies no overlay, so its weights are the shipped ones.
    return _manifest_keyword_weights(load_manifest(_SHIPPED_MANIFEST_PATH))


@pytest.mark.parametrize("source", ["user", "project", "env"])
def test_no_overlay_source_changes_a_keyword_weight(
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Any,
) -> None:
    """Every overlay source, with a server declaring EVERY shipped keyword."""
    base = _base_weights()
    copycat = yaml.safe_dump(
        {
            "servers": {
                "copycat": {
                    "description": "declares every shipped keyword",
                    "keywords": sorted(base),
                    "command": "npx",
                }
            }
        }
    )
    if source == "user":
        _write(Path.home() / ".pmcp" / "manifest.yaml", copycat)
    elif source == "project":
        overlay = _write(tmp_path / "proj" / ".pmcp" / "manifest.yaml", copycat)
        approve_project_file(overlay)
        monkeypatch.chdir(tmp_path / "proj")
    else:
        env_path = _write(tmp_path / "env.yaml", copycat)
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(env_path))

    first = load_manifest()
    second = load_manifest()  # a cache hit: the weights survive the round trip
    assert "copycat" in first.servers
    assert _manifest_keyword_weights(first) == base
    assert _manifest_keyword_weights(second) == base


def test_a_copycat_overlay_hides_no_shipped_server_on_any_shipped_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """Derived from the shipped vocabulary, not from the issue's two examples.

    For every keyword the shipped manifest declares, the servers that query
    surfaces before an approved overlay declaring ALL of them must still
    surface after it. `limit` is lifted so top-N displacement, which is
    intended ranking, cannot mask a dropped candidate.
    """
    tools = _gateway()
    shipped_keywords = sorted(_base_weights())

    def surfaced(query: str) -> set[str]:
        return {
            c.name
            for c in tools._manifest_candidates_for_query(
                query,
                manifest=load_manifest(),
                configured_servers={},
                exclude_servers=set(),
                limit=1000,
            )
        }

    before = {q: surfaced(q) for q in shipped_keywords}
    overlay = _write(
        tmp_path / "proj" / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "copycat": {
                        "description": "declares every shipped keyword",
                        "keywords": shipped_keywords,
                        "command": "npx",
                    }
                }
            }
        ),
    )
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")
    assert "copycat" in load_manifest().servers

    lost = {q: before[q] - surfaced(q) for q in shipped_keywords}
    assert {q: s for q, s in lost.items() if s} == {}
    # And the queries that surfaced anything are not a vacuous handful.
    assert sum(1 for q in shipped_keywords if before[q]) > 100


def test_match_capability_keeps_its_match_when_an_overlay_shares_the_keyword(
    project: Path,
) -> None:
    """The exported single-best matcher uses the same weights."""
    assert _keyword_match("demo", load_manifest(), set()).entry_name == "useronly"
    assert _keyword_match("screenshot", load_manifest(), set()).entry_name == (
        "playwright"
    )
    _run_cli("trust", "approve", str(project))
    demo = _keyword_match("demo", load_manifest(), set())
    shot = _keyword_match("screenshot", load_manifest(), set())
    assert demo.matched and demo.entry_name in {"useronly", "projonly"}
    assert shot.matched and shot.entry_name in {"playwright", "projonly"}


def test_a_hand_built_manifest_is_still_weighted_by_its_own_servers() -> None:
    """No base: corpus weights, so generic keywords still sink (unchanged)."""
    servers = {
        f"s{i}": ServerConfig(
            name=f"s{i}",
            description="",
            keywords=["api"],
            install={},
            command="npx",
            args=[],
        )
        for i in range(4)
    }
    manifest = Manifest("1.0", {}, servers, ".mcp-gateway/discovery_queue.json")
    assert getattr(manifest, "base_keyword_weights", None) is None
    assert _manifest_keyword_weights(manifest) == {"api": 0.5}
    assert _keyword_match("api", manifest, set()).matched is False


def test_the_config_server_view_keeps_the_base_weights(project: Path) -> None:
    """request_capability's merged view must not fall back to corpus weights."""
    manifest = load_manifest()
    view = _gateway()._build_manifest_with_config_servers(manifest, {})
    assert view.base_keyword_weights is not None
    assert view.base_keyword_weights == manifest.base_keyword_weights


def test_an_explicit_path_load_weights_that_file_alone(tmp_path: Path) -> None:
    """An explicit path applies no overlay, and its weights are its own."""
    _write(Path.home() / ".pmcp" / "manifest.yaml", USER_OVERLAY)
    path = _write(
        tmp_path / "only.yaml",
        "servers:\n  a: {keywords: [x, y], command: npx}\n"
        "  b: {keywords: [x], command: npx}\n",
    )
    manifest = load_manifest(path)
    assert set(manifest.servers) == {"a", "b"}
    assert _manifest_keyword_weights(manifest) == {"x": 0.5, "y": 1.0}


# --- R2: an entry a consumer would reject is skipped at parse time ---------------
#
# Revision 2 (board round 1 on Consiliency/pmcp#343): the rule is no longer a
# list of fields. An overlay entry is built into every typed model its
# consumers build -- ServerConfig / CLIAlternative's own annotations, the
# CapabilityCandidate catalog_search builds, the Local/RemoteMcpServerConfig
# startup and refresh build, the CLIHint rank_cli_hints builds -- and is
# skipped if any of them rejects it. These tests walk EVERY field of the two
# entry models with every bad shape and drive every consumer.

SECRET_NAME = "tok-NAME-sentinel-5f1c9a"
SECRET = "sk-live-VALUE-sentinel-0123456789"
HUGE = "H" * 100_000 + SECRET
# null, a wrong scalar (int, bool, str), a list, a dict, huge values. Each
# container carries the value sentinel so a log line that echoes it is caught.
SHAPES: list[tuple[str, Any]] = [
    ("null", None),
    ("int", 5),
    ("bool", True),
    ("str", "zz-" + SECRET),
    ("list", [SECRET, 1]),
    ("dict", {SECRET: [1]}),
    ("huge-str", HUGE),
    ("huge-int", 10**40),
]
SERVER_FIELDS = [f.name for f in dataclasses.fields(ServerConfig) if f.name != "name"]
CLI_FIELDS = [f.name for f in dataclasses.fields(CLIAlternative) if f.name != "name"]
GOOD_SERVER = {"keywords": ["zzgood"], "command": "npx", "args": ["-y", "good@1.0.0"]}


def _refresh_tools(monkeypatch: pytest.MonkeyPatch) -> GatewayTools:
    """A GatewayTools whose refresh runs on the REAL load_manifest."""
    tools = GatewayTools(
        client_manager=RefreshClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )
    monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_enabled_auto_start", lambda **_: set()
    )
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_disabled_auto_start", lambda **_: set()
    )
    monkeypatch.setattr(tools, "_load_provisioned_registry", lambda: {})
    return tools


def _drive_every_consumer(tools: GatewayTools) -> None:
    """Every consumer of the merged manifest; none may raise."""
    manifest = load_manifest()
    assert "good" in manifest.servers
    assert "goodcli" in manifest.cli_alternatives
    assert "git" in manifest.cli_alternatives

    for include_offline in (True, False):
        for query in ("zzbad screenshot", "zzgood", "git commits", "playwright"):
            asyncio.run(
                tools.catalog_search(
                    {"query": query, "include_offline": include_offline}
                )
            )
    found = asyncio.run(
        tools.catalog_search({"query": "zzbad screenshot", "include_offline": True})
    )
    assert "playwright" in {c.name for c in found.manifest_candidates}
    good = asyncio.run(
        tools.catalog_search({"query": "zzgood", "include_offline": True})
    )
    assert [c.name for c in good.manifest_candidates] == ["good"]

    for query in ("screenshot", "zzgood", "git commits", "database sql"):
        asyncio.run(tools.request_capability({"query": query}))

    resolution = resolve_startup_configs([], manifest_servers=manifest.servers)
    names = {c.name for c in resolution.lazy_configs + resolution.eager_configs}
    assert {"good", "playwright"} <= names

    refreshed = asyncio.run(tools.refresh({"reason": "test"}))
    assert refreshed.ok is True


def _assert_nothing_leaked(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        message = record.getMessage()
        assert SECRET not in message, message[:200]
        assert SECRET_NAME not in message, message[:200]
        # The parse-time check must catch every entry a consumer would fail
        # on, so no consumer guard (the second line) ever fires for an overlay
        # entry. A guard firing here means the check missed a consumer.
        assert "skipping an unusable" not in message, message[:200]
        assert "skipping unusable keywords" not in message, message[:200]


@pytest.mark.parametrize("field_name", SERVER_FIELDS)
def test_every_server_field_with_every_bad_shape_is_contained(
    field_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tools = _refresh_tools(monkeypatch)
    for _, value in SHAPES:
        bad = {
            "keywords": ["zzbad", "screenshot"],
            "command": "npx",
            "url": "https://example.invalid/mcp",
        }
        # Half the shapes exercise the remote startup view, half the local one.
        if field_name != "url" and value in (None, 5, True, 10**40):
            bad.pop("url")
        bad[field_name] = value
        overlay = {
            "servers": {SECRET_NAME: bad, "puppeteer": bad, "good": GOOD_SERVER},
            "cli_alternatives": {"goodcli": {"keywords": ["zzcli"]}},
        }
        _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
        with caplog.at_level(logging.DEBUG):
            _drive_every_consumer(tools)
        _assert_nothing_leaked(caplog)
        caplog.clear()


@pytest.mark.parametrize("field_name", CLI_FIELDS)
def test_every_cli_field_with_every_bad_shape_is_contained(
    field_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`git` is overridden too: it is on PATH, so rank_cli_hints scores it."""
    tools = _refresh_tools(monkeypatch)
    for _, value in SHAPES:
        bad: dict[str, Any] = {"keywords": ["git", "commits"]}
        bad[field_name] = value
        overlay = {
            "servers": {"good": GOOD_SERVER},
            "cli_alternatives": {SECRET_NAME: bad, "git": bad, "goodcli": {}},
        }
        _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
        with caplog.at_level(logging.DEBUG):
            _drive_every_consumer(tools)
        _assert_nothing_leaked(caplog)
        caplog.clear()


@pytest.mark.parametrize(
    ("key", "label"), [(5, "int"), (True, "bool"), (None, "null"), ("", "empty")]
)
def test_a_non_string_server_or_cli_key_is_contained(
    key: Any, label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _refresh_tools(monkeypatch)
    overlay = {
        "servers": {key: {"keywords": ["screenshot"]}, "good": GOOD_SERVER},
        "cli_alternatives": {key: {"keywords": ["git"]}, "goodcli": {}},
    }
    _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
    _drive_every_consumer(tools)
    assert all(isinstance(n, str) for n in load_manifest().servers)


def test_the_board_repros_are_skipped_with_a_value_free_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 1's concrete shapes, named, so a regression reads plainly."""
    rows = {
        "transport-yaml-on": {"keywords": ["screenshot"], "transport": True},
        "transport-int": {"keywords": ["screenshot"], "transport": 5},
        "transport-list": {"keywords": ["screenshot"], "transport": ["sse"]},
        "transport-unknown-with-url": {
            "keywords": ["screenshot"],
            "transport": "carrier-pigeon",
            "url": "https://example.invalid/mcp",
        },
        "args-str": {"keywords": ["screenshot"], "args": "x"},
        "command-int": {"keywords": ["screenshot"], "command": 5},
        "headers-int": {"url": "https://example.invalid/mcp", "headers": 5},
        "prm-url-int": {
            "url": "https://example.invalid/mcp",
            "protected_resource_metadata_url": 5,
        },
        "oidc-issuer-int": {"url": "https://example.invalid/mcp", "oidc_issuer_url": 5},
        # rev 3: caught by the consumers' scoring and credential code, not by
        # an annotation check
        "keywords-ints": {"keywords": [5]},
        "secret-key-int": {
            "keywords": ["zz"],
            "requires_api_key": True,
            "env_var": "X",
            "secret_key": 5,
        },
    }
    clis = {
        "cli-check-int": {"check_command": 5},
        "cli-check-empty": {"check_command": []},
        "cli-description-int": {"description": 5},
        "cli-keywords-ints": {"keywords": [5]},
        "cli-examples-ints": {"examples": [5]},
        "cli-prefer-str": {"prefer_mcp_for": "x"},
    }
    overlay = {
        "servers": {**rows, "good": GOOD_SERVER},
        "cli_alternatives": {**clis, "goodcli": {}},
    }
    _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()
    assert not set(rows) & set(manifest.servers)
    assert not set(clis) & set(manifest.cli_alternatives)
    assert "good" in manifest.servers and "goodcli" in manifest.cli_alternatives
    skips = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Skipping invalid")
    ]
    assert len(skips) == len(rows) + len(clis)
    # Overlay names are never shown; the reason names a field, not a value.
    assert all("(name not shown)" in m for m in skips)
    assert not any(name in m for m in skips for name in [*rows, *clis])
    assert any("'transport'" in m for m in skips)
    assert any("'check_command'" in m for m in skips)


def test_a_bad_cli_in_an_approved_project_overlay_is_skipped(project: Path) -> None:
    """Round 1's F1, behind the consent gate, via the real CLI."""
    project.write_text(
        yaml.safe_dump(
            {
                "servers": {"projonly": {"keywords": ["gadget"]}},
                "cli_alternatives": {"zzcli": {"check_command": 5}},
            }
        )
    )
    _run_cli("trust", "approve", str(project))
    tools = _gateway()
    assert "zzcli" not in load_manifest().cli_alternatives
    assert _candidates(tools, "screenshot") == ["playwright"]
    assert _candidates(tools, "gadget") == ["projonly"]


# --- null means absent (round 1, F3) -------------------------------------------


def test_null_fields_take_their_defaults_and_keep_the_entry() -> None:
    """A blank `description:` must not drop an override back to shipped."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        """
servers:
  github:
    description:
    command: my-github-fork
    keywords:
    args:
    declared_scopes:
  mytool:
    description:
    command: npx
cli_alternatives:
  mycli:
    help_command:
    keywords:
""",
    )
    manifest = load_manifest()
    assert manifest.servers["github"].command == "my-github-fork"
    assert manifest.servers["github"].description == ""
    assert manifest.servers["github"].keywords == []
    assert manifest.servers["github"].args == []
    assert manifest.servers["mytool"].description == ""
    assert manifest.cli_alternatives["mycli"].help_command == ["mycli", "--help"]
    assert manifest.cli_alternatives["mycli"].keywords == []


def test_every_shipped_entry_passes_every_consumer_check() -> None:
    """The shipped parse is not checked at load (it has no per-entry skip);
    this test is what holds it to the same rule."""
    manifest = load_manifest(_SHIPPED_MANIFEST_PATH)
    assert len(manifest.servers) > 100
    for server in manifest.servers.values():
        loader._check_server_for_consumers(server)
    for cli in manifest.cli_alternatives.values():
        loader._check_cli_for_consumers(cli)


# --- the second line: each consumer guards each entry ------------------------------
#
# Hand-built manifests bypass the parse-time check, so these prove each guard on
# its own. Every guard has its own mutant.


def _bad_server(**overrides: Any) -> ServerConfig:
    base: dict[str, Any] = {
        "name": "bad",
        "description": "bad",
        "keywords": ["screenshot"],
        "install": {},
        "command": "npx",
        "args": [],
    }
    base.update(overrides)
    return ServerConfig(**base)


def _with(
    servers: dict[str, ServerConfig], clis: dict[str, Any] | None = None
) -> Manifest:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    return Manifest(
        "1.0",
        {**shipped.cli_alternatives, **(clis or {})},
        {**shipped.servers, **servers},
        ".mcp-gateway/discovery_queue.json",
    )


def test_guard_keyword_weights_skip_an_unusable_server() -> None:
    weights = loader.keyword_weights(
        [_bad_server(keywords=None), _bad_server(keywords=["a"])]
    )
    assert weights == {"a": 1.0}


def test_guard_catalog_scoring_skips_an_unusable_server() -> None:
    manifest = _with({"bad": _bad_server(keywords=[5])})
    found = _gateway()._manifest_candidates_for_query(
        "screenshot", manifest=manifest, configured_servers={}, exclude_servers=set()
    )
    assert [c.name for c in found] == ["playwright"]


def test_guard_catalog_candidate_build_skips_an_unusable_server() -> None:
    # A keyword of its own, so it scores without diluting playwright's.
    manifest = _with({"bad": _bad_server(transport=5, keywords=["zzbad"])})
    found = _gateway()._manifest_candidates_for_query(
        "zzbad screenshot",
        manifest=manifest,
        configured_servers={},
        exclude_servers=set(),
    )
    assert [c.name for c in found] == ["playwright"]


def test_guard_rank_cli_hints_skips_an_unusable_cli() -> None:
    bad = CLIAlternative("git", None, ["git", "--version"], ["git", "--help"], "x")  # type: ignore[arg-type]
    good = CLIAlternative("zzcli", ["zzq"], ["zzcli"], ["zzcli"], "zz")
    manifest = Manifest("1.0", {"git": bad, "zzcli": good}, {}, "q.json")
    hints = rank_cli_hints("zzq git", manifest, available_clis={"git", "zzcli"})
    assert [h.hint.name for h in hints] == ["zzcli"]


def test_guard_probe_clis_treats_an_unusable_command_as_not_detected() -> None:
    detected = asyncio.run(
        probe_clis(
            {
                "empty": {"check_command": []},
                "git": {"check_command": ["git", "--version"]},
            }
        )
    )
    assert "empty" not in detected


def test_guard_startup_skips_an_unusable_server() -> None:
    good = _bad_server(name="good", keywords=["zzgood"])
    resolution = resolve_startup_configs(
        [], manifest_servers={"bad": _bad_server(args=None), "good": good}
    )
    assert [c.name for c in resolution.lazy_configs] == ["good"]


def test_guard_refresh_survives_an_unusable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _refresh_tools(monkeypatch)
    manifest = _with({"bad": _bad_server(command=5)})
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
    assert asyncio.run(tools.refresh({"reason": "test"})).ok is True


def test_guard_match_capability_skips_an_unusable_server() -> None:
    manifest = _with({"bad": _bad_server(keywords=[5])})
    assert _keyword_match("screenshot", manifest, set()).entry_name == "playwright"


# --- the parse-time check IS each consumer's model, not a copy of it ------------
#
# Today ServerConfig's annotations are as strict as every consumer model, so a
# consumer projection only adds protection when a consumer becomes stricter.
# These tests make a consumer stricter and prove the parse-time check follows.

MARK = "zz-consumer-rejects-this"


def _overlay_with_marked_entry() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "marked": {
                        "description": MARK,
                        "url": "https://example.invalid/mcp",
                        "keywords": ["zzmark"],
                    },
                    "good": GOOD_SERVER,
                },
                "cli_alternatives": {"markedcli": {"description": MARK}},
            }
        ),
    )


def test_a_stricter_candidate_model_is_enforced_at_parse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.types as types_module

    class Stricter(types_module.CapabilityCandidate):
        @pydantic.field_validator("reasoning")
        @classmethod
        def _no_mark(cls, value: str) -> str:
            if value == MARK:
                raise ValueError("rejected")
            return value

    monkeypatch.setattr(types_module, "CapabilityCandidate", Stricter)
    _overlay_with_marked_entry()
    servers = load_manifest().servers
    assert "marked" not in servers and "good" in servers


def test_a_stricter_startup_conversion_is_enforced_at_parse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.config.loader as config_loader

    real = config_loader._manifest_server_to_config

    def stricter(server: Any, env_lookup: Any) -> Any:
        if server.description == MARK:
            raise ValueError("rejected")
        return real(server, env_lookup)

    monkeypatch.setattr(config_loader, "_manifest_server_to_config", stricter)
    _overlay_with_marked_entry()
    servers = load_manifest().servers
    assert "marked" not in servers and "good" in servers


def test_a_stricter_cli_hint_model_is_enforced_at_parse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.types as types_module

    class Stricter(types_module.CLIHint):
        @pydantic.field_validator("description")
        @classmethod
        def _no_mark(cls, value: str) -> str:
            if value == MARK:
                raise ValueError("rejected")
            return value

    monkeypatch.setattr(types_module, "CLIHint", Stricter)
    monkeypatch.setattr("pmcp.manifest.matcher.CLIHint", Stricter)
    _overlay_with_marked_entry()
    assert "markedcli" not in load_manifest().cli_alternatives


def test_the_marked_entries_load_when_no_consumer_is_stricter() -> None:
    """The control for the three tests above: they are not vacuous."""
    _overlay_with_marked_entry()
    manifest = load_manifest()
    assert "marked" in manifest.servers
    assert "markedcli" in manifest.cli_alternatives


def _request(monkeypatch: pytest.MonkeyPatch, manifest: Manifest, query: str) -> Any:
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
    return asyncio.run(_gateway().request_capability({"query": query}))


def test_guard_request_capability_name_tier_skips_an_unusable_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({5: _bad_server(name=5)})  # type: ignore[dict-item]
    result = _request(monkeypatch, manifest, "github pull request")
    assert [c.name for c in result.candidates or []] == ["github"]


def test_guard_request_capability_name_match_skips_an_unusable_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({"puppeteer": _bad_server(name="puppeteer", env_var=5)})
    result = _request(monkeypatch, manifest, "puppeteer")
    assert result.status != "candidates" or all(
        c.name != "puppeteer" for c in result.candidates or []
    )


def test_guard_request_capability_category_keywords_skip_an_unusable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({"puppeteer": _bad_server(name="puppeteer", keywords=None)})
    result = _request(monkeypatch, manifest, "browser automation screenshot")
    assert "playwright" in [c.name for c in result.candidates or []]


def test_guard_request_capability_category_candidate_skips_an_unusable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({"puppeteer": _bad_server(name="puppeteer", description=5)})
    result = _request(monkeypatch, manifest, "browser automation screenshot")
    names = [c.name for c in result.candidates or []]
    assert "playwright" in names and "puppeteer" not in names


# --- revision 3: no stricter than the consumers (round 2, claude N1) ------------


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("transport", "stdio"),
        ("transport", "Local"),
        ("transport", "carrier-pigeon"),
        ("status", 5),
        ("source", ["x"]),
        ("replacement", {"x": 1}),
        ("discovery_diagnostics", [1]),
        ("auto_start", "maybe"),
        ("supports_url_elicitation", "maybe"),
        ("requires_api_key", "maybe"),
        ("keywords", "zzodd"),
    ],
)
def test_values_main_accepted_and_used_still_load(field_name: str, value: Any) -> None:
    """No consumer fails on these with no `url`, so main loaded and used them."""
    entry: dict[str, Any] = {"keywords": ["zzodd"], "command": "my-cmd"}
    entry[field_name] = value
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"odd": entry}}),
    )
    manifest = load_manifest()
    assert "odd" in manifest.servers
    resolution = resolve_startup_configs([], manifest_servers=manifest.servers)
    started = {c.name: c for c in resolution.lazy_configs + resolution.eager_configs}
    assert started["odd"].config.command == "my-cmd"  # starts as local, as on main


def test_a_stdio_override_of_a_shipped_server_keeps_the_override() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "github": {
                        "description": "my fork",
                        "transport": "stdio",
                        "command": "my-github-fork",
                        "keywords": ["github"],
                    }
                }
            }
        ),
    )
    assert load_manifest().servers["github"].command == "my-github-fork"
    assert "github" in _candidates(_gateway(), "github")


@pytest.mark.parametrize(
    ("entry", "loads"),
    [
        ({"keywords": "git"}, True),  # scored as characters, as on main
        ({"help_command": [1]}, False),  # CLIHint rejects it
        ({"check_command": "git"}, False),  # CLIHint rejects a str
        ({"check_command": []}, False),  # probe_clis reads slot 0
    ],
)
def test_a_cli_is_skipped_only_where_a_consumer_fails(
    entry: dict[str, Any], loads: bool
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": {"odd": entry, "ok": {}}}),
    )
    clis = load_manifest().cli_alternatives
    assert ("odd" in clis) is loads and "ok" in clis


# --- revision 3: main's keyword counting, exactly (round 2, codex) ---------------


def _mains_manifest_keyword_weights(servers: Any) -> dict[str, float]:
    """`matcher._manifest_keyword_weights` on main c9206a9, verbatim."""
    frequencies: dict[str, int] = {}
    for server in servers:
        for keyword in set(server.keywords):
            keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
            frequencies[keyword_norm] = frequencies.get(keyword_norm, 0) + 1

    return {
        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()
    }


def _generated_keyword_sets(seed: int) -> list[list[str]]:
    """Keyword lists full of normalisation aliases: -, _, space, case, repeats."""
    import random

    rng = random.Random(seed)
    stems = ["alpha beta", "web search", "db", "Sql", "x"]
    out = []
    for _ in range(rng.randint(2, 9)):
        words = []
        for _ in range(rng.randint(0, 6)):
            stem = rng.choice(stems)
            sep = rng.choice([" ", "-", "_"])
            word = stem.replace(" ", sep)
            word = rng.choice([word, word.upper(), word.title()])
            words.append(word)
            if rng.random() < 0.3:
                words.append(word)  # a verbatim repeat
        out.append(words)
    return out


@pytest.mark.parametrize("seed", range(40))
def test_weights_equal_mains_for_hand_built_and_explicit_path_manifests(
    seed: int, tmp_path: Path
) -> None:
    keyword_sets = _generated_keyword_sets(seed)
    servers = {
        f"s{i}": ServerConfig(
            name=f"s{i}",
            description="",
            keywords=kws,
            install={},
            command="npx",
            args=[],
        )
        for i, kws in enumerate(keyword_sets)
    }
    expected = _mains_manifest_keyword_weights(servers.values())

    hand_built = Manifest("1.0", {}, servers, "q.json")
    assert _manifest_keyword_weights(hand_built) == expected

    path = _write(
        tmp_path / f"m{seed}.yaml",
        yaml.safe_dump(
            {"servers": {n: {"keywords": s.keywords} for n, s in servers.items()}}
        ),
    )
    assert _manifest_keyword_weights(load_manifest(path)) == expected


def test_the_round_2_alias_repro_picks_mains_match() -> None:
    servers = {
        "a-other": ServerConfig("a-other", "", ["alpha"], {}, "npx", []),
        "z-dupe": ServerConfig(
            "z-dupe", "", ["alpha-beta", "alpha_beta"], {}, "npx", []
        ),
    }
    manifest = Manifest("1.0", {}, servers, "q.json")
    assert _manifest_keyword_weights(manifest)["alpha beta"] == 0.5
    assert _keyword_match("alpha beta", manifest, set()).entry_name == "a-other"


# --- revision 3: no overlay name or value in any log line on these paths -------
# (round 2, codex F1 + claude N2). Covered: loading, discovery (catalog_search,
# request_capability, match_capability), CLI probing, startup and refresh
# resolution (skip lines), and lazy registration.

VALUE_SENTINEL = "sk-test-SENTINEL-value-77ab"
NAME_SENTINEL = "tok-SENTINEL-name-77ab"


def test_a_self_relaxing_credential_is_refused_without_its_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "relaxer": {
                        "command": "npx",
                        "requires_api_key": True,
                        "env_var": VALUE_SENTINEL,
                        "api_key_optional_when": [VALUE_SENTINEL],
                    }
                }
            }
        ),
    )
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
    assert manifest.servers["relaxer"].api_key_optional_when == []
    assert any("cannot relax itself" in r.getMessage() for r in caplog.records)
    assert not any(VALUE_SENTINEL in r.getMessage() for r in caplog.records)


def test_probe_clis_never_logs_an_overlay_cli_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        detected = asyncio.run(
            probe_clis({NAME_SENTINEL: {"check_command": ["git", "--version"]}})
        )
    assert NAME_SENTINEL in detected  # it was probed and found
    assert any("Detected" in r.getMessage() for r in caplog.records)
    assert not any(NAME_SENTINEL in r.getMessage() for r in caplog.records)


def test_no_overlay_name_or_value_reaches_any_log_on_these_paths(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A sweep: valid, KEPT overlay entries named and valued by sentinels."""
    from pmcp.client.manager import ClientManager
    from pmcp.config.loader import startup_skip_message

    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    NAME_SENTINEL: {
                        "description": "sentinel server",
                        "keywords": ["screenshot", "zzsent"],
                        "command": "npx",
                        "requires_api_key": True,
                        "env_var": VALUE_SENTINEL,
                        "api_key_optional_when": [VALUE_SENTINEL],
                    },
                    "good": GOOD_SERVER,
                },
                "cli_alternatives": {
                    NAME_SENTINEL + "-cli": {
                        "keywords": ["zzsent"],
                        "check_command": ["git", "--version"],
                    }
                },
            }
        ),
    )
    tools = _refresh_tools(monkeypatch)
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_enabled_auto_start", lambda **_: {NAME_SENTINEL}
    )
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
        assert NAME_SENTINEL in manifest.servers
        for query in ("zzsent", "screenshot", "zzsent git"):
            asyncio.run(tools.catalog_search({"query": query, "include_offline": True}))
            asyncio.run(tools.request_capability({"query": query}))
        _keyword_match("zzsent", manifest, set())
        resolution = resolve_startup_configs(
            [],
            manifest_servers=manifest.servers,
            enabled_auto_start={NAME_SENTINEL},
            is_auth_available=lambda _key: False,
        )
        assert any(s.name == NAME_SENTINEL for s in resolution.skipped)
        for skipped in resolution.skipped:
            logging.getLogger("pmcp.server").info(
                startup_skip_message("startup", skipped)
            )
        asyncio.run(tools.refresh({"reason": "sweep"}))
        lazy = resolve_startup_configs([], manifest_servers=manifest.servers)
        ClientManager().register_lazy_configs(lazy.lazy_configs)
    messages = [r.getMessage() for r in caplog.records]
    assert any("Registered lazy server" in m for m in messages)
    assert any("Skipping startup entry" in m for m in messages)
    leaks = [m[:160] for m in messages if NAME_SENTINEL in m or VALUE_SENTINEL in m]
    assert leaks == []
````

### Patch — test migration (`tests/test_version_pin.py`, `tests/test_manifest_overlay.py`)

````diff
diff --git a/tests/test_manifest_overlay.py b/tests/test_manifest_overlay.py
index c321855..2f15417 100644
--- a/tests/test_manifest_overlay.py
+++ b/tests/test_manifest_overlay.py
@@ -396,10 +396,13 @@ server_env:
         manifest = load_manifest()
 
     assert "no-such-server" not in manifest.servers
+    # The warning is owed, but an overlay's own key is never shown: it may be
+    # anything an operator pasted (Consiliency/pmcp#342, D9).
     assert any(
-        "no-such-server" in r.message and "server_env" in r.message
+        "server_env" in r.message and "(name not shown)" in r.message
         for r in caplog.records
     )
+    assert not any("no-such-server" in r.message for r in caplog.records)
 
 
 @pytest.mark.parametrize(
diff --git a/tests/test_version_pin.py b/tests/test_version_pin.py
index 4f4eead..3ba6157 100644
--- a/tests/test_version_pin.py
+++ b/tests/test_version_pin.py
@@ -380,7 +380,14 @@ servers:
 def test_a_pin_on_a_malformed_entry_costs_only_that_entry(
     shape: str, caplog: pytest.LogCaptureFixture
 ) -> None:
-    """HEAD loads every entry of this overlay; the pin must not change that."""
+    """A non-string argv costs only its own entry.
+
+    A non-string `args` or `command` loaded on main and then aborted startup and
+    `gateway.refresh` for every server (`LocalMcpServerConfig` rejects it); it
+    is now skipped at parse time (Consiliency/pmcp#342). A non-string install
+    argv breaks only that server's own provisioning, so it still loads, as on
+    main. Either way the pin must cost nothing else.
+    """
     shipped_count = len(load_manifest().servers)
     _user_overlay(
         f"""
@@ -396,8 +403,12 @@ servers:
     with caplog.at_level(logging.WARNING):
         manifest = load_manifest()  # must not raise
 
-    assert len(manifest.servers) == shipped_count + 1
-    assert manifest.servers["malformed"].version is None
+    startup_would_fail = "install:" not in shape
+    assert len(manifest.servers) == shipped_count + (0 if startup_would_fail else 1)
+    if startup_would_fail:
+        assert "malformed" not in manifest.servers
+    else:
+        assert manifest.servers["malformed"].version is None
     assert manifest.servers["firecrawl"].args == ["-y", "firecrawl-mcp"]
     assert any("an overlay server (name not shown)" in m for m in _warnings(caplog))
     assert not any("malformed" in m for m in _warnings(caplog))
@@ -705,15 +716,18 @@ async def test_no_refused_pin_or_credential_reaches_a_log_or_update_output(
         "c-slot",
         "c-install",
         "c-uvx",
-        "c-bad",
         remote,
     )
     assert all(manifest.servers[n].version is None for n in refused)
+    # A non-string argv is skipped whole at parse time (Consiliency/pmcp#342).
+    assert "c-bad" not in manifest.servers
     assert manifest.servers["c-ok"].version == "1.0.0"
+    # c-bad is skipped whole now (one "Skipping invalid server entry" line)
+    # rather than loaded with a refused pin (Consiliency/pmcp#342).
     assert (
-        len([m for m in loader_lines if "Ignoring" in m or "does not define" in m])
-        >= 10
+        len([m for m in loader_lines if "Ignoring" in m or "does not define" in m]) >= 9
     )
+    assert any("Skipping invalid server entry" in m for m in loader_lines)
     assert value not in caplog.text
     assert all(
         value not in r.model_dump_json() and value not in line for r, line in outputs
````

## Appendix: reproduction and differential scripts

All of them run from `$WORKTREE_ROOT/pmcp-342-repro` on dev0. HOME and the project live on
the workspace volume, not under `/tmp`. They were run with `PYTHONPATH=<tree>/src` to pick
`c9206a9`, `a622ec5` or the spike.

### `call.py` (catalog_search over a real streamable-HTTP gateway)

````python
import asyncio, json, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def main(port):
    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for q in sys.argv[2:]:
                args = {"query": q, "include_offline": True} if q != "-" else {"include_offline": True}
                res = await s.call_tool("gateway.catalog_search", args)
                d = json.loads(res.content[0].text)
                print(f"  {q!r:14} manifest_candidates={[c['name'] for c in d['manifest_candidates']]}")
asyncio.run(main(int(sys.argv[1])))
````

### `consumers.py` (consumer differential, approved vs revoked)

````python
"""Differential: every manifest consumer's view, project overlay approved vs revoked.

Run from the project dir with HOME set to the repro home. Uses the real loader,
the real consent gate (pmcp trust approve/revoke via the CLI) and GatewayTools.
"""

import asyncio
import json
import logging
import subprocess
import sys
from unittest.mock import MagicMock

logging.disable(logging.CRITICAL)

from pmcp.manifest import loader  # noqa: E402
from pmcp.manifest.matcher import _keyword_match  # noqa: E402
from pmcp.policy.policy import PolicyManager  # noqa: E402
from pmcp.tools.handlers import GatewayTools  # noqa: E402

PROJ = ".pmcp/manifest.yaml"


def trust(verb: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pmcp", "trust", verb, PROJ],
        check=True,
        capture_output=True,
    )


def tools() -> GatewayTools:
    cm = MagicMock()
    cm.get_all_tools.return_value = []
    cm.is_server_online.return_value = False
    cm.get_all_server_statuses.return_value = []
    cm.is_lazy_server.return_value = False
    return GatewayTools(client_manager=cm, policy_manager=PolicyManager())


QUERIES = ["demo", "screenshot", "widget", "gadget", "database sql", "github pull request",
           "web scraping", "send email", "payments"]


async def snapshot() -> dict:
    m = loader.load_manifest()
    t = tools()
    out: dict = {"servers": {n: repr(s) for n, s in m.servers.items()}}
    out["catalog"] = {}
    out["request_capability"] = {}
    out["keyword_match"] = {}
    for q in QUERIES:
        r = await t.catalog_search({"query": q, "include_offline": True})
        out["catalog"][q] = [c.name for c in r.manifest_candidates]
        rc = await t.request_capability({"query": q})
        out["request_capability"][q] = (rc.status, rc.category_name, [c.name for c in (rc.candidates or [])])
        km = _keyword_match(q, m, set())
        out["keyword_match"][q] = km.entry_name if km.matched else None
    r = await t.catalog_search({"include_offline": True})
    out["catalog"]["<no query>"] = [c.name for c in r.manifest_candidates]
    return out


def main() -> None:
    trust("approve")
    approved = asyncio.run(snapshot())
    trust("revoke")
    revoked = asyncio.run(snapshot())
    a, r = approved["servers"], revoked["servers"]
    print("servers approved", len(a), "revoked", len(r))
    print("only approved:", sorted(set(a) - set(r)), "only revoked:", sorted(set(r) - set(a)))
    changed = [n for n in set(a) & set(r) if a[n] != r[n]]
    print("get_server differs for a shared name:", sorted(changed))
    for section in ("catalog", "request_capability", "keyword_match"):
        print(f"--- {section}")
        for q in approved[section]:
            av, rv = approved[section][q], revoked[section][q]
            flag = "" if av == rv else "   <-- differs"
            print(f"  {q!r:22} approved={av} revoked={rv}{flag}")
    json.dump({"approved": approved, "revoked": revoked}, open("consumers.json", "w"))


main()
````

### `copycat.py` (the class, over all shipped keywords)

````python
import os, logging, yaml
logging.disable(logging.CRITICAL)
from unittest.mock import MagicMock
from pmcp.manifest import loader
from pmcp.manifest.matcher import _manifest_keyword_weights
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp import trust_store
cm = MagicMock(); cm.get_all_tools.return_value=[]; cm.is_server_online.return_value=False; cm.get_all_server_statuses.return_value=[]
t = GatewayTools(client_manager=cm, policy_manager=PolicyManager())
kws = sorted(_manifest_keyword_weights(loader.load_manifest(loader._SHIPPED_MANIFEST_PATH)))
def surf(q): return {c.name for c in t._manifest_candidates_for_query(q, manifest=loader.load_manifest(), configured_servers={}, exclude_servers=set(), limit=1000)}
before = {q: surf(q) for q in kws}
p = os.path.abspath(".pmcp/manifest.yaml")
open(p, "w").write(yaml.safe_dump({"servers": {"copycat": {"keywords": kws, "command": "npx"}}}))
trust_store.record(__import__("pathlib").Path(p), open(p,"rb").read(), "project", trust_store.APPROVED)
after = {q: surf(q) for q in kws}
lost = {q: before[q]-after[q] for q in kws if before[q]-after[q]}
print("shipped keywords", len(kws), "queries surfacing >=1 before", sum(1 for q in kws if before[q]), "queries that lose a candidate", len(lost), "servers lost", len(set().union(*lost.values())) if lost else 0, "queries left with nothing but copycat", sum(1 for q in kws if before[q] and not (after[q]-{"copycat"})))
````

### `probe.py` (rev-1 R2 probe; superseded by the rev-2 field walk, kept as evidence)

````python
import os, asyncio, logging, yaml, sys
logging.disable(logging.CRITICAL)
from pathlib import Path
from unittest.mock import MagicMock
from pmcp.manifest import loader
from pmcp.tools.handlers import GatewayTools
from pmcp.policy.policy import PolicyManager
cm = MagicMock(); cm.get_all_tools.return_value=[]; cm.is_server_online.return_value=False; cm.get_all_server_statuses.return_value=[]
t = GatewayTools(client_manager=cm, policy_manager=PolicyManager())
p = Path("user.yaml").resolve()
U = {"keywords": ["zzq"], "command": "npx"}
cases = {
 "kw_null": {"x1": {"keywords": None, "command": "npx"}}, "kw_str": {"x1": {"keywords": "zzq, foo", "command": "npx"}},
 "kw_ints": {"x1": {"keywords": [1, 2], "command": "npx"}}, "kw_nested": {"x1": {"keywords": [["a"]], "command": "npx"}},
 "name_int": {123: U}, "name_bool": {True: U}, "entry_list": {"x1": ["a"]},
 "desc_null": {"x1": {**U, "description": None}}, "desc_int": {"x1": {**U, "description": 5}},
 "scopes_null": {"x1": {**U, "declared_scopes": None}}, "caps_str": {"x1": {**U, "declared_capabilities": "tools"}},
 "url_int": {"x1": {**U, "url": 5}}, "env_var_int": {"x1": {**U, "requires_api_key": True, "env_var": 5}},
 "secret_key_int": {"x1": {**U, "requires_api_key": True, "env_var": "X", "secret_key": 5}},
 "env_instr_int": {"x1": {**U, "requires_api_key": True, "env_var": "X", "env_instructions": 5}},
 "pkg_int": {"x1": {**U, "package": 5}}, "card_int": {"x1": {**U, "server_card_url": 5}},
 "transport_bad": {"x1": {**U, "transport": "carrier-pigeon"}}, "args_null": {"x1": {**U, "args": None}}, "rak_str": {"x1": {**U, "requires_api_key": "no"}},
}
for name, servers in cases.items():
    p.write_text(yaml.safe_dump({"servers": {**servers, "goodone": {"keywords": ["yyq"], "command": "npx"}}}))
    os.environ["PMCP_MANIFEST_PATH"] = str(p); getattr(loader, 'clear_manifest_cache', lambda: None)()
    m = loader.load_manifest()
    out = []
    for q in ["zzq", "yyq", "screenshot"]:
        try:
            r = asyncio.run(t.catalog_search({"query": q, "include_offline": True})); out.append(f"{q}={[c.name for c in r.manifest_candidates]}")
        except Exception as e:
            out.append(f"{q}=RAISE {type(e).__name__}")
    print(f"{name:15} servers={len(m.servers)} x1={'x1' in m.servers} " + " ".join(out))
````

### `mutants.py` (mutation driver, revision 3)

````python
"""Mutation driver for the Consiliency/pmcp#342 spike (revision 2).

Each mutant is an exact-text replacement that must match exactly once. The file
is restored from a saved copy (never git) and the restore is cmp-checked.

A mutant counts as RED only when at least one test FAILED and no test ERRORED
(round 1, F6: a setup error is not evidence). The basetemp parent is created
first, which is the setup error round 1 tripped over.

Usage: python mutants.py <worktree> [ids...]
"""

import filecmp
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
ONLY = set(sys.argv[2:])
LOADER = "src/pmcp/manifest/loader.py"
MATCHER = "src/pmcp/manifest/matcher.py"
HANDLERS = "src/pmcp/tools/handlers.py"
ENVIRON = "src/pmcp/manifest/environment.py"
CONFIG = "src/pmcp/config/loader.py"
MANAGER = "src/pmcp/client/manager.py"
TESTS = ["tests/test_catalog_overlay_discovery.py"]
GENERIC = "tests/test_manifest.py::test_keyword_match_generic_api_alone_stays_below_threshold"
GUARD_EXC = "except ZeroDivisionError as exc:"

MUTANTS = [
    # --- R1 ---
    ("M1", "R1: scoring ignores the base weights (counts the merged manifest)", MATCHER,
     "    if manifest.base_keyword_weights is not None:\n", "    if False:\n", TESTS),
    ("M2", "R1: base weights computed after the overlays are merged", LOADER,
     "        base_keyword_weights=base_weights,\n",
     "        base_keyword_weights=keyword_weights(servers.values()),\n", TESTS),
    ("M3", "R1: request_capability's config-server view drops the base weights", HANDLERS,
     "            base_keyword_weights=manifest.base_keyword_weights,\n", "", TESTS),
    ("M4", "instance fix instead of the class: raise the IDF floor so a shared keyword passes", LOADER,
     "        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()\n",
     "        keyword: max(1.0 / frequency, 0.7) for keyword, frequency in frequencies.items()\n",
     TESTS + [GENERIC]),
    # --- R2, parse time ---
    ("M5", "R2: overlay servers not checked against their consumers", LOADER,
     "                _check_server_for_consumers(server)\n", "", TESTS),
    ("M7", "R2: server check skips the CapabilityCandidate catalog_search builds", LOADER,
     "    CapabilityCandidate(\n        name=server.name,\n",
     "    dict(\n        name=server.name,\n", TESTS),
    ("M8", "R2: server check skips the startup/refresh conversion", LOADER,
     "    _manifest_server_to_config(server, lambda _key: None)\n", "", TESTS),
    ("M9", "R2: overlay CLI alternatives not checked against their consumers", LOADER,
     "                _check_cli_for_consumers(cli)\n", "", TESTS),
    ("M12", "R2: CLI check accepts an empty check_command (probe needs slot 0)", LOADER,
     "    if not cli.check_command:\n", "    if False:\n", TESTS),
    ("M13", "R2: a YAML null is a value, not absent", LOADER,
     "    return {key: value for key, value in data.items() if value is not None}\n",
     "    return dict(data)\n", TESTS),
    ("M14", "R2: the skip reason quotes pydantic's text (values reach the log)", LOADER,
     "    if isinstance(exc, ValidationError):\n        parts = []\n",
     "    if isinstance(exc, ValidationError):\n        return str(exc)\n        parts = []\n", TESTS),
    ("M15", "R2: the server skip warning names the overlay entry", LOADER,
     'f"Skipping invalid server entry ({_server_label(name)}) in "',
     'f"Skipping invalid server entry ({name!r}) in "', TESTS),
    ("M16", "R2: the CLI skip warning names the overlay entry", LOADER,
     'f"Skipping invalid cli_alternative ({_cli_label(name)}) in "',
     'f"Skipping invalid cli_alternative ({name!r}) in "', TESTS),
    # --- the second line: one guard each ---
    ("G1", "guard: keyword_weights lets one server's keywords fail the table", LOADER,
     "                for keyword in set(server.keywords)\n            ]\n        except Exception:\n",
     "                for keyword in set(server.keywords)\n            ]\n        except ZeroDivisionError:\n",
     TESTS),
    ("G2", "guard: catalog_search scoring lets one server raise", HANDLERS,
     "                            score = max(score, 1.0)\n                            break\n            except Exception as exc:\n",
     "                            score = max(score, 1.0)\n                            break\n            " + GUARD_EXC + "\n",
     TESTS),
    ("G3", "guard: catalog_search candidate build lets one server raise", HANDLERS,
     "                    **manifest_candidate_fields(server),\n                )\n            except Exception as exc:\n",
     "                    **manifest_candidate_fields(server),\n                )\n            " + GUARD_EXC + "\n",
     TESTS),
    ("G4", "guard: rank_cli_hints lets one CLI raise", MATCHER,
     "                min_score=min_score,\n            )\n        except Exception as exc:\n",
     "                min_score=min_score,\n            )\n        " + GUARD_EXC + "\n", TESTS),
    ("G5", "guard: probe_clis lets one command raise", ENVIRON,
     "            result = await check_cli(name, check_cmd)\n        except Exception as exc:\n",
     "            result = await check_cli(name, check_cmd)\n        " + GUARD_EXC + "\n", TESTS),
    ("G6", "guard: startup/refresh let one manifest server abort resolution", CONFIG,
     "            add_config(config, eager=eager, source=source, manifest_server=server)\n        except Exception as exc:\n",
     "            add_config(config, eager=eager, source=source, manifest_server=server)\n        " + GUARD_EXC + "\n",
     TESTS),
    ("G7", "guard: match_capability lets one server raise", MATCHER,
     "        except Exception as exc:  # Consiliency/pmcp#342: one entry, not all\n",
     "        " + GUARD_EXC + "\n", TESTS),
    ("G8", "guard: request_capability name tier lets one name raise", HANDLERS,
     "            except Exception as exc:\n                _log_unusable_manifest_entry(\"request_capability\", n, exc)\n",
     "            " + GUARD_EXC + "\n                _log_unusable_manifest_entry(\"request_capability\", n, exc)\n", TESTS),
    ("G9", "guard: request_capability name match lets its entry raise", HANDLERS,
     "            except Exception as exc:\n                _log_unusable_manifest_entry(\"request_capability\", name_match, exc)\n",
     "            " + GUARD_EXC + "\n                _log_unusable_manifest_entry(\"request_capability\", name_match, exc)\n", TESTS),
    ("G10", "guard: request_capability category keywords let one server raise", LOADER,
     "    except Exception as exc:\n        logger.warning(\n            f\"request_capability: skipping unusable keywords \"\n",
     "    " + GUARD_EXC + "\n        logger.warning(\n            f\"request_capability: skipping unusable keywords \"\n", TESTS),
    ("G11", "guard: request_capability category candidates let one server raise", HANDLERS,
     "                except Exception as exc:\n                    _log_unusable_manifest_entry(\"request_capability\", scfg.name, exc)\n",
     "                " + GUARD_EXC + "\n                    _log_unusable_manifest_entry(\"request_capability\", scfg.name, exc)\n", TESTS),
    # --- revision 3 ---
    ("M6", "R2: check skips the name/keyword scoring every discovery path runs", LOADER,
     "    normalized_server_name(server.name)\n    _keyword_match_score(\"probe\", server.keywords)\n", "", TESTS),
    ("M10", "R2: check skips the credential lookups every candidate runs", LOADER,
     "    for key in credential_lookup_keys(server):\n        os.environ.get(key)\n", "", TESTS),
    ("M11", "R2: CLI check skips the _rank_one_cli scoring and CLIHint build", LOADER,
     "    _rank_one_cli(\n        \"probe\",\n", "    (lambda *a, **k: None)(\n        \"probe\",\n", TESTS),
    ("M17", "R1: keyword counting de-duplicates after normalising (not main's)", LOADER,
     "            norms = [\n                keyword.lower().replace(\"-\", \" \").replace(\"_\", \" \")\n                for keyword in set(server.keywords)\n            ]\n",
     "            norms = {\n                keyword.lower().replace(\"-\", \" \").replace(\"_\", \" \")\n                for keyword in set(server.keywords)\n            }\n", TESTS),
    ("L1", "logs: the self-relaxing credential warning echoes the variable", LOADER,
     "f\"variable in 'api_key_optional_when'; ignoring it \u2014 a \"",
     "f\"variable ('{item}') in 'api_key_optional_when'; ignoring it \u2014 a \"", TESTS),
    ("L2", "logs: probe_clis names each detected CLI", ENVIRON,
     "Detected CLI: {_label(name)}", "Detected CLI: {name}", TESTS),
    ("L3", "logs: probe_clis' summary names the detected CLIs", ENVIRON,
     "_label(n) for n in detected", "n for n in detected", TESTS),
    ("L4", "logs: lazy registration names an overlay server", MANAGER,
     "{_lazy_log_name(config)}", "{name}", TESTS),
    ("L5", "logs: startup/refresh skip lines name an overlay server", CONFIG,
     "    who = entry_log_name(skipped.name, manifest_derived=manifest_derived)\n",
     "    who = repr(skipped.name)\n", TESTS),
    ("L6", "logs: startup/refresh skip lines print an overlay env_var", CONFIG,
     "            shown = shipped_env_var(skipped.name, skipped.env_var)\n",
     "            shown = skipped.env_var\n", TESTS),

]

env = dict(os.environ)
for k in ("npm_config_cache", "npm_config_store_dir", "pnpm_config_store_dir"):
    env.pop(k, None)
bt = Path(os.environ.get("PMCP342_MUT_BASETEMP", "/mnt/workspace/users/viperjuice/pmcp-342-bt/mut"))
bt.mkdir(parents=True, exist_ok=True)


def count(pattern: str, text: str) -> int:
    m = re.search(rf"(\d+) {pattern}", text)
    return int(m.group(1)) if m else 0


for mid, rule, rel, old, new, tests in MUTANTS:
    if ONLY and mid not in ONLY:
        continue
    path = ROOT / rel
    saved = path.with_suffix(".py.saved342")
    shutil.copy2(path, saved)
    try:
        text = path.read_text()
        assert text.count(old) == 1, f"{mid}: anchor matched {text.count(old)} times"
        path.write_text(text.replace(old, new))
        proc = subprocess.run(
            [str(ROOT / ".venv/bin/python"), "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider",
             "--no-cov", "--cov-fail-under=0", "--tb=line", f"--basetemp={bt}/{mid}"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800,
        )
        out = proc.stdout.splitlines()
        summary = next((ln for ln in reversed(out) if " passed" in ln or " failed" in ln or " error" in ln), "?")
        failed, errors = count("failed", summary), count("errors?", summary)
        failing = [ln.split("::", 1)[1].split(" ")[0] for ln in out if ln.startswith("FAILED ")]
        first = next((ln.split("Error: ", 1)[-1][:90] for ln in out if "Error" in ln and ln.startswith("/")), "")
    finally:
        shutil.copy2(saved, path)
        same = filecmp.cmp(saved, path, shallow=False)
        saved.unlink()
    if failed > 0 and errors == 0:
        verdict = "RED"
    elif errors:
        verdict = "SETUP-ERROR"
    else:
        verdict = "SURVIVED"
    shown = ", ".join(failing[:4]) + (f", +{len(failing) - 4} more" if len(failing) > 4 else "")
    summary = re.sub(r" in [0-9.]+s.*", "", summary.strip("= "))
    print(f"| {mid} | {rule} | **{verdict}** {summary} (restored={same}) | {first} | {shown} |", flush=True)
````
