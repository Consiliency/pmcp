# Detailed plan: an overlay never hides another server from discovery — weights from the base manifest, and one entry never takes down the others (Consiliency/pmcp#342)

> Written on main `c9206a9` (dev0, a team host), worktree `pmcp-342`, branch
> `plan/342-catalog-overlay`. `origin/main` was re-fetched for revision 2 and is still
> `c9206a9`. Every number below was measured this session on that tree, on the commit
> before the manifest cache (`a622ec5`), on the rev-1 spike, or on the rev-2 spike applied
> to `c9206a9`. The spike was then removed, and this PR carries only this file.
>
> **Bounded-plan verdict: within threshold, larger than rev 1.**
> - Source: five files change (+462/−142: `manifest/loader.py` +237/−20, `manifest/matcher.py` +105/−62, `tools/handlers.py` +96/−56, `config/loader.py` +13/−2, `manifest/environment.py` +11/−2).
> - Tests: one module is new (`tests/test_catalog_overlay_discovery.py`, 81 tests).
>   Three existing tests are migrated, because the entries they load are now skipped or no
>   longer named in a log (D4, D9).
> - Docs: one CHANGELOG bullet and one README paragraph.
> - It is still one conceptual change: an overlay can add to discovery, and one entry can
>   never take the rest down.
>
> **Revision 2** (2026-10-03): board round 1 on `bd5c25f` (Consiliency/pmcp#343).
> - **Gemini** found nothing blocking.
> - **Claude, grok and codex** each found blocking gaps in R2. Rev 1's per-field type check
>   was a hand-written list, and it missed four things:
>   - **B1**: a non-string `transport`, which is copied into `CapabilityCandidate` (all
>     three seats).
>   - **B2**: CLI alternatives, which `rank_cli_hints` reads on every query (claude F1,
>     grok 2).
>   - **B3**: the startup and refresh view, which builds a pydantic config for every server
>     with no per-server guard (grok 3). `args: null`, an int `command`, `headers`, or a
>     metadata URL aborted it for every server.
>   - **B4**: the skip warning, which logged the overlay key verbatim (codex).
>
>   R2 is now one rule, derived from the consumers instead of from probes. An overlay entry,
>   server or CLI alternative, is built into every typed model its consumers build, and is
>   skipped at parse time if any of them rejects it. The skip warning carries no value and no
>   overlay name. Every per-entry consumer also guards each entry, as a second line (D7).
> - **Claude F3** is taken: a YAML `null` means "absent" for every field (D8).
> - **Claude F4** is taken: the docs say "within the result limit", and ties still break by
>   name (D10).
> - **Claude F6** is taken: the mutation driver counts a mutant RED only on a real test
>   failure with no errors.
> - **R1 is unchanged.** It held under every attack: 1250 shipped-only queries were
>   byte-identical, and on the 569-keyword copycat the candidate-losing queries went from 329
>   to 0.

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

- `tests/test_manifest.py`'s IDF tests use hand-built `Manifest`s and pass unchanged. That
  covers `test_keyword_match_generic_api_alone_stays_below_threshold`, the duplicate-keyword
  test and the real-manifest keyword table.
- `tests/test_manifest_cache.py` (47) passes. `Manifest` gains one defaulted field, which
  pickles with the cached result.
- **Three existing tests are migrated** (verbatim patch below):
  - `tests/test_version_pin.py::test_a_pin_on_a_malformed_entry_costs_only_that_entry`
    (3 cases). The entry's argv holds an int (`args: ["-y", 123]`, `command: 123`, or an
    install argv with an int). On main it loaded and then aborted startup and refresh for
    every server. It is now skipped. The test still proves its own point: the pin costs
    nothing else.
  - `…::test_no_refused_pin_or_credential_reaches_a_log_or_update_output`: `c-bad`
    (`args: ["-y", 5, …]`) is skipped whole instead of loaded with a refused pin. So its
    pin-refusal line becomes one skip line (9 refusal lines instead of 10).
  - `tests/test_manifest_overlay.py::test_server_env_unknown_server_warns_and_skips`: the
    warning is still owed, but it no longer shows the overlay's own key (D9).
- Touched-suite subset (18 files: manifest, overlay, cache, offline discovery, catalog,
  config loader, startup credential gates, server, tools, consent, trust CLI, version pin,
  env provenance, scoped advisor, phase-4 e2e, provisioning): **1955 passed, 1 skipped, 19 deselected, 0 failed** (166.99 s; before the 3-test migration it was 6 failed, the six cases migrated).

### Cost

- `load_manifest()` cache hit, median of 200 calls, 3 rounds alternating (rev 1, unchanged
  by rev 2, because the checks run only when an overlay is parsed): main 0.93–1.03 ms,
  spike 0.95–1.02 ms.
- The consumer check costs 6.3 ms for all 107 shipped servers and 12 CLIs, including the
  first `TypeAdapter` build. Shipped entries are not checked at load; they are checked by a
  test. An overlay pays about 0.05 ms per entry, once per cache miss.

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

### D3. Carry the base weights through `_build_manifest_with_config_servers`

`request_capability` builds a merged view with `.mcp.json`-only servers
(`handlers.py:3310`). It does not score keywords today. But a fresh `Manifest(...)` there
would silently drop back to corpus weights, letting configured servers dilute shipped ones
for any future scorer that takes the view. One line passes `base_keyword_weights` through
(M3).

### D4. R2: an entry any consumer would reject is skipped at parse time, checked by building the consumers' own models

Rev 1 kept a hand-written list of fields, built from what a probe happened to try. Round 1
found four gaps in it. Rev 2 removes the list. `_parse_overlay_document` parses each entry,
then runs it through every typed model its consumers build, inside the existing per-entry
`try/except`:

| entry | check | consumer it stands for |
|---|---|---|
| server | `TypeAdapter(ServerConfig)` over its own field values | every consumer: the dataclass annotations are the contract (`transport` is a `Literal`, `auto_start` a `bool`, …) |
| server | `CapabilityCandidate(**manifest_candidate_fields(server), …)` | `catalog_search`. **The same function builds the real candidate** (`handlers.py`), so a field added to the candidate is checked with no list to update |
| server | `config.loader._manifest_server_to_config(server, lambda _: None)` | gateway startup, `gateway.refresh`, provisioning and lazy connects: the **same function**, with no env read |
| CLI | `TypeAdapter(CLIAlternative)` over its own field values | every consumer |
| CLI | `CLIHint(available=False, **cli_hint_fields(cli))` | `rank_cli_hints`, which builds its hint from the **same function** |
| CLI | `check_command` names a program | `probe_clis` → `check_cli` reads `check_command[0]` |

- **Derived, not listed.** Three tests make a consumer stricter, by monkeypatching a stricter
  `CapabilityCandidate`, a stricter startup conversion, or a stricter `CLIHint`. They prove
  that the parse-time check follows. A control test proves the same entries load otherwise
  (M7, M8, M11).
- **Lax pydantic validation, check only.** An int is not a `str`, and a str is not a
  `list[str]`. `"no"` is accepted for a `bool`, as pydantic's lax mode accepts it. The
  values are not rewritten, so behaviour for already-valid entries is byte-identical.
  Coercing `"no"` to `False` would be a separate behaviour change (Non-goals).
- **Overlay entries only.** The shipped parse has no per-entry skip, so a failure there
  would take down the gateway. The shipped entries are held to the same rule by
  `test_every_shipped_entry_passes_every_consumer_check`. All 107 servers and 12 CLIs pass,
  in 6.3 ms, which is not spent on any request path.
- **Unknown `transport` string.** `carrier-pigeon` is now skipped. `ServerConfig.transport`
  is a `Literal`, and with a `url` the startup conversion already rejected it, which aborted
  startup on main.

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

### D9. No value and no overlay name in any overlay log line

- The skip warnings name the entry with `_server_label(name)` / `_cli_label(name)`. A name
  pmcp ships is shown; any other is "an overlay … (name not shown)". This is the rule
  `_server_label` already applied to pin warnings (Consiliency/pmcp#294).
- The reason (`_rejection_reason`) keeps only the first location element of each pydantic
  error, when it is a plain attribute name, plus the error type. A pydantic message quotes
  its input, and deeper location elements can be user dict keys. Any other exception
  becomes its class name.
- The other overlay-entry lines that printed the key are switched to the label: `server_env`
  for an unknown server, the `extra_env` / `api_key_optional_when` field warnings, and
  "overrides existing". The guards (D7) use the same labels.
- `test_every_*_field_…` plants a name sentinel (the key) and a value sentinel (inside every
  container and the 100 KB string). It asserts that neither appears in any captured log
  record, at DEBUG. M14, M15 and M16.
- Consiliency/pmcp#297's `exception_text` wrapping of the skip line composes with this. It
  renders an already value-free reason unchanged.

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
  - `_shape_of` (a cached `TypeAdapter`) and `_field_values`;
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

### `src/pmcp/manifest/environment.py` (modify)
- `probe_clis` guards each `check_command` (G5).

### `src/pmcp/config/loader.py` (modify)
- `resolve_startup_configs` guards each manifest server (G6). This covers gateway startup
  and `gateway.refresh`.

### `tests/test_catalog_overlay_discovery.py` (new, 81 tests; verbatim below)
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
  > skipped when the overlay is read. The warning names the field, never its value, and
  > never shows an overlay entry's name. A blank (`null`) field now means "not set" and
  > takes its default, instead of dropping the entry. See
  > [Consiliency/pmcp#342](https://github.com/Consiliency/pmcp/issues/342).

  Never put a closing keyword next to the number.
- `README.md` § "Private manifest overlay", the "fail-soft" paragraph (`README.md:1157`).
  Replace "a malformed file or a single bad entry logs a warning and is skipped" with:

  > "a malformed file logs a warning and is skipped; so does a single entry that pmcp could
  > not use: a field of the wrong type, such as a `keywords` that is not a list of strings,
  > a non-string `transport`, or a CLI alternative with an empty `check_command`. The
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

## Verification (measured this session on the rev-2 spike; the implementer re-runs each step)

Run from the worktree. Before running:
- A fresh worktree needs `uv sync --all-extras -p 3.10` first.
- On dev0, `unset npm_config_cache npm_config_store_dir pnpm_config_store_dir`.
- Keep `--basetemp` off `/tmp` and outside the checkout.
- Also keep it below no directory holding a `.git`. During this session, something created a
  transient empty `.git` in `/mnt/workspace/worktrees/viperjuice`, and every trust-store test
  under it errored ("resolves inside the checkout") until it went away. The embedding
  proof was re-run with `--basetemp` under `/mnt/workspace/users/viperjuice`.
- Detach long runs with `setsid nohup … < /dev/null &`. A plain `nohup … & disown` mutation
  run died mid-mutant once this session and left a mutated file. It was restored from its
  saved copy.

```bash
# 1. The new module, red on main and on rev 1, green on rev 2
uv run pytest tests/test_catalog_overlay_discovery.py -q -p no:cacheprovider --no-cov --cov-fail-under=0
#   rev-2 spike: 81 passed
#   same file on main c9206a9:      77 failed, 4 passed
#   same file on the rev-1 patch:   61 failed, 20 passed  (the R2 gaps round 1 found, plus every
#                                   name-sentinel leak and every guard test)

# 2. Lint, format, types (CI's own commands)
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
#   -> All checks passed! / already formatted / Success: no issues found in 52 source files

# 3. Touched-suite subset (18 files, list in "The test surface this touches")
#   -> **1955 passed, 1 skipped, 19 deselected, 0 failed** (166.99 s; before the 3-test migration it was 6 failed, the six cases migrated)

# 4. The real gateway, end to end (call.py; R1 table): unchanged from rev 1 on the rev-2 spike
#   -> unapproved demo→[useronly], screenshot→[playwright]; approved demo→[projonly, useronly], screenshot→[playwright, projonly], gadget→[projonly]; revoked as unapproved

# 5. R1 class and consumer differentials
python copycat.py      # main: 329 of 440 queries lose a candidate, 91 servers; rev 2: 0 of 440, 0 servers
python consumers.py    # rev 2: only additions (projonly) differ approved vs revoked

# 6. Mutation driver (27 mutants, appendix). RED requires failed > 0 and errors == 0.
python mutants.py "$PWD"
#   -> 27 of 27 mutants RED (a real test failure and no errors), every restore byte-identical (table below)

# 7. The full suite, once, alone, detached (CI command minus -v)
setsid nohup uv run pytest tests/ -q --tb=short --cov --cov-report= -p no:cacheprovider \
  --basetemp=$WORKTREE_ROOT/pmcp-342-bt/full2 > full2.log 2>&1 < /dev/null &
#   -> 5604 passed, 3 skipped, 80 deselected, 0 failed in 717.44s (0:11:57); coverage 90.15% (alone, 3.10.21, npm vars unset)

# 8. Gates
python3 scripts/check_security_claims.py
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-342-catalog-overlay-*.md
#   -> OK, 129 cited node ids / blocking inconsistencies: 0

# 9. Embedding proof: extract the source patch, the test module and the migration patch from
#    THIS file; on a clean c9206a9 tree: git apply --check, git apply, write the module, run it
#   -> apply-check clean; source, module and migration cmp-identical to the spike; on clean c9206a9 the module plus the two migrated modules: 265 passed
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
- [ ] **Derived, not listed:** a stricter `CapabilityCandidate`, startup conversion or
  `CLIHint` is enforced at parse time with no change to the loader (M7, M8, M11), and the
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
guard mutants narrow the guard's `except` to `ZeroDivisionError`. Run on the rev-2 spike
text embedded below (Python 3.10.21).

| id | mutant (the rule it breaks) | result | first failure | failing tests (first 4) |
|---|---|---|---|---|
| M1 | R1: scoring ignores the base weights (counts the merged manifest) | **RED** 37 failed, 44 passed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable, test_no_overlay_source_changes_a_keyword_weight[user], test_no_overlay_source_changes_a_keyword_weight[project], test_no_overlay_source_changes_a_keyword_weight[env], +33 more |
| M2 | R1: base weights computed after the overlays are merged | **RED** 37 failed, 44 passed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable, test_no_overlay_source_changes_a_keyword_weight[user], test_no_overlay_source_changes_a_keyword_weight[project], test_no_overlay_source_changes_a_keyword_weight[env], +33 more |
| M3 | R1: request_capability's config-server view drops the base weights | **RED** 1 failed, 80 passed (restored=True) | assert None is not None | test_the_config_server_view_keeps_the_base_weights |
| M4 | instance fix instead of the class: raise the IDF floor so a shared keyword passes | **RED** 3 failed, 79 passed (restored=True) | assert {'api': 0.7} == {'api': 0.5} | test_a_hand_built_manifest_is_still_weighted_by_its_own_servers, test_an_explicit_path_load_weights_that_file_alone, test_keyword_match_generic_api_alone_stays_below_threshold |
| M5 | R2: overlay servers not checked against their consumers | **RED** 13 failed, 68 passed (restored=True) | assert not ({'args-str', 'command-int', 'headers-int', 'oidc-issuer-int', 'prm-url-int', ' | test_a_non_string_server_or_cli_key_is_contained[5-int], test_a_non_string_server_or_cli_key_is_contained[True-bool], test_a_non_string_server_or_cli_key_is_contained[None-null], test_the_board_repros_are_skipped_with_a_value_free_warning, +9 more |
| M6 | R2: server check skips ServerConfig's own annotations | **RED** 7 failed, 74 passed (restored=True) | assert ('odd' not in {'playwright': ServerConfig(name='playwright', description='Browser a | test_an_entry_contradicting_its_own_annotations_is_skipped[status-5], test_an_entry_contradicting_its_own_annotations_is_skipped[source-value1], test_an_entry_contradicting_its_own_annotations_is_skipped[replacement-value2], test_an_entry_contradicting_its_own_annotations_is_skipped[auto_start-maybe], +3 more |
| M7 | R2: server check skips the CapabilityCandidate catalog_search builds | **RED** 1 failed, 80 passed (restored=True) | assert ('marked' not in {'playwright': ServerConfig(name='playwright', description='Browse | test_a_stricter_candidate_model_is_enforced_at_parse_time |
| M8 | R2: server check skips the startup/refresh conversion | **RED** 1 failed, 80 passed (restored=True) | assert ('marked' not in {'playwright': ServerConfig(name='playwright', description='Browse | test_a_stricter_startup_conversion_is_enforced_at_parse_time |
| M9 | R2: overlay CLI alternatives not checked against their consumers | **RED** 6 failed, 75 passed (restored=True) | assert not ({'cli-check-empty', 'cli-check-int', 'cli-description-int', 'cli-examples-ints | test_the_board_repros_are_skipped_with_a_value_free_warning, test_a_bad_cli_in_an_approved_project_overlay_is_skipped, test_a_stricter_cli_hint_model_is_enforced_at_parse_time, test_a_cli_contradicting_its_own_annotations_is_skipped[keywords-git], +2 more |
| M10 | R2: CLI check skips CLIAlternative's own annotations | **RED** 2 failed, 79 passed (restored=True) | assert not ({'cli-check-empty', 'cli-check-int', 'cli-description-int', 'cli-examples-ints | test_the_board_repros_are_skipped_with_a_value_free_warning, test_a_cli_contradicting_its_own_annotations_is_skipped[keywords-git] |
| M11 | R2: CLI check skips the CLIHint rank_cli_hints builds | **RED** 1 failed, 80 passed (restored=True) | assert 'markedcli' not in {'git': CLIAlternative(name='git', keywords=['git', 'version con | test_a_stricter_cli_hint_model_is_enforced_at_parse_time |
| M12 | R2: CLI check accepts an empty check_command (probe needs slot 0) | **RED** 1 failed, 80 passed (restored=True) | assert not ({'cli-check-empty', 'cli-check-int', 'cli-description-int', 'cli-examples-ints | test_the_board_repros_are_skipped_with_a_value_free_warning |
| M13 | R2: a YAML null is a value, not absent | **RED** 1 failed, 80 passed (restored=True) | assert 'npx' == 'my-github-fork' | test_null_fields_take_their_defaults_and_keep_the_entry |
| M14 | R2: the skip reason quotes pydantic's text (values reach the log) | **RED** 32 failed, 49 passed (restored=True) | Skipping invalid server entry (server 'puppeteer') in overlay /mnt/workspace/worktrees/vip | test_every_server_field_with_every_bad_shape_is_contained[description], test_every_server_field_with_every_bad_shape_is_contained[keywords], test_every_server_field_with_every_bad_shape_is_contained[command], test_every_server_field_with_every_bad_shape_is_contained[args], +28 more |
| M15 | R2: the server skip warning names the overlay entry | **RED** 28 failed, 53 passed (restored=True) | Skipping invalid server entry ('tok-NAME-sentinel-5f1c9a') in overlay /mnt/workspace/workt | test_every_server_field_with_every_bad_shape_is_contained[description], test_every_server_field_with_every_bad_shape_is_contained[keywords], test_every_server_field_with_every_bad_shape_is_contained[install], test_every_server_field_with_every_bad_shape_is_contained[command], +24 more |
| M16 | R2: the CLI skip warning names the overlay entry | **RED** 7 failed, 74 passed (restored=True) | Skipping invalid cli_alternative ('tok-NAME-sentinel-5f1c9a') in overlay /mnt/workspace/wo | test_every_cli_field_with_every_bad_shape_is_contained[keywords], test_every_cli_field_with_every_bad_shape_is_contained[check_command], test_every_cli_field_with_every_bad_shape_is_contained[help_command], test_every_cli_field_with_every_bad_shape_is_contained[description], +3 more |
| G1 | guard: keyword_weights lets one server's keywords fail the table | **RED** 3 failed, 78 passed (restored=True) | 'NoneType' object is not iterable | test_guard_keyword_weights_skip_an_unusable_server, test_guard_catalog_scoring_skips_an_unusable_server, test_guard_match_capability_skips_an_unusable_server |
| G2 | guard: catalog_search scoring lets one server raise | **RED** 1 failed, 80 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_catalog_scoring_skips_an_unusable_server |
| G3 | guard: catalog_search candidate build lets one server raise | **RED** 1 failed, 80 passed (restored=True) | 1 validation error for CapabilityCandidate | test_guard_catalog_candidate_build_skips_an_unusable_server |
| G4 | guard: rank_cli_hints lets one CLI raise | **RED** 1 failed, 80 passed (restored=True) | 'NoneType' object is not iterable | test_guard_rank_cli_hints_skips_an_unusable_cli |
| G5 | guard: probe_clis lets one command raise | **RED** 1 failed, 80 passed (restored=True) | list index out of range | test_guard_probe_clis_treats_an_unusable_command_as_not_detected |
| G6 | guard: startup/refresh let one manifest server abort resolution | **RED** 2 failed, 79 passed (restored=True) | 1 validation error for LocalMcpServerConfig | test_guard_startup_skips_an_unusable_server, test_guard_refresh_survives_an_unusable_server |
| G7 | guard: match_capability lets one server raise | **RED** 1 failed, 80 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_match_capability_skips_an_unusable_server |
| G8 | guard: request_capability name tier lets one name raise | **RED** 1 failed, 80 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_request_capability_name_tier_skips_an_unusable_name |
| G9 | guard: request_capability name match lets its entry raise | **RED** 1 failed, 80 passed (restored=True) | str expected, not int | test_guard_request_capability_name_match_skips_an_unusable_entry |
| G10 | guard: request_capability category keywords let one server raise | **RED** 1 failed, 80 passed (restored=True) | 'NoneType' object is not iterable | test_guard_request_capability_category_keywords_skip_an_unusable_server |
| G11 | guard: request_capability category candidates let one server raise | **RED** 1 failed, 80 passed (restored=True) | 1 validation error for CapabilityCandidate | test_guard_request_capability_category_candidate_skips_an_unusable_server |

**27 of 27 mutants RED (a real test failure and no errors), every restore byte-identical.**

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
- **Python 3.11 and 3.12.** The new code uses only `TypeAdapter`, `field_validator` and
  dataclass `fields()`, which behave the same across the CI matrix. The module was run on
  3.10.21 only.
- **A real gateway with a malformed overlay.** R2 was measured through `GatewayTools`,
  `resolve_startup_configs` and `gateway.refresh` on the real loader, not through a
  started `pmcp --transport http`. R1 was measured through both.

## Execution Policy

- execute: effort=medium.
- reason: five source files, about 600 changed lines, on the discovery, startup and
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
diff --git a/src/pmcp/config/loader.py b/src/pmcp/config/loader.py
index c71e7f8..f7b5b5b 100644
--- a/src/pmcp/config/loader.py
+++ b/src/pmcp/config/loader.py
@@ -1627,11 +1627,22 @@ def resolve_startup_configs(
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
index 613559f..08781e0 100644
--- a/src/pmcp/manifest/environment.py
+++ b/src/pmcp/manifest/environment.py
@@ -120,8 +120,17 @@ async def probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
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
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index aa5ec3d..1aa7ebd 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -12,7 +12,7 @@ import tempfile
 import threading
 from collections import OrderedDict
 from collections.abc import Iterable, Mapping
-from dataclasses import dataclass, field, replace
+from dataclasses import dataclass, field, fields, replace
 from pathlib import Path
 from typing import Any, Literal, cast
 
@@ -415,6 +415,25 @@ def requires_credential(
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
@@ -512,6 +531,17 @@ class Manifest:
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
@@ -572,8 +602,7 @@ class Manifest:
                 server = self.servers.get(sname)
                 if not server:
                     continue
-                for kw in server.keywords:
-                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
+                for kw_norm in _category_keyword_norms(server):
                     kw_cats.setdefault(kw_norm, set()).add(cat_name)
 
         def _kw_weight(kw_norm: str) -> float:
@@ -601,8 +630,7 @@ class Manifest:
                 server = self.servers.get(sname)
                 if not server:
                     continue
-                for kw in server.keywords:
-                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
+                for kw_norm in _category_keyword_norms(server):
                     if set(kw_norm.split()).issubset(query_words):
                         score += _kw_weight(kw_norm)
 
@@ -642,8 +670,173 @@ class Manifest:
         return matching_clis, matching_servers
 
 
+def keyword_weights(servers: Iterable[ServerConfig]) -> dict[str, float]:
+    """Inverse document frequency of each normalized keyword, floored at 0.5.
+
+    A keyword one server declares weighs 1.0; one that N servers share weighs
+    max(1/N, 0.5). Duplicates within one server count once.
+    """
+    frequencies: dict[str, int] = {}
+    for server in servers:
+        # A server whose keywords are unusable contributes none, rather than
+        # costing every other server its weights (Consiliency/pmcp#342).
+        try:
+            norms = {
+                keyword.lower().replace("-", " ").replace("_", " ")
+                for keyword in set(server.keywords)
+            }
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
+@functools.lru_cache(maxsize=None)
+def _shape_of(kind: type) -> Any:
+    """A pydantic validator for ``kind``'s own field annotations."""
+    from pydantic import TypeAdapter
+
+    return TypeAdapter(kind)
+
+
+def _field_values(entry: Any) -> dict[str, Any]:
+    return {f.name: getattr(entry, f.name) for f in fields(entry)}
+
+
+def _check_server_for_consumers(server: ServerConfig) -> None:
+    """Raise unless every consumer can build its view of ``server``.
+
+    A wrong type in one overlay entry did not stay in that entry: consumers
+    iterate all servers, so it raised in ``catalog_search`` for every query, or
+    aborted startup and ``gateway.refresh`` for every server
+    (Consiliency/pmcp#342). The rule is "an entry any consumer would reject is
+    skipped at parse time", checked by building each consumer's own typed
+    model rather than by a list of fields:
+
+    * ``ServerConfig``'s own annotations (every field, every consumer);
+    * the ``CapabilityCandidate`` that ``catalog_search`` builds;
+    * the ``LocalMcpServerConfig``/``RemoteMcpServerConfig`` that startup,
+      refresh, provisioning and lazy connects build
+      (``config.loader._manifest_server_to_config``, with no env read).
+    """
+    from pmcp.config.loader import _manifest_server_to_config
+    from pmcp.types import CapabilityCandidate
+
+    _shape_of(ServerConfig).validate_python(_field_values(server))
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
+    """Raise unless every consumer can use ``cli`` (see the server check).
+
+    ``rank_cli_hints`` builds a ``CLIHint`` from it on every query, and
+    ``probe_clis`` runs ``check_command`` and needs a program in slot 0.
+    """
+    from pmcp.types import CLIHint
+
+    _shape_of(CLIAlternative).validate_python(_field_values(cli))
+    CLIHint(available=False, **cli_hint_fields(cli))
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
@@ -671,14 +864,16 @@ def _parse_extra_env(
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
@@ -687,7 +882,7 @@ def _parse_extra_env(
             parsed[key] = str(value)
         else:
             logger.warning(
-                f"Skipping '{field_label}' key '{key}' for server '{name}': "
+                f"Skipping a '{field_label}' key for {_server_label(name)}: "
                 f"unsupported value type {type(value).__name__}"
             )
     return parsed
@@ -708,7 +903,7 @@ def _parse_api_key_optional_when(
         return []
     if not isinstance(raw, list):
         logger.warning(
-            f"Ignoring 'api_key_optional_when' for server '{name}': not a list"
+            f"Ignoring 'api_key_optional_when' for {_server_label(name)}: not a list"
         )
         return []
 
@@ -716,12 +911,13 @@ def _parse_api_key_optional_when(
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
+                f"The entry for {_server_label(name)} names its own credential variable "
                 f"('{item}') in 'api_key_optional_when'; ignoring — a "
                 f"credential cannot relax itself"
             )
@@ -994,7 +1190,14 @@ def _materialize_version_pin_soft(server: ServerConfig) -> ServerConfig:
 
 
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
 
@@ -1217,11 +1420,15 @@ def _parse_overlay_document(
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
 
@@ -1230,12 +1437,15 @@ def _parse_overlay_document(
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
 
@@ -1525,6 +1735,11 @@ def _build_manifest(
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
@@ -1572,7 +1787,7 @@ def _build_manifest(
                 if name in servers:
                     logger.warning(
                         f"Manifest overlay ({label}) from {overlay_path} overrides "
-                        f"existing server '{name}'"
+                        f"existing {_server_label(name)}"
                     )
             servers.update(overlay_servers)
             cli_alternatives.update(overlay_clis)
@@ -1585,7 +1800,8 @@ def _build_manifest(
                 if existing is None:
                     logger.warning(
                         f"Manifest overlay ({label}) from {overlay_path} has a "
-                        f"'server_env' patch for unknown server '{name}': skipped"
+                        f"'server_env' patch for an unknown server "
+                        f"({_server_label(name)}): skipped"
                     )
                     continue
                 servers[name] = replace(
@@ -1619,6 +1835,7 @@ def _build_manifest(
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
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 09e9f34..1fed10f 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -79,7 +79,12 @@ from pmcp.manifest.installer import (
     get_job_manager,
     InstallError,
 )
-from pmcp.manifest.loader import load_manifest, npm_env_may_redirect
+from pmcp.manifest.loader import (
+    _server_label,
+    load_manifest,
+    manifest_candidate_fields,
+    npm_env_may_redirect,
+)
 from pmcp.manifest.package_identity import PackageIdentity, resolve_package_identity
 from pmcp.manifest.matcher import (
     _keyword_match_score,
@@ -783,6 +788,14 @@ def _summarize_arg_schema(
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
 
@@ -1146,16 +1159,23 @@ class GatewayTools:
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
+                norm_name = name.lower().replace("-", "").replace("_", "")
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
@@ -1169,16 +1189,17 @@ class GatewayTools:
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
@@ -1187,18 +1208,16 @@ class GatewayTools:
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
@@ -3312,6 +3331,7 @@ class GatewayTools:
             cli_alternatives=dict(manifest.cli_alternatives),
             servers=merged_servers,
             discovery_queue_path=manifest.discovery_queue_path,
+            base_keyword_weights=manifest.base_keyword_weights,
         )
 
     def _get_server_env_metadata(
@@ -3610,10 +3630,15 @@ class GatewayTools:
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
+                norm_to_server[
+                    n.lower().replace("-", "").replace("_", "").replace(" ", "")
+                ] = n
+            except Exception as exc:
+                _log_unusable_manifest_entry("request_capability", n, exc)
         name_match: str | None = None
         for window_size in (3, 2, 1):
             for i in range(len(query_words) - window_size + 1):
@@ -3636,28 +3661,39 @@ class GatewayTools:
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
@@ -3727,16 +3763,17 @@ class GatewayTools:
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
@@ -3747,7 +3784,10 @@ class GatewayTools:
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
    _overlay_with_marked_entry()
    assert "markedcli" not in load_manifest().cli_alternatives


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("status", 5),
        ("source", ["x"]),
        ("replacement", {"x": 1}),
        ("auto_start", "maybe"),
        ("supports_url_elicitation", "maybe"),
        ("requires_api_key", "maybe"),
        ("transport", "carrier-pigeon"),
    ],
)
def test_an_entry_contradicting_its_own_annotations_is_skipped(
    field_name: str, value: Any
) -> None:
    """Fields no consumer reads today still follow ServerConfig's annotations."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "odd": {"keywords": ["zzodd"], field_name: value},
                    "good": GOOD_SERVER,
                }
            }
        ),
    )
    servers = load_manifest().servers
    assert "odd" not in servers and "good" in servers


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("keywords", "git"), ("help_command", [1]), ("check_command", "git")],
)
def test_a_cli_contradicting_its_own_annotations_is_skipped(
    field_name: str, value: Any
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": {"odd": {field_name: value}, "ok": {}}}),
    )
    clis = load_manifest().cli_alternatives
    assert "odd" not in clis and "ok" in clis


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
index 4f4eead..a66beb7 100644
--- a/tests/test_version_pin.py
+++ b/tests/test_version_pin.py
@@ -380,7 +380,12 @@ servers:
 def test_a_pin_on_a_malformed_entry_costs_only_that_entry(
     shape: str, caplog: pytest.LogCaptureFixture
 ) -> None:
-    """HEAD loads every entry of this overlay; the pin must not change that."""
+    """A non-string argv costs only its own entry.
+
+    Before Consiliency/pmcp#342 this entry loaded, and then aborted startup and
+    `gateway.refresh` for every server (`LocalMcpServerConfig` rejects it). It
+    is now skipped at parse time; the pin must still cost nothing else.
+    """
     shipped_count = len(load_manifest().servers)
     _user_overlay(
         f"""
@@ -396,8 +401,8 @@ servers:
     with caplog.at_level(logging.WARNING):
         manifest = load_manifest()  # must not raise
 
-    assert len(manifest.servers) == shipped_count + 1
-    assert manifest.servers["malformed"].version is None
+    assert len(manifest.servers) == shipped_count
+    assert "malformed" not in manifest.servers
     assert manifest.servers["firecrawl"].args == ["-y", "firecrawl-mcp"]
     assert any("an overlay server (name not shown)" in m for m in _warnings(caplog))
     assert not any("malformed" in m for m in _warnings(caplog))
@@ -705,15 +710,18 @@ async def test_no_refused_pin_or_credential_reaches_a_log_or_update_output(
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

### `mutants.py` (mutation driver, revision 2)

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
    ("M6", "R2: server check skips ServerConfig's own annotations", LOADER,
     "    _shape_of(ServerConfig).validate_python(_field_values(server))\n", "", TESTS),
    ("M7", "R2: server check skips the CapabilityCandidate catalog_search builds", LOADER,
     "    CapabilityCandidate(\n        name=server.name,\n",
     "    dict(\n        name=server.name,\n", TESTS),
    ("M8", "R2: server check skips the startup/refresh conversion", LOADER,
     "    _manifest_server_to_config(server, lambda _key: None)\n", "", TESTS),
    ("M9", "R2: overlay CLI alternatives not checked against their consumers", LOADER,
     "                _check_cli_for_consumers(cli)\n", "", TESTS),
    ("M10", "R2: CLI check skips CLIAlternative's own annotations", LOADER,
     "    _shape_of(CLIAlternative).validate_python(_field_values(cli))\n", "", TESTS),
    ("M11", "R2: CLI check skips the CLIHint rank_cli_hints builds", LOADER,
     "    CLIHint(available=False, **cli_hint_fields(cli))\n", "", TESTS),
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
     "                for keyword in set(server.keywords)\n            }\n        except Exception:\n",
     "                for keyword in set(server.keywords)\n            }\n        except ZeroDivisionError:\n",
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
]

env = dict(os.environ)
for k in ("npm_config_cache", "npm_config_store_dir", "pnpm_config_store_dir"):
    env.pop(k, None)
bt = Path(os.environ["WORKTREE_ROOT"]) / "pmcp-342-bt" / "mut3"
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
