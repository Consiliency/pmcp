# Detailed plan: an overlay never hides another server from discovery — weights from the base manifest, and one entry never takes down the others (Consiliency/pmcp#342)

> Written on main `c9206a9` (dev0, a team host), worktree `pmcp-342`, branch
> `plan/342-catalog-overlay`. For revision 6, `origin/main` was re-fetched and is
> `6edf8a4` (Consiliency/pmcp#330's task-units change). It touches `handlers.py`,
> `server.py`, `client/manager.py` and `environment.py` near this patch. The rev-6 patch was
> merged onto it three-way with no conflict; only one context line differs, and the
> patch's own added and removed lines are byte-identical to the `b2884db` version. The
> embedding proof, the new module and the full suite below run on `6edf8a4`. The mutation
> table was measured on `31d0f40` with the same hunks, and every one of its 59 anchors
> matches exactly once on `6edf8a4`. Every number below was measured this session on one
> of:
> - main;
> - the commit before the manifest cache (`a622ec5`);
> - the rev-1 to rev-5 spikes;
> - the rev-6 spike.
>
> The spike was then removed, and this PR carries only this file.
>
> **Bounded-plan verdict: within threshold, larger than rev 1.**
> - Source: eight files change (+1020/−252: `cli_commands/secrets.py` +38/−22, `client/manager.py` +16/−1, `config/loader.py` +64/−4, `manifest/environment.py` +26/−8, `manifest/loader.py` +553/−31, `manifest/matcher.py` +127/−64, `server.py` +6/−17, `tools/handlers.py` +190/−105).
> - Tests: one module is new (`tests/test_catalog_overlay_discovery.py`, 271 tests).
>   Three existing tests are migrated (D4, D9).
> - Docs: one CHANGELOG bullet, one CHANGELOG upgrade note, and one README paragraph.
> - It is still one conceptual change: an overlay can add to discovery, and one entry can
>   never take the rest down.
>
> **Revision 6** (2026-10-04): board round 5 on `4798034` (Consiliency/pmcp#343). Codex
> and gemini reviewed, grok timed out, and claude disagreed. The field walk (1039 cases),
> the value-free parse warnings, the category-tier fix and the flake attribution all held.
> Rev 6 fixes three blocking findings and checks one note:
> - **B11 (claude F001, codex F003)**: an overlay CLI named like a shipped server won
>   `request_capability`'s *name* tier, for 23 of 107 shipped names on rev 5 (98 on main).
>   Rev 5 had gated one tier. Rev 6 applies **one rule where CLI and server results are
>   combined: an overlay CLI ranks below every server tier**, in `request_capability` and
>   `match_capability`. `catalog_search` never combines them. The tiers are derived from
>   the code (*Every discovery tier, derived*). The differential now generates an overlay
>   CLI for every shipped server name, every category word and every shipped keyword (D2;
>   mutants K1, K3, K4).
> - **B12 (codex F002, claude F002)**: the JSON-native check named an arbitrary overlay key
>   in its WARNING, and dropped entries main loads (`added: 2026-10-04`). Now only the
>   schema fields the parsers read are checked (`_SERVER_SCHEMA_KEYS` / `_CLI_SCHEMA_KEYS`,
>   derived from the parsers' source by a test). Unknown keys are ignored, as on main. A
>   field that is stored but never consumed drops to its default and keeps its entry, and
>   a rejection names only the schema field (D4; J1–J3).
> - **B13 (codex F001)**: a YAML *constructor* error (`!!int x` → `ValueError`, `!!bool x` →
>   `KeyError`, `!!timestamp x` → `AttributeError`) escaped the overlay parser, which caught
>   only `YAMLError`. It stopped the shipped manifest from loading, and its text quoted the
>   value. The parse boundary now contains **every** exception value-free, and a generated
>   sweep runs 15 invalid-tag documents as user, env and project overlays (D9; E1).
> - **Gemini's note**: the quoted `kw in category_keywords.get(best_cat, [])` is **not in
>   the code**. A grep of the rev-5 patch, and of the tree, finds no CLI comparison in
>   `get_servers_in_category`. `search_by_keyword`'s `matching_clis` compares strings. A
>   test pins that category selection reads no CLI.
>
> **Revision 5** (2026-10-04): board round 4 on `f1d6bad`. Value-free YAML parse errors;
> parse, don't validate; overlay CLIs kept off the category tier; the rev-4 full-suite
> failure attributed to a pre-existing leak in `tests/test_progressive_disclosure.py`.
>
> **Revision 4** (2026-10-04): board round 3 on `403d7e1`. Every cross-server statistic is
> base-only, and every field is checked against every consumer that reads it. Both sets
> were derived mechanically.
>
> **Revision 3** (2026-10-04): board round 2 on `bc68f23`. The log paths were covered,
> main's keyword counting was restored, and the check runs only what consumers run.
>
> **Revision 2** (2026-10-03): board round 1 on `bd5c25f`. Consumer-model checks and
> per-consumer guards; `null` as absent; "within the result limit".

## Round 5 board findings (Consiliency/pmcp#343 @ `4798034`)

| finding | seat | reproduced (rev-6 tests on the rev-5 patch) | resolution |
|---|---|---|---|
| **B11** an overlay CLI named like a shipped server wins the name tier (`airbnb`, `redis`; 23 of 107 names) | claude F001, codex F003 | yes: both falsifiers and the all-terms differential fail on rev 5 (and on main) | D2: one rule at the CLI/server combination point. Overlay CLIs are excluded from the shipped-CLI tier and answer only after every server tier. K1, K3, K4 |
| **B12** the JSON-native check echoes an overlay key and drops entries main loads | codex F002, claude F002 | yes: both falsifiers, the 5 stored-only cases and the schema-key derivation fail on rev 5 | D4: schema fields only; unknown keys ignored; stored-only fields fall back to their default; messages name schema fields only. J1–J3 |
| **B13** a YAML constructor error (`ValueError`, `KeyError`, `AttributeError`) escapes the parse boundary, stops the shipped manifest, and quotes the value | codex F001 | yes: codex's `_build_manifest` falsifier and 12 of 45 invalid-tag cases (`!!int`, `!!bool`, `!!timestamp`, `!!float` × user/env/project) fail on rev 5; the other 33 were already `YAMLError`s | D9: the overlay parse contains every exception and logs path, class and position. E1 |
| gemini: `kw in category_keywords.get(best_cat, [])` never matches | gemini | not present: no such expression exists in the rev-5 patch or the tree (grep) | `test_gemini_note_category_selection_reads_no_cli` pins that category selection reads no CLI |

## Round 4 board findings (Consiliency/pmcp#343 @ `f1d6bad`)

| finding | seat | reproduced (rev-5 tests on the rev-4 patch) | resolution |
|---|---|---|---|
| **B9** a malformed overlay's YAML error is logged with PyYAML's text, which quotes the source line, so a value reaches a WARNING | codex F002, grok F001 | yes: all 42 `test_a_malformed_overlay_is_reported_without_any_value` cases (14 documents × user/env/project) and codex's falsifier fail on rev 4 | D9: `yaml_error_text` gives the class plus line and column, at every derived site; the `load_manifest` catch sites log only the class. Y1–Y3 |
| **B10** inheritance validated a throwaway model; `command: !!binary bnB4` stayed bytes, and `filter_self_references` raised for every config | codex F001 | yes: codex's falsifier and 6 of 8 YAML-tag walk cases (`!!binary`, timestamp, `!!set`, `!!omap`, `.inf`, `.nan`) fail on rev 4; `!!float` on a string field and `!!python/*` were already refused | D4: parse, don't validate. The entry must be JSON-native, and the kept entry carries each consumer model's validated values. P1–P6 |
| F001 an overlay CLI (new, or overriding `git`) pre-empts `request_capability`'s category tier | claude | yes: the falsifier and the differential's `cli-new`, `cli-override-git` and `cli-everything` cases fail on rev 4 (and on main) | D2: an overlay CLI answers only when the category tier finds no server; a shipped CLI keeps main's precedence. K1, K2 |
| N1 a local entry's `oidc_issuer_url: 5` (or `supports_url_elicitation: maybe`) is kept and shown by `pmcp secrets check` | claude | yes | D4: the remote auth view is built for every entry, and its validated values are kept |
| N2 main-side counts off by one | claude | yes: the counting in rev 4 was wrong | re-measured on `2adcd9a`: 148 failed, 64 passed (the 64 pin main's behaviour: must-load, main-weighting, controls) |
| N3 the AST walk selects consumers by substring | claude | — | the limit is stated in the consumer section, with the generic-reader greps (`derive5.txt`) |

## Round 3 board findings (Consiliency/pmcp#343 @ `403d7e1`)

| finding | seat | reproduced (rev-4 tests on the rev-3 patch, and `falsifiers.py`) | resolution |
|---|---|---|---|
| **B7** the category tier's span and score read the merged servers. Replacing `playwright` with `keywords: [markdown]` empties `request_capability("markdown")` | grok F001 (and `postgresql`, `voice`, `wiki`) | yes: main and rev 3 `not_available`, rev 4 the same 9 servers as before the overlay. `test_a_replaced_category_server_does_not_empty_another_category` and the differential's `grok-f001` and `replace-mapped` cases fail on rev 3 | D2: every cross-server statistic is base-only. Statistics table below; mutants C1, C2 |
| (derived) `request_capability`'s name index was last-wins, so an overlay name that normalises like a shipped one took its slot | found by the statistics derivation | yes: `test_an_overlay_name_aliasing_a_shipped_name_keeps_the_shipped_match` fails on rev 3 | base names win ties. Mutant C3 |
| **B8** a remote entry with `args: 5` passes the check, then `_merge_manifest_defaults` raises inside `load_configs`, aborting every config | codex F051 | yes: main and rev 3 `TypeError: Value after * must be an iterable`; rev 4 loads `healthy`. The walk's `command`, `args` and `headers` rows, the falsifier test and three field rows fail on rev 3 | D4: the check runs every consumer that reads a field, whatever the path; consumer table below. D7: `load_configs` contains one entry (guard F3). Mutants F1, F3 |
| F1 `pmcp secrets` had one `try` around the whole manifest loop: a bad `headers` silently dropped every later server's auth metadata | claude | yes: main and rev 3 lose `zz-good-remote`'s metadata; rev 4 keeps it | D4 runs the per-server read; D7 makes the loop per-server (F4). Mutants F2, F4 |
| (found while spiking) the inheritance check's `model_dump()` emitted a pydantic `UserWarning` quoting the field value (`input_value=…`) | — | yes: the walk now records warnings and fails on rev 4 without `warnings=False` | `model_dump(warnings=False)`; mutant F5 |
| F2 D9's exclusion wording | claude | by reading, plus the seat's live log | D9 states the lazy-start, third-party DEBUG and `pmcp refresh` exclusions exactly |

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
| `request_capability` tier 1 (name map, name-match candidate) and tier 2 (`get_servers_in_category`, category candidates) | names; category span and score over `_CATEGORY_MAP`; builds `CapabilityCandidate` | identical for the 9 *additive* queries — but a *replacing* overlay moves the category statistics (round 3, B7), which rev 1–3 missed | raises: name map (int/bool key), category keywords, candidate build | **fixed in rev 4:** base-only category statistics (D2); R2: D4 + G8–G11 |
| gateway startup (`server.py:750`) and `gateway.refresh` (`handlers.py:1926`), both through `resolve_startup_configs` → `_manifest_server_to_config` | builds `Local`/`RemoteMcpServerConfig` for every manifest server | name set ± `projonly` | `ValidationError` aborts resolution **for every server** | **R2 fixed:** D4 + G6 |
| `describe`, `provision`, `auth_connect`, `update_server` / `pmcp update`, `_resolve_lifecycle_target`, `_finalize_server_ready`, `sync_environment`, `_materialised_pin`, `_auth_env_options` | `get_server(name)` | `get_server` differs for **0** shared names | per-entry by nature: a lookup of one name. D4 skips the bad entry before any of them sees it | ruled out (name lookup) |
| `load_configs` defaults, `pmcp secrets`, refresher | rev 3 marked these ruled out; the rev-4 derivation shows each reads fields of every entry | — | `load_configs` and `secrets` abort / drop siblings (round 3) | **fixed in rev 4** — see the derived consumer table below |
| `config_status`, `get_startup_policy`, `pmcp config`, `pmcp init`, `manifest/sync.py` | name sets, or per-name fields | same name set ± `projonly`; `load_manifest` never raised | — | ruled out |
| `gateway.health` | client-manager statuses | — | — | ruled out |
| `Manifest.search_by_keyword` | substring over keywords | no in-tree caller | D4 keeps `keywords` a list of str | ruled out |

### Every statistic computed across servers, derived (rev 4)

Derivation (`aggr.txt`, appendix): `grep` every aggregation over a manifest collection in
`src/pmcp`, that is every `for`, comprehension, `len`, `set` or `sum` over `.servers`,
`manifest_servers`, `merged_servers`, `manifest_by_name`, `_CATEGORY_MAP` or
`cli_alternatives`. That gives 36 sites on the rev-4 tree, plus `keyword_weights`' own loop over its
argument (37 lines in `aggr.txt`). Every site is classified. A *statistic* is a value computed from several entries
that changes how another entry is scored or resolved.

| site | what it computes | cross-entry statistic? | rev 4 |
|---|---|---|---|
| `manifest/loader.py` `keyword_weights` (via `_manifest_keyword_weights`) | keyword frequency → IDF weight | **yes** | base-only (`base_keyword_weights`, D2, rev 1) |
| `manifest/loader.py` `get_servers_in_category`: keyword → set of categories | category span → span weight (0.1 / 0.3 / 0.7 / 1.0) | **yes** | **base-only** (`base_category_keywords`, rev 4) |
| `manifest/loader.py` `get_servers_in_category`: per-category keyword hits | the category score and the winning category | **yes** | **base-only** (same index, rev 4) |
| `tools/handlers.py` `request_capability` name index `norm_to_server` | normalised name → server (last wins) | **yes**: one name can take another's slot | **base names win ties** (`setdefault`, rev 4) |
| `tools/handlers.py` `_manifest_candidates_for_query` loop | per-server score with base weights, then sort and limit | no (ranking only) | top-N displacement is the documented exception (D10) |
| `manifest/matcher.py` `_keyword_match` loop | per-server score with base weights, then best | no (top-1) | displacement only by an overlay entry (tested) |
| `manifest/matcher.py` `rank_cli_hints`; `handlers.py` `_build_cli_probe_configs` | per-CLI score with no weights; per-CLI probe config | no | — |
| `manifest/loader.py` `get_category_summary` | counts of mapped names present, total servers | no: display text, and an overlay can only add | — |
| `manifest/loader.py` `get_auto_start_servers`, `search_by_keyword` | per-entry filter | no | — |
| `manifest/sync.py` `sync_registry_to_manifest` | name / package / `replacement` lookup maps for registry classification | per-entry lookups. It is exported but has **no in-tree caller** | ruled out, and stated |
| `config/loader.py` `_coerce_manifest_servers`, `resolve_startup_configs` loop, `known_names` | per-server startup classification; name set | no | guarded per entry (G6) |
| `cli_commands/secrets.py` manifest loop | per-server auth metadata | no, but one entry could end the loop | per-server (F4, rev 4) |
| `cli.py`, `server.py`, `summary/generator.py`, `manifest/refresher.py`, `handlers.py` health and registry, `policy/policy.py` | descriptions cache, registry cache, client statuses, policy lists | not manifest statistics | — |

**The invariant, tested by a differential.** For generated overlays, no non-overlay server
leaves the candidates of any query, on `catalog_search` (limit lifted), `request_capability`
or `match_capability`. The overlays are additive (4 servers), replacing 3 shipped unmapped
names, replacing 6 `_CATEGORY_MAP` names (each with 25 keywords drawn from the category
vocabulary), grok's case, and one server declaring the whole vocabulary. The queries are the
whole category vocabulary: every keyword of every mapped server plus every category name.

There are two documented exceptions:
- **Top-N displacement** (D10). `catalog_search` is compared with the limit lifted, and
  `match_capability` may pick an overlay server instead.
- **Resolution precedence** in `request_capability`. It returns one resolution, with name
  tier > CLI tier > category tier. A query that names an overlay server resolves to it, as
  on main. The differential skips only queries that share a word with an overlay server's
  name.

On the rev-3 patch, the `grok-f001` and `replace-mapped` cases fail; on rev 4 all five pass.

### Every consumer of every field, derived (rev 4)

Derivation (`consumers_ast.py`, appendix): an AST walk of every function in `src/pmcp`
whose body mentions a manifest source. The sources are `load_manifest`, `get_server`,
`manifest_server(s)`, `ManifestServerConfig`, `ServerConfig`, `CLIAlternative`,
`cli_alternatives`, `manifest.servers`, `merged_manifest` and `manifest_by_name`. For each
such function, the walk records which `ServerConfig` / `CLIAlternative` fields it reads, as
attributes or through `getattr`. It finds **86 functions** (`consumers_ast.out`). The table
groups them by whether one entry can affect *another*.

**Iterating consumers** (one bad entry could take down siblings). Each is either run by the
parse-time check, or reads only fields that a check run on every entry already constrains,
and each is also guarded per entry.

| consumer (derived) | fields it reads | run by the check? | guard |
|---|---|---|---|
| `_manifest_candidates_for_query`, `keyword_weights` | name, keywords, + `manifest_candidate_fields` | yes: name/keyword scoring, `CapabilityCandidate` | G1–G3 |
| `request_capability` name index, name match, category, candidates; `get_servers_in_category`, `_category_keyword_norms` | name, keywords, description, env_var, requires_api_key | yes: name normalisation, keyword scoring, credential lookups, candidate | G8–G11 |
| `_keyword_match` | name, keywords | yes | G7 |
| `rank_cli_hints` / `_rank_one_cli`, `_build_cli_probe_configs`, `probe_clis` | every `CLIAlternative` field | yes: `_rank_one_cli`, `check_command[0]` | G4, G5 |
| `resolve_startup_configs`, `add_config`, `_eager_requires_credential`, `_manifest_server_to_config`, `credential_requirement`, `credential_storage_key` | args, command, extra_env, env_var, secret_key, api_key_optional_when, requires_api_key, url, transport, headers, the five metadata URLs, declared_scopes, supports_url_elicitation, auto_start | yes: the real conversion; credential lookups | G6 |
| **`load_configs` → `_merge_manifest_defaults`** (configured-default inheritance, any transport) | command, args, extra_env, env_var, secret_key | **yes (rev 4):** run with a partial `LocalMcpServerConfig(command="", args=[])`, and the result validated as the `LocalMcpServerConfig` startup consumes | **F3 (rev 4)** |
| **`pmcp secrets` `_extract_required_keys`** | the five metadata URLs, declared_scopes, supports_url_elicitation, headers; env_var, extra_env (via `requires_credential`) | **yes:** every field it reads is the auth view's validated value (rev 5; rev 4 ran `manifest_secret_metadata` itself) | **F4 (rev 4)** |
| `manifest/refresher.py` `refresh_all`, `check_staleness` (`pmcp refresh`) | command, args, package, version, extra_env, env_var, secret_key | not run, because it resolves packages over the network. Every field it reads is constrained on every entry: `command` and `args` by the inheritance check (iterable of str), `package` by the candidate, `version` by `_parse_version_pin`, and `extra_env` and the credential names by the parsers and candidate. `detect_package_type` on a `str` `args` iterates it as on main (measured: no raise) | its own per-server `try` around `refresh_server` |
| `identity.is_self_reference` / `filter_self_references`, `server._kill_orphan_processes`, `handlers._refresh_config_unchanged` | args, command, url, headers on the **converted** `ResolvedServerConfig` | indirectly: they read the conversion's typed output | — |
| `run_status`, `run_config`, `config_status`, `get_startup_policy`, `get_auto_start_servers`, `run_init` | name, status, source, headers, auto_start; `run_init` reads args, command, description and env_var of curated starter names | every field read is constrained above | — (CLI and admin views) |

**Per-name consumers** (one entry, only itself). These look up `get_server(name)`, or
consume the converted config for one server. A bad entry is already skipped at parse time;
otherwise only its own operation fails. They are:
- `provision`, `provision_gate` (`_config_runs_exactly`, `_manifest_package_names`: args,
  command, install, package);
- `installer` (install, env_var, extra_env);
- `auth_connect`, `update_server`, `refresh_server`, `_materialised_pin`,
  `_resolve_lifecycle_target`, `_finalize_server_ready`, `register_discovered_server`;
- the client manager's connect paths.

`install` is read only here, and per name, which is why an int in an install argv still
loads, as on main (rev-3 migration).

`doctor` (`collect_remote_header_diagnostics`) reads `.mcp.json` server entries, not the
manifest, so it is not a manifest consumer. The brief's "doctor" is covered by that
reading.

**The field walk now exercises this table.** It runs every `ServerConfig` field and every
`CLIAlternative` field × 8 shapes × {remote, local}: rev 3 stripped `url` for some shapes.
It uses the real `load_configs`, with `~/.mcp.json` holding a partial entry named like the
bad overlay entry and a healthy sibling. It drives:
- `catalog_search` (`include_offline` on and off), `request_capability`;
- startup resolution, `gateway.refresh` (real `load_configs`);
- `load_configs` directly (the `healthy` sibling must load);
- `pmcp secrets`' `_extract_required_keys` (a later good remote server's metadata must
  survive).

No log record or Python warning may carry the name or value sentinel, and no consumer guard
may fire.

**The derivation's limit (claude N3), stated.** `consumers_ast.py` selects functions whose
source mentions a manifest marker. A consumer that received a `ServerConfig` under a name
containing none of the markers, and read it generically, would be missed. The evidence that
none does today is `derive5.txt`'s grep for the generic readers (`asdict(`, `vars(`,
`__dict__`, `fields(`, `model_dump(`). Its only hit on manifest-derived data is the check's
own `inherited.model_dump(warnings=False)`. The others read the registry cache, auth
messages, policy diagnostics, task records and transport payloads. A type-based derivation
(annotations plus call graph) would be stronger. It is not needed for this change, so it is
named here and not built.

### The test surface this touches

- **New:** `tests/test_catalog_overlay_discovery.py` (271 tests).
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
- **Touched suites, by name (24 files):** `tests/test_catalog_overlay_discovery.py`, `tests/test_client_manager.py`, `tests/test_client_manager_reconnect.py`, `tests/test_config_loader.py`, `tests/test_credential_gates_handlers.py`, `tests/test_credential_gates_startup.py`, `tests/test_credential_optionality_e2e.py`, `tests/test_env_overlay_provenance.py`, `tests/test_lazy_start.py`, `tests/test_manifest.py`, `tests/test_manifest_cache.py`, `tests/test_manifest_overlay.py`, `tests/test_manifest_provision.py`, `tests/test_offline_discovery.py`, `tests/test_phase4_e2e.py`, `tests/test_project_source_consent_manifest.py`, `tests/test_scoped_advisor_audit.py`, `tests/test_server.py`, `tests/test_startup_policy_reapproval.py`, `tests/test_startup_resolver.py`, `tests/test_tools.py`, `tests/test_trust_cli.py`, `tests/test_version_pin.py`, `tests/test_secrets_command.py`.
  Result: **2489 passed, 1 skipped, 19 deselected, 0 failed** (874.68 s, on b2884db + the same hunks); on 6edf8a4 the full suite (step 8) covers them.

### Cost

- `load_manifest()` cache hit, median of 200 calls, 3 rounds alternating (rev 1, unchanged
  by rev 2, because the checks run only when an overlay is parsed): main 0.93–1.03 ms,
  spike 0.95–1.02 ms.
- The rev-3 consumer check costs 1.2 ms for all 107 shipped servers and 12 CLIs, or
  about 0.01 ms per entry. Shipped entries are not checked at load; they are checked by a
  test. An overlay pays that once per entry per cache miss.

### Every discovery tier, derived (rev 6)

Derivation: every `return CapabilityResolution(` in `request_capability`, in source order,
and the result lists of `catalog_search` and `_keyword_match` (`match_capability`). `awk`
over `handlers.py` / `matcher.py`.

| entry point | tier (in order) | what answers | overlay CLI, rev 6 |
|---|---|---|---|
| `request_capability` | 1. name | a manifest server whose normalised name is in the query; yields to a colliding **shipped** CLI unless the query says "server"/"mcp"/… (main) | cannot collide: `name_match_collides_with_cli` sees only shipped CLIs |
| | 2. shipped CLI (`use_cli`) | the first available **shipped** CLI hint | excluded |
| | 3. category (`pick_from_category`) | the base-only category tier (D2) | — |
| | 4. configured server | a `.mcp.json` server by keyword | — |
| | **5. overlay CLI (`use_cli`)** | **the first available overlay CLI hint: only here, after every server tier** | answers |
| | 6. registry | only for an unknown PascalCase service | — |
| | 7. `not_available` | — | — |
| `catalog_search` | separate lists | `results` (tools), `manifest_candidates`, `registry_candidates`, `cli_hints` | in `cli_hints` only. CLI and server results are never combined, so nothing to rank |
| `match_capability` / `_keyword_match` | single best | shipped CLIs and servers, best score ≥ 0.2 (CLI preferred on ties, as on main) | considered only if neither a shipped CLI nor a server reaches 0.2 |

The rule is stated once, at the combination point, and not per tier. Overlay CLIs are
removed from the shipped-CLI candidate, `cli_hint_match`, before any tier reads it. They
come back in exactly one place, after the last server tier. That also covers tiers added
later: a new server tier placed before step 5 outranks overlay CLIs without further
change.

### The rev-4 full-suite failure, pinned down (rev 5)

In the rev-4 full run, `tests/test_project_source_consent_config.py::test_an_absent_project_mcp_json_warns_about_nothing`
failed. It captured asyncio's "Task exception was never retrieved" for
`ClientManager._drain_outbound`, with
`RuntimeError: <Queue …> is bound to a different event loop`. The coordinator then ran
main `bb6ddde` and got a clean full suite. Measured since then, in a separate scratch
worktree (`pmcp-342-flake`) with `--cov`, as CI runs it:

| run | tree | result |
|---|---|---|
| patch 1 | `bb6ddde` + rev-4 patch | 5669 passed, 0 failed |
| patch 2 | `bb6ddde` + rev-4 patch | 5669 passed, 0 failed. A first attempt died at 13% when `/mnt/workspace` reached 0 bytes free. The volume was full for every user; the run was repeated once space returned. |
| main 1 | `bb6ddde` | 5523 passed, 0 failed |
| main 2 | `bb6ddde` | 5523 passed, 0 failed |

So the failure did not reproduce in four runs, and a timing-dependent leak needs a
deterministic way to attribute it. `leakfind.py` (appendix) is a pytest plugin that runs
`gc.collect()` after every test and records any "never retrieved" task exception against
the test that just finished. A task freed by an earlier test would already have been
collected after that test, so the record names the test that created the task, or the one
that released the last reference to it.

| tree | orphaned task exceptions | attributed to |
|---|---|---|
| main `bb6ddde` | **1**: `RuntimeError('<Queue …> is bound to a different event loop')` | `tests/test_progressive_disclosure.py::TestToolsCoverageMatrix::test_high_priority_context7_tools_searchable` |
| `bb6ddde` + rev-4 patch | **1**, the same RuntimeError | the same test, `test_high_priority_context7_tools_searchable` |

**Cause (pre-existing, not this patch).** `tests/test_progressive_disclosure.py` has a
**module-scoped async fixture**, `gateway_with_servers`. It builds a real `ClientManager`
and `connect_all`s the operator's real `playwright` and `context7` servers, which are present
on dev0, on one event loop. Each test then runs on its own function loop. When a connected
server sends a request (a `ping`), `_enqueue_outbound` creates the bounded queue on one loop
and the `_drain_outbound` writer task on another, and the task dies with "bound to a
different event loop". Nobody awaits it. Python logs it whenever the garbage collector frees
the task, inside whichever later test happens to be capturing WARNINGs at that moment. In
collection order, `test_project_source_consent_config.py` is the next file after
`test_progressive_disclosure.py` (`prog` < `proj`), and its first test asserts that no
warning was logged at all.

- **The patch neither creates nor frees the task.** The new module creates no
  `ClientManager` that connects anything. Its one real `ClientManager()` only calls
  `register_lazy_configs`, it uses no module-scoped or async fixtures, and its
  `asyncio.run` calls use mocked client managers. The attribution shows the leak on main.
- **Why main's runs were clean.** The patch adds 146 to 212 tests that run earlier
  (`test_catalog_overlay_discovery.py`), which moves the points at which the garbage
  collector runs. Whether the dead task is freed during the one test that forbids warnings
  depends on those points.
- **Minimal order on main:** with `gcatcall.py` (appendix), a plugin that runs `gc.collect()` inside each test's call phase so that the collection lands in a known test, `pytest tests/test_progressive_disclosure.py tests/test_project_source_consent_config.py::test_an_absent_project_mcp_json_warns_about_nothing -p gcatcall` **fails on main `bb6ddde`** with exactly the rev-4 assertion (`Left contains one more item: "Task exception was never retrieved … _drain_outbound … bound to a different event loop"`). Without the plugin the same two-test order passes, and the orphaned task's exception is printed at interpreter exit. The target test alone, with the plugin, passes (1 passed). The leak needs the operator's real `playwright`/`context7` servers, which the module fixture connects; on a host without them those tests skip

This is a latent ordering flake in main's test suite, to be filed separately. The plan does
not change `test_progressive_disclosure.py`.

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

**Every cross-server statistic is base-only (rev 4).** The rule is not "keyword weights";
it is every statistic in the derived table above. `_build_manifest` computes, from the base
servers before any overlay:
- `keyword_weights` (rev 1);
- `category_keyword_index`: for each `_CATEGORY_MAP` category, each mapped base server's
  normalised keywords.

`get_servers_in_category` takes both the span (keyword → categories) and the per-category
score from that index, so an overlay, additive or replacing, never moves which category
wins or a keyword's span. The category tier then lists the mapped names present in the
merged manifest, so a replaced server is listed with its overlay definition, as before. A
hand-built `Manifest` (`base_category_keywords is None`) is scored from its own servers, as
on main.

`request_capability`'s normalised name index is the third statistic: base names win a
normalised tie.

**An overlay CLI never pre-empts the server tiers (rev 5, claude F001).**
`request_capability` resolves in tier order: name, then CLI, then category. An available
overlay CLI, whether new or overriding a shipped one, whose keywords cover a category
answered before the category tier and hid every server in it. Now the CLI tier skips a CLI
in `Manifest.overlay_cli_names` whenever the category tier would find servers. The check
reads the query the same way, through `_names_an_unknown_service`, the shared PascalCase
pre-check. An overlay CLI still answers when no server tier does (tested), and a shipped CLI
keeps main's precedence (tested with `git commits`). The differential now includes three
overlay-CLI documents: a new CLI, an override of `git`, and a CLI declaring the whole
category vocabulary. Each is marked available, against every category-vocabulary query.

*Superseded by rev 6: the rev-5 gate covered only the category tier.*

**An overlay CLI ranks below every server tier (rev 6, B11).** Rev 5 suppressed an overlay
CLI only when the category tier would find servers, so an overlay CLI named like a shipped
server still won the name tier (`name_match_collides_with_cli`). Rev 6 uses the derived
tier table above:
- `cli_hint_match` holds **shipped** CLIs only, so the name-tier collision and the
  shipped-CLI tier keep main's behaviour exactly;
- `overlay_cli_match` answers only after the name, category and configured-server tiers;
- `_keyword_match` (`match_capability`) considers overlay CLIs only when nothing else
  reaches its threshold;
- `catalog_search` never combines the two.

The differential `test_no_overlay_cli_outranks_a_server_or_a_shipped_cli_anywhere` builds
one overlay CLI per discovery term. That is every shipped server name (with the CLI named
like the server), every category word and name, and every shipped keyword
(606 terms), all available at once. For every term it asserts:
- `request_capability` keeps every server candidate, and keeps every shipped-CLI answer;
- `catalog_search`'s manifest candidates are unchanged;
- `match_capability` keeps its server or shipped-CLI match.

It also asserts that an overlay CLI still answers where nothing else did (not vacuous).


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
`_parse_overlay_document`. Rev 4 adds the two iterating consumers the derivation found
(last two server rows): a field is checked against **every** consumer that reads it,
whatever conversion path the entry takes:

| entry | check (the consumer's own code) | consumer |
|---|---|---|
| server | `normalized_server_name(server.name)` — the function the handlers now share | `catalog_search` name match, `request_capability` name tier |
| server | `matcher._keyword_match_score("probe", server.keywords)` | every keyword scorer (catalog, match_capability; the category tier normalises the same way) |
| server | `requires_credential(server)`, and `os.environ.get(key)` for each `credential_lookup_keys(server)` key | every candidate's credential metadata (`_get_server_env_metadata`, `_auth_env_options`) |
| server | `CapabilityCandidate(**manifest_candidate_fields(server), …)` — the function the handler builds from | `catalog_search` |
| server | `config.loader._manifest_server_to_config(server, lambda _: None)` — the same function | startup, `gateway.refresh`, provisioning, lazy connects |
| server (rev 4) | `config.loader._merge_manifest_defaults(name, LocalMcpServerConfig(command="", args=[]), {name: server})`, result validated as `LocalMcpServerConfig` (`model_dump(warnings=False)`) — the same function, for **every** entry, remote or local | `load_configs`' configured-default inheritance, which reads `command`, `args`, `extra_env` and the credential keys whatever the transport |
| server (rev 5) | the remote auth view, `RemoteMcpServerConfig(url=<url or placeholder>, headers, the five metadata URLs, declared_scopes, supports_url_elicitation)`, for **every** entry; its validated values are kept | `pmcp secrets` (`manifest_secret_metadata`, which reads exactly these) and `_auth_metadata_for_server`. Rev 4 called `manifest_secret_metadata` itself; with the auth view that call can no longer fail, and its mutant survived, so it was removed |
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
- **Rev 4, per field and not per path.** A remote entry is now checked against the
  inheritance path too, so `url` + `args: 5` or `command: 5` is skipped (codex F051). Values
  every consumer iterates harmlessly still load: `url` + `args: "x"` (iterated as
  characters, as on main) and `url` + `extra_env: {A: 1}` (coerced by the parser). So does
  an int inside an `install` argv, read only by that server's own provisioning. A
  `headers: 5` with no `url` is skipped, because `pmcp secrets` reads `headers` on every
  entry. Five must-load / must-skip rows pin these (`test_a_field_is_checked_against_every_consumer_that_reads_it`).
- **Rev 5: parse, don't validate (codex F001).** Rev 4 built each consumer's model, threw
  the result away, and kept the raw YAML value, which a consumer could read unconverted.
  Rev 5 takes two steps:
  1. **The entry must be JSON-native** (`_schema_fields`): str, int, finite float,
     bool, null, lists, and mappings with string keys, which is the value space of a
     `.mcp.json` entry. YAML-only types (`!!binary` bytes, timestamps, `!!set`, `!!omap`
     pairs, `.inf`/`.nan`) reject the entry with a value-free reason. `!!python/*` tags
     already fail `safe_load`, so the whole document is refused with a value-free
     diagnostic (D9).
  2. **The kept entry is the canonical one** (`_canonical_server`, `_canonical_cli`). Each
     consumer model is built, and its validated values replace the raw ones:
     - `description`, `url`, `package`, `server_card_url`, `declared_*`, `env_var` and
       `env_instructions` come from the `CapabilityCandidate`;
     - `headers`, the five metadata URLs and `supports_url_elicitation` come from the
       remote auth view;
     - `command` and `args` come from inheritance's validated `LocalMcpServerConfig`;
     - the CLI fields come from the `CLIHint`.

  So `supports_url_elicitation: "false"` is kept as `False`, not as a truthy string, and a
  remote entry's `args: "ab"` is kept as inheritance converts it (`["a", "b"]`). The real
  startup conversion runs on the entry's own `command`/`args` *before* inheritance, so a
  local `args: "x"` is still refused, as startup would refuse it. Every shipped server and
  CLI is already canonical (tested: `_canonical_server(s) == s`).
- **Rev 5: the auth view for every entry (claude N1).** `pmcp secrets` and
  `_auth_metadata_for_server` read the metadata URLs, `headers`, `declared_scopes` and
  `supports_url_elicitation` whatever the transport. So the entry is built into a
  `RemoteMcpServerConfig` (with a placeholder URL when it has none), and its values are
  kept. A local `oidc_issuer_url: 5` or `supports_url_elicitation: maybe` is skipped.
  `"true"` is kept as `True`.
- **Rev 6: only the schema fields are checked (B12).** Rev 5 required the *whole* entry to
  be JSON-native. So an unread key the operator added (`added: 2026-10-04`, a YAML date)
  dropped the entry, which main loads, and the rejection message named that free-text
  key. Rev 6:
  - **checks only `_SERVER_SCHEMA_KEYS` / `_CLI_SCHEMA_KEYS`**, exactly the keys
    `_parse_server_config` and `_parse_cli_alternative` read. A test derives the sets from
    the parsers' own source, every `data.get("<key>")`, and asserts equality, so a new
    field cannot be missed;
  - **ignores unknown keys**, as main does;
  - **drops to the default a stored-only field** (`discovery_metadata`,
    `discovery_diagnostics`, `status`, `source`, `replacement`) holding a non-JSON value,
    and keeps the entry. These are parsed and stored but read by no consumer; the only
    reader of the last three, `sync_registry_to_manifest`, has no in-tree caller;
  - **rejects the entry** when a consumed field holds a non-JSON value. The message is
    `'<schema field>' holds a value that is not JSON`, which names no overlay key or
    value.

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
| G8–G11 | `request_capability`: name index, name-match candidate, category keywords, category candidates | G8–G11 |
| F3 | `load_configs`: configured-default inheritance for one `.mcp.json` entry. On failure, that entry loads without manifest defaults, exactly as when the manifest is unavailable: a partial entry is skipped and a complete one loads as written. Siblings always load | F3 (rev 4) |
| F4 | `pmcp secrets`: each manifest server's auth metadata | F4 (rev 4) |

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
- manifest loading and overlay parsing, **including a document that fails to parse**
  (rev 5);
- the four sites that catch a `load_manifest()` failure and log it: gateway startup,
  `gateway.refresh`, `load_configs`' defaults, and `pmcp secrets` (which logs nothing)
  (rev 5);
- discovery: `catalog_search`, `request_capability`, `match_capability`, `rank_cli_hints`;
- CLI probing: `probe_clis`, `check_cli`, `get_cli_help`;
- startup and refresh resolution: the shared skip lines;
- lazy registration (`Registered lazy server`);
- the guards (D7).

**Out of scope, said so.** Exactly these, measured or found by the round-3 board and the
consumer derivation:
- **Lifecycle lines, from the first start *attempt*.** A client can trigger a lazy start of
  any server by naming it, for example `gateway.describe` with `<overlay-name>::x`, before
  anything has connected. The client manager then logs the server's key in
  `Triggering lazy-start`, `Lazy-starting server`, `Connecting to MCP server` and
  `Failed to lazy-start <name>: …`. That exception text can carry the entry's `command` or
  `url`. The provisioning flow behaves the same way. It is the same as for `.mcp.json`
  servers, and naming the server is how an operator follows a start.
- **`pmcp refresh`** (`manifest/refresher.py`, the descriptions refresher, not
  `gateway.refresh`) logs each server's name and its error text.
- **Third-party DEBUG output.** `sse_starlette`'s `chunk:` lines at `--debug` echo whole
  tool responses, including candidate names and descriptions. That is the response body,
  which the client receives anyway.
- **`.mcp.json` entries** are the operator's own file. Their names are logged as before,
  for example `Configured server '<name>': ignoring its manifest defaults (…)` (rev 4).

**How:**
- **Every exception from the YAML load (rev 6, B13).** The overlay parse catches every
  exception, not only `YAMLError`. With PyYAML 6.0.3 a constructor raises `ValueError`
  (`!!int x`, `!!float x`), `KeyError` (`!!bool x`; its text is the value, lowercased) or
  `AttributeError` (`!!timestamp x`), and one escaping the parser stopped the shipped
  manifest from loading. The whole overlay is skipped with `yaml_error_text`: the class,
  plus the line and column when the exception carries a mark. Shipped servers keep
  loading. The sweep `test_an_invalid_tag_is_contained_without_any_value` covers
  `!!int`, `!!float`, `!!bool`, `!!timestamp`, `!!binary`, `!!set`, `!!omap`, `!!pairs`,
  `!!map`, `!!seq`, `!!str` on a mapping, a custom tag, `!!python/*`, and scalar and
  list-of-scalar merge keys. Each carries the sentinel, and each runs as a user, env and
  project overlay (45 cases). The sentinel never appears, case-insensitively, in any
  record at DEBUG. `!!binary` decodes leniently, so it reaches the entry check and is
  skipped there.
- **YAML and parse errors (rev 5).** `grep -rnE "YAMLError|safe_load|load_yaml|yaml\.load|_parse_trusted_(yaml|document)\(|MarkedYAMLError" src/pmcp`
  (`derive5.txt`) finds every YAML parse site. These are the overlay or manifest input
  sites:
  - the overlay parse (`_parse_overlay_document`): it now catches every exception (rev 6)
    and logs the path and `yaml_error_text(exc)`, which is the class plus line and
    column when known;
  - the base or explicit-path parse (`_build_manifest`): it raises to its caller, and the
    callers that log (`server.py` startup, `gateway.refresh`, `load_configs`) now log only
    the class;
  - `_shipped_manifest_entries` and `_shipped_cli_names`: these swallow the error and log
    nothing.

  The other YAML sites read pmcp's own files or other configs, not overlay or manifest
  input: `config/guidance.py`, `policy/policy.py`, `manifest/code_patterns_loader.py`,
  `templates/code_snippets_loader.py`, and `manifest/refresher.py`'s descriptions cache.
  They are out of scope, as is `config/loader.py`'s `.mcp.json` parse error (the operator's
  JSON, `Failed to parse config file …: {e}`). The sweep
  `test_a_malformed_overlay_is_reported_without_any_value` generates 14 malformed
  documents from the YAML grammar, with the sentinel in a different position in each:
  - unterminated flow sequences, including the sentinel as the first token;
  - unterminated flow mappings;
  - bad indentation and tab indentation;
  - unclosed double and single quotes;
  - an invalid escape;
  - a bad verbatim tag and a `!!python/object` tag;
  - an undefined alias;
  - the sentinel as an error-token key, and in a trailing comment;
  - a block inside a flow.

  It runs each as a user, env and project overlay (42 cases). PyYAML raises no error for
  duplicate keys (the last one wins), so none is generated. Every case must keep the shipped
  manifest, log a `Skipping unreadable manifest overlay … <Error> at line N, column M`, and
  leave no sentinel in any record at DEBUG.

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
- Mutants L1–L6 each reintroduce one leak, and Y1–Y3 reintroduce an error's text; each
  is red.

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
  - `_canonical_server` and `_canonical_cli` (D4);
  - `_rejection_reason`, `_shipped_cli_names` and `_cli_label` (D9);
  - `_category_keyword_norms` (G10).
- Rev 6: `_SERVER_SCHEMA_KEYS`, `_CLI_SCHEMA_KEYS`, `_STORED_ONLY_KEYS`, `_is_json_native`
  and `_schema_fields` replace rev 5's whole-entry check (D4). The overlay parse catches
  every exception from the YAML load (D9).
- Rev 5: `yaml_error_text` (D9); `_schema_fields`, `_canonical_server` and
  `_canonical_cli` replace the two check functions, and `_parse_overlay_document` keeps
  their canonical result (D4). The `Manifest.overlay_cli_names` field records every CLI an
  overlay added or replaced (D2).
- Rev 4: the `Manifest.base_category_keywords` field and `category_keyword_index()`,
  computed from the base before the overlay loop. `get_servers_in_category` reads both
  the span and the score from it. `_canonical_server` also runs
  configured-default inheritance and `pmcp secrets`' per-server metadata.
- `_parse_overlay_document` runs both checks inside the existing per-entry `try/except`
  and logs the labelled, value-free reason.
- Every overlay-entry log line names the entry by label.

### `src/pmcp/manifest/matcher.py` (modify)
- `_manifest_keyword_weights` returns the base weights.
- `rank_cli_hints`' body moves unchanged into `_rank_one_cli`, which builds its `CLIHint`
  from `cli_hint_fields`. Each CLI is guarded (G4).
- `_keyword_match` guards each server (G7). Rev 6: it considers overlay CLIs only when
  neither a shipped CLI nor a server reaches the threshold (D2).

### `src/pmcp/tools/handlers.py` (modify)
- `_build_manifest_with_config_servers` carries the base weights and, in rev 4, the base
  category statistics (D3).
- Rev 4: `request_capability`'s name index keeps the base name on a normalised tie.
- Rev 6: `cli_hint_match` holds shipped CLIs only, and `overlay_cli_match` answers after
  every server tier; `_use_cli_resolution` is shared by both (D2, which replaces rev 5's
  category-only gate). `_names_an_unknown_service` (rev 5) is the shared PascalCase
  pre-check. `gateway.refresh` logs a
  manifest-load failure by class only (D9).
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
- Rev 4: `load_configs` contains one entry's configured-default inheritance (guard F3).
- Rev 5: `load_configs` logs a manifest-load failure by class only (D9).

### `src/pmcp/cli_commands/secrets.py` (modify, rev 4)
- `manifest_secret_metadata(server)`: the per-server half of `_extract_required_keys`,
  whose fields the overlay check's auth view covers (rev 5). The manifest loop is
  per-server (guard F4), so one
  entry can no longer drop every later server's auth metadata (claude F1).

### `src/pmcp/server.py` (modify)
- Rev 5: startup logs a manifest-load failure by class only (D9).
- The startup skip lines go through `config.loader.startup_skip_message` (D9), and the
  now-unused `StartupSkipReason` import is dropped.

### `src/pmcp/client/manager.py` (modify)
- `Registered lazy server` names a manifest-derived server by its label (`_lazy_log_name`,
  D9).

### `tests/test_catalog_overlay_discovery.py` (new, 271 tests; verbatim below)
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
- **The second line:** one test per guard, G1–G11, F3 and F4, each with a hand-built bad
  entry that bypasses the parse-time check.
- **Rev 6:**
  - both round-5 B11 falsifiers (`airbnb`, `redis`);
  - the all-terms overlay-CLI differential;
  - the gemini-note test;
  - both B12 falsifiers, the stored-only fields, and the schema-key derivation;
  - the 45-case invalid-tag sweep and codex's `_build_manifest` falsifier.
- **Rev 5:**
  - the malformed-YAML sweep (14 grammar-derived documents × user/env/project) and
    codex's F002 falsifier;
  - the catch-site test;
  - the YAML-tag walk (8 tags × every field, local and remote, with partial `.mcp.json`
    entries, merge keys, the full consumer drive, and a JSON-native assertion on every
    kept entry);
  - codex's F001 falsifier;
  - the validated-value, already-canonical, inherited-converted and metadata-field tests;
  - claude's F001 falsifier;
  - the overlay-CLI fallback, shipped-CLI precedence and provenance tests;
  - three overlay-CLI cases in the differential.
- **Rev 4:**
  - grok F001's falsifier;
  - the statistics differential (5 generated overlays × the category vocabulary × three
    entry points);
  - the aliasing-name test;
  - codex F051's falsifier through the real `load_configs`;
  - five per-field rows;
  - a field walk that now runs remote **and** local for every shape, with the real
    `load_configs`, partial `.mcp.json` entries and `pmcp secrets`, and fails on any
    value-bearing Python warning.

### `tests/test_version_pin.py`, `tests/test_manifest_overlay.py` (migrate, +18/−7; verbatim below)
- See *The test surface this touches*.

## Documentation impact

- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed` (`CHANGELOG.md:567` on
  `6edf8a4`, verified to sit under `[Unreleased]`):

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
  > takes its default, instead of dropping the entry. One overlay entry also no longer
  > stops other `.mcp.json` servers from loading when one of them inherits its defaults,
  > or makes `pmcp secrets` drop other servers' auth metadata. An overlay that replaces a
  > shipped server no longer changes how `gateway.request_capability` picks a category
  > for anything else. See
  > [Consiliency/pmcp#342](https://github.com/Consiliency/pmcp/issues/342).

  Never put a closing keyword next to the number.
- `CHANGELOG.md` `### Upgrade notes` (`CHANGELOG.md:10` on `6edf8a4`; "Things 2.7.3 accepted
  that 2.8.0 refuses"), one bullet:

  > - **An overlay entry that part of pmcp would fail on is skipped when the overlay is
  >   read**, with a WARNING naming the field, for example an `args` or `command` that is
  >   not a string, or a `cli_alternatives` entry with an empty `check_command`. Before,
  >   it loaded and then made `catalog_search` fail, or stopped startup and
  >   `gateway.refresh` for every server. An overlay CLI no longer outranks a server in
  >   `gateway.request_capability`. *Fixed*
- `README.md` § "Private manifest overlay", the "fail-soft" paragraph (`README.md:1261` on
  `6edf8a4`).
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
  > server, or replacing a shipped server, never hides another server from
  > `gateway.catalog_search` or `gateway.request_capability` (within the result limit;
  > ties rank by name; a query that names a server resolves to it)."
- No `SECURITY.md` change. Consent semantics are unchanged (D5). D9 strengthens an existing
  property, that overlay keys and values are not logged, and adds no claim the security
  checker would need to track. Run `scripts/check_security_claims.py`.

## Dependencies & order

1. Applies to `6edf8a4` (re-fetched for rev 6). The source patch below is the three-way
   merge onto it, with no conflict, and applies with `git apply`. Consiliency/pmcp#330's
   task-units change sits next to it in `handlers.py` and `server.py` with no overlap. Consiliency/pmcp#298 is already on
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
   - **YAML errors (rev 5).** Consiliency/pmcp#297's plan (Consiliency/pmcp#314) also makes
     YAML errors value-free, through `load_yaml` and `exception_text`. This plan does not
     depend on it: `yaml_error_text` is self-contained. Whichever lands second reconciles.
     If #297 lands first, the overlay parse may call `load_yaml` and keep
     `yaml_error_text`'s output, or #297's equivalent, provided that the sweep
     `test_a_malformed_overlay_is_reported_without_any_value` and Y1 still pass.
   - **Consiliency/pmcp#314 overlap, re-checked for rev 6.** #297's `load_yaml` wraps the
     parse and makes YAML errors value-free through `exception_text`. Rev 6 adds one
     requirement that whichever lands second must keep: the overlay parse boundary
     contains **every** exception from the load, not only `YAMLError` (codex F001, E1). If
     `load_yaml` re-raises constructor errors as its own type, the overlay parse must still
     catch them; the sweep `test_an_invalid_tag_is_contained_without_any_value` and E1
     check it.
2. Within the plan, apply the source patch, the new module and the test migration
   together: the migration fails without the source patch, and the source patch fails the
   three migrated tests without it.
3. Docs last.

## Verification (measured this session on the rev-6 spike; the implementer re-runs each step)

Run from the worktree. Before running:
- A fresh worktree needs `uv sync --all-extras -p 3.10` first.
- On dev0, run every pytest under
  `env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir`.
- `mkdir -p` the basetemp first. Keep it off `/tmp`, outside the checkout, readable all the
  way up, and under no `.git`. This session used `/var/tmp/pmcp-342-bt-viperjuice`.
- Check `df -h /mnt/workspace` before a long run.
- Detach long runs with `setsid nohup … < /dev/null &`, and stop them by PID, never with
  `pkill -f`. Restore a mutated file from the driver's `.saved342` copy, and verify the
  restore by comparing `git diff -- src | sha256sum` with the snapshot's sha256 (done twice
  this session).

```bash
# 1. The new module: red on main and on rev 5, green on rev 6
uv run pytest tests/test_catalog_overlay_discovery.py -q -p no:cacheprovider --no-cov --cov-fail-under=0
#   rev-6 spike: 271 passed
#   same file on main b2884db:     200 failed, 71 passed (the 71 pin main's behaviour: must-load, main-weighting, controls, no-query)
#   same file on the rev-5 patch:  25 failed, 246 passed: exactly round 5's findings (12 invalid-tag cases, 5 stored-only fields, the stored-only and schema-key derivations, the all-terms overlay-CLI differential, and the six round-5 falsifiers)

# 2. Round 5's falsifiers (all are tests in the module), main vs rev 6
#   claude F001 / codex F003  overlay CLI `airbnb` / `redis`: main and rev 5 use_cli, no server | rev 6 the server is a candidate
#   codex F002 / claude F002  unknown key holding .nan / a date / !!binary: rev 5 drops the entry and logs the key | rev 6 loads it, logs no key
#   codex F001                `command: !!int <sentinel>` through _build_manifest: main and rev 5 raise ValueError | rev 6 keeps playwright

# 3. Lint, format, types (CI's own commands)
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
#   -> All checks passed! / already formatted / Success: no issues found in 52 source files

# 4. Touched suites (24 files, named in "The test surface this touches")
#   -> **2489 passed, 1 skipped, 19 deselected, 0 failed** (874.68 s, on b2884db + the same hunks); on 6edf8a4 the full suite (step 8) covers them

# 5. The real gateway, end to end (call.py; R1 table), on b2884db + rev 6 (same hunks)
#   -> unapproved demo→[useronly], screenshot→[playwright]; approved demo→[projonly, useronly], screenshot→[playwright, projonly], gadget→[projonly]; revoked as unapproved; the gateway log names neither overlay server (0 lines)

# 6. R1 class: copycat over all 569 shipped keywords
python copycat.py      # main: 329 of 440 queries lose a candidate, 91 servers; rev 6: 0 of 440, 0 servers

# 7. Mutation driver (59 mutants, appendix). RED requires failed > 0 and errors == 0; -x.
#    Run in a /var/tmp scratch worktree of 31d0f40 + the same hunks; all 59 anchors re-checked on 6edf8a4.
python mutants.py "$PWD"
#   -> 59 of 59 mutants RED (a real test failure and no errors), every restore byte-identical (table below)

# 8. The full suite, once, alone, detached, on 6edf8a4 + the rev-6 patch (CI command minus -v)
setsid nohup env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
  uv run pytest tests/ -q --tb=short --cov --cov-report= -p no:cacheprovider \
  --basetemp=/var/tmp/pmcp-342-bt-viperjuice/full7 > full7.log 2>&1 < /dev/null &
#   -> 9035 passed, 5 skipped, 80 deselected, 0 failed in 1364.34s; coverage 90.57% (6edf8a4 + rev 6, alone, npm vars unset, --cov)

# 9. Gates
python3 scripts/check_security_claims.py
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-342-catalog-overlay-*.md
#   -> OK, 129 cited node ids / blocking inconsistencies: 0

# 10. Embedding proof: extract the source patch, the test module and the migration patch from
#     THIS file; on a clean 6edf8a4 tree: git apply --check, git apply, write the module, run it
#   -> apply-check clean on 6edf8a4; source, module and migration cmp-identical to the spike; the module plus the two migrated modules: 455 passed
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
- [ ] **Rev 4, statistics:** every cross-server statistic in the derived table is base-only.
  The differential (additive, replacing shipped, replacing `_CATEGORY_MAP` names, grok's
  case, and the whole vocabulary) removes no non-overlay server from any category-vocabulary
  query on `catalog_search`, `request_capability` or `match_capability`. The two documented
  exceptions are top-N displacement and resolution precedence. Grok's falsifier keeps all
  9 servers. An aliasing overlay name keeps the shipped match (C1–C3).
- [ ] **Rev 4, fields:** every field is checked against every consumer that reads it, whatever
  the path. Codex's falsifier loads `healthy`. The walk (remote and local, real
  `load_configs`, partial `.mcp.json` entries, `pmcp secrets`) raises nowhere and leaks no
  name or value in a log record or Python warning. `load_configs` and `pmcp secrets`
  contain one entry (F1–F5).
- [ ] **Rev 5, YAML:** no malformed overlay (14 grammar-derived documents × user, env and
  project) puts any of its text in a log record. Each is reported as `<Error> at line N,
  column M` with its path. The `load_manifest()` catch sites log only the class (Y1–Y3).
- [ ] **Rev 5, parse don't validate:** no kept entry holds a non-JSON-native value, under
  every YAML tag on every field. Every kept entry carries its consumers' validated values.
  Codex's `!!binary` falsifier keeps `healthy` through `filter_self_references`. Shipped
  entries are already canonical (P1–P6).
- [ ] **Rev 6, overlay CLIs (supersedes rev 5's):** an overlay CLI ranks below every server
  tier. With one overlay CLI per shipped server name, category word and shipped keyword,
  all available, no term loses a `request_capability` server or a shipped-CLI answer,
  `catalog_search`'s manifest candidates are unchanged, and `match_capability` keeps
  its match. Both round-5 falsifiers pass. An overlay CLI still answers where nothing
  else does (K1–K4).
- [ ] **Rev 6, schema fields:** unknown keys are ignored. Claude's `added: 2026-10-04`
  entry loads with no key in any log. A stored-only field's YAML value falls back to its
  default. A rejection names only a schema field. The schema key sets equal the keys the
  parsers read (J1–J3).
- [ ] **Rev 6, YAML constructors:** every invalid tag (15 kinds × user, env and project)
  is contained at the parse boundary, value-free, with the shipped manifest loaded;
  codex's `!!int` falsifier keeps `playwright` (E1).
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
guard mutants narrow the guard's `except` to `ZeroDivisionError`. Each run stops at the first failure (`-x`; the module takes about 200 s), so a row shows
the failing test and the count up to it. Run on the rev-6 spike text embedded below
(Python 3.10.21).

| id | mutant (the rule it breaks) | result | first failure | failing tests (first 4) |
|---|---|---|---|---|
| M1 | R1: scoring ignores the base weights (counts the merged manifest) | **RED** 1 failed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable |
| M2 | R1: base weights computed after the overlays are merged | **RED** 1 failed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable |
| M3 | R1: request_capability's config-server view drops the base weights | **RED** 1 failed, 8 passed (restored=True) | assert None is not None | test_the_config_server_view_keeps_the_base_weights |
| M4 | instance fix instead of the class: raise the IDF floor so a shared keyword passes | **RED** 1 failed, 7 passed (restored=True) | assert {'api': 0.7} == {'api': 0.5} | test_a_hand_built_manifest_is_still_weighted_by_its_own_servers |
| M5 | R2: overlay servers neither gated nor checked (raw parse kept) | **RED** 1 failed, 10 passed (restored=True) | catalog_search: skipping an unusable manifest entry (an overlay server (name not shown)): | test_every_server_field_with_every_bad_shape_is_contained[description] |
| M7 | R2: the CapabilityCandidate is built without validation | **RED** 1 failed, 10 passed (restored=True) | catalog_search: skipping an unusable manifest entry (an overlay server (name not shown)): | test_every_server_field_with_every_bad_shape_is_contained[description] |
| M8 | R2: check skips the startup/refresh conversion | **RED** 1 failed, 51 passed (restored=True) | assert not ({'args-str', 'command-int', 'headers-int', 'keywords-ints', 'oidc-issuer-int', | test_the_board_repros_are_skipped_with_a_value_free_warning |
| M9 | R2: overlay CLI alternatives neither gated nor checked | **RED** 1 failed, 41 passed (restored=True) | rank_cli_hints: skipping an unusable entry (cli_alternative 'git'): TypeError | test_every_cli_field_with_every_bad_shape_is_contained[keywords] |
| M12 | R2: CLI check accepts an empty check_command (probe needs slot 0) | **RED** 1 failed, 51 passed (restored=True) | assert not ({'cli-check-empty', 'cli-check-int', 'cli-description-int', 'cli-examples-ints | test_the_board_repros_are_skipped_with_a_value_free_warning |
| M13 | R2: a YAML null is a value, not absent | **RED** 1 failed, 53 passed (restored=True) | assert 'npx' == 'my-github-fork' | test_null_fields_take_their_defaults_and_keep_the_entry |
| M14 | R2: the skip reason quotes pydantic's text (values reach the log) | **RED** 1 failed, 10 passed (restored=True) | Skipping invalid server entry (an overlay server (name not shown)) in overlay /var/tmp/pmc | test_every_server_field_with_every_bad_shape_is_contained[description] |
| M15 | R2: the server skip warning names the overlay entry | **RED** 1 failed, 10 passed (restored=True) | Skipping invalid server entry ('aaa-tok-NAME-sentinel-5f1c9a') in overlay /var/tmp/pmcp-34 | test_every_server_field_with_every_bad_shape_is_contained[description] |
| M16 | R2: the CLI skip warning names the overlay entry | **RED** 1 failed, 41 passed (restored=True) | Skipping invalid cli_alternative ('tok-NAME-sentinel-5f1c9a') in overlay /var/tmp/pmcp-342 | test_every_cli_field_with_every_bad_shape_is_contained[keywords] |
| G1 | guard: keyword_weights lets one server's keywords fail the table | **RED** 1 failed, 55 passed (restored=True) | 'NoneType' object is not iterable | test_guard_keyword_weights_skip_an_unusable_server |
| G2 | guard: catalog_search scoring lets one server raise | **RED** 1 failed, 56 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_catalog_scoring_skips_an_unusable_server |
| G3 | guard: catalog_search candidate build lets one server raise | **RED** 1 failed, 57 passed (restored=True) | 1 validation error for CapabilityCandidate | test_guard_catalog_candidate_build_skips_an_unusable_server |
| G4 | guard: rank_cli_hints lets one CLI raise | **RED** 1 failed, 58 passed (restored=True) | 'NoneType' object is not iterable | test_guard_rank_cli_hints_skips_an_unusable_cli |
| G5 | guard: probe_clis lets one command raise | **RED** 1 failed, 59 passed (restored=True) | list index out of range | test_guard_probe_clis_treats_an_unusable_command_as_not_detected |
| G6 | guard: startup/refresh let one manifest server abort resolution | **RED** 1 failed, 60 passed (restored=True) | 1 validation error for LocalMcpServerConfig | test_guard_startup_skips_an_unusable_server |
| G7 | guard: match_capability lets one server raise | **RED** 1 failed, 62 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_match_capability_skips_an_unusable_server |
| G8 | guard: request_capability name tier lets one name raise | **RED** 1 failed, 67 passed (restored=True) | 'int' object has no attribute 'lower' | test_guard_request_capability_name_tier_skips_an_unusable_name |
| G9 | guard: request_capability name match lets its entry raise | **RED** 1 failed, 68 passed (restored=True) | str expected, not int | test_guard_request_capability_name_match_skips_an_unusable_entry |
| G10 | guard: request_capability category keywords let one server raise | **RED** 1 failed, 69 passed (restored=True) | 'NoneType' object is not iterable | test_guard_request_capability_category_keywords_skip_an_unusable_server |
| G11 | guard: request_capability category candidates let one server raise | **RED** 1 failed, 70 passed (restored=True) | 1 validation error for CapabilityCandidate | test_guard_request_capability_category_candidate_skips_an_unusable_server |
| M6 | R2: check skips the name/keyword scoring every discovery path runs | **RED** 1 failed, 11 passed (restored=True) | 'int' object is not iterable | test_every_server_field_with_every_bad_shape_is_contained[keywords] |
| M10 | R2: check skips the credential lookups every candidate runs | **RED** 1 failed, 17 passed (restored=True) | catalog_search: skipping an unusable manifest entry (an overlay server (name not shown)): | test_every_server_field_with_every_bad_shape_is_contained[secret_key] |
| M11 | R2: CLI check skips the _rank_one_cli scoring and CLIHint build | **RED** 1 failed, 41 passed (restored=True) | rank_cli_hints: skipping an unusable entry (cli_alternative 'git'): TypeError | test_every_cli_field_with_every_bad_shape_is_contained[keywords] |
| M17 | R1: keyword counting de-duplicates after normalising (not main's) | **RED** 1 failed, 92 passed (restored=True) | assert {'db': 0.5, '...ql': 1.0, ...} == {'db': 0.5, '...ql': 0.5, ...} | test_weights_equal_mains_for_hand_built_and_explicit_path_manifests[6] |
| L1 | logs: the self-relaxing credential warning echoes the variable | **RED** 1 failed, 127 passed (restored=True) |  | test_a_self_relaxing_credential_is_refused_without_its_value |
| L2 | logs: probe_clis names each detected CLI | **RED** 1 failed, 128 passed (restored=True) |  | test_probe_clis_never_logs_an_overlay_cli_name |
| L3 | logs: probe_clis' summary names the detected CLIs | **RED** 1 failed, 128 passed (restored=True) |  | test_probe_clis_never_logs_an_overlay_cli_name |
| L4 | logs: lazy registration names an overlay server | **RED** 1 failed, 129 passed (restored=True) | assert ['Registered ...EL-name-77ab'] == [] | test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| L5 | logs: startup/refresh skip lines name an overlay server | **RED** 1 failed, 129 passed (restored=True) |  | test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| L6 | logs: startup/refresh skip lines print an overlay env_var | **RED** 1 failed, 129 passed (restored=True) | assert ['Skipping st...ager startup'] == [] | test_no_overlay_name_or_value_reaches_any_log_on_these_paths |
| C1 | stats: the category tier counts the merged manifest | **RED** 1 failed, 130 passed (restored=True) | assert ('not_available', []) == ('pick_from_c...ecrawl', ...]) | test_a_replaced_category_server_does_not_empty_another_category |
| C2 | stats: the config-server view drops the base category statistics | **RED** 1 failed, 8 passed (restored=True) | assert None is not None | test_the_config_server_view_keeps_the_base_weights |
| C3 | stats: the name index lets an aliasing overlay name take a base slot | **RED** 1 failed, 139 passed (restored=True) | assert ['play_wright'] == ['playwright'] | test_an_overlay_name_aliasing_a_shipped_name_keeps_the_shipped_match |
| F1 | fields: check skips configured-default inheritance | **RED** 1 failed, 13 passed (restored=True) | 'int' object has no attribute 'lower' | test_every_server_field_with_every_bad_shape_is_contained[command] |
| F2 | fields: the auth view skips `headers` (pmcp secrets reads them on every entry) | **RED** 1 failed, 51 passed (restored=True) | assert not ({'args-str', 'command-int', 'headers-int', 'keywords-ints', 'oidc-issuer-int', | test_the_board_repros_are_skipped_with_a_value_free_warning |
| F3 | guard: load_configs lets one entry's inheritance abort every config | **RED** 1 failed, 141 passed (restored=True) | Value after * must be an iterable, not int | test_guard_configured_default_inheritance_contains_one_entry |
| F4 | guard: pmcp secrets lets one entry drop every later server | **RED** 1 failed, 142 passed (restored=True) | 'int' object has no attribute 'values' | test_guard_secrets_contains_one_entry |
| F5 | logs: the inheritance check lets pydantic's warning quote a value | **RED** 1 failed, 13 passed (restored=True) |  | test_every_server_field_with_every_bad_shape_is_contained[command] |
| Y1 | yaml: the overlay parse warning quotes the YAML error (values reach the log) | **RED** 1 failed, 148 passed (restored=True) |  | test_a_malformed_overlay_is_reported_without_any_value[user-bad-indentation] |
| Y2 | yaml: gateway.refresh logs a manifest-load error's text | **RED** 1 failed, 191 passed (restored=True) |  | test_a_load_failure_is_logged_by_class_on_every_catch_site |
| Y3 | yaml: load_configs logs a manifest-load error's text | **RED** 1 failed, 191 passed (restored=True) | assert not ['Manifest defaults unavailable during config load: bad token sk-yaml-SENTINEL- | test_a_load_failure_is_logged_by_class_on_every_catch_site |
| P1 | parse: the kept server is the raw parse, not the canonical one | **RED** 1 failed, 201 passed (restored=True) | assert 'false' is False | test_consumers_read_the_validated_value_not_the_raw_one |
| P2 | parse: server schema fields are not required to be JSON-native | **RED** 1 failed, 192 passed (restored=True) | zz-bad-local-requires-api-key | test_no_consumer_ever_sees_a_non_json_value[binary] |
| P3 | parse: CLI schema fields are not required to be JSON-native | **RED** 1 failed, 198 passed (restored=True) | badcli-keywords | test_no_consumer_ever_sees_a_non_json_value[set] |
| P4 | parse: the auth view's validated value is not kept | **RED** 1 failed, 201 passed (restored=True) | assert 'false' is False | test_consumers_read_the_validated_value_not_the_raw_one |
| P5 | parse: a local entry's metadata URLs are not checked (claude N1) | **RED** 1 failed, 10 passed (restored=True) | assert 'zz-good-remote' in {'github-remote': {'protected_resource_metadata_url': 'https:// | test_every_server_field_with_every_bad_shape_is_contained[description] |
| P6 | parse: inheritance's converted command/args are not kept | **RED** 1 failed, 210 passed (restored=True) | assert 'ab' == ['a', 'b'] | test_inherited_fields_are_kept_converted |
| K1 | cli: an overlay CLI takes the shipped-CLI tier (outranks the name and category tiers) | **RED** 1 failed, 132 passed (restored=True) | assert [('APIs', 're...', ...]), ...] == [] | test_no_overlay_removes_a_non_overlay_server_from_any_discovery_entry_point[cli-everything] |
| K2 | cli: overlay CLIs are not recorded as overlay CLIs | **RED** 1 failed, 132 passed (restored=True) | assert [('APIs', 're...', ...]), ...] == [] | test_no_overlay_removes_a_non_overlay_server_from_any_discovery_entry_point[cli-everything] |
| K3 | cli: the overlay-CLI fallback after every server tier is removed | **RED** 1 failed, 208 passed (restored=True) | assert 'not_available' == 'use_cli' | test_an_overlay_cli_still_answers_when_no_server_tier_does |
| K4 | cli: match_capability lets an overlay CLI outrank a server | **RED** 1 failed, 214 passed (restored=True) | assert [('CMS & cont...eroku')), ...] == [] | test_no_overlay_cli_outranks_a_server_or_a_shipped_cli_anywhere |
| J1 | schema: unknown keys are checked (main ignores them) | **RED** 1 failed, 216 passed (restored=True) | assert 'mytool' in {'playwright': ServerConfig(name='playwright', description='Browser aut | test_claude_f002_falsifier_unknown_keys_neither_drop_nor_log |
| J2 | schema: a rejection quotes the value | **RED** 1 failed, 217 passed (restored=True) |  | test_codex_f002_falsifier_a_rejection_names_no_overlay_key |
| J3 | schema: a stored-only field's YAML value costs the entry | **RED** 1 failed, 219 passed (restored=True) | assert 'odd' in {'playwright': ServerConfig(name='playwright', description='Browser automa | test_a_stored_only_field_with_a_yaml_value_keeps_the_entry[discovery_diagnostics] |
| E1 | yaml: only YAMLError is contained at the overlay parse boundary | **RED** 1 failed, 226 passed (restored=True) | 'sk-tag-sentinel-61d' | test_an_invalid_tag_is_contained_without_any_value[user-bool] |

**59 of 59 mutants RED (a real test failure and no errors), every restore byte-identical.**

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
- **`tests/runtime/test_hang_diagnostics.py` and the basetemp.** In rev 3 its two async-hang
  tests failed only because `--basetemp` sat under the unreadable `/mnt/workspace/users`.
  Rev 4's full run uses `/var/tmp/pmcp-342-bt-viperjuice`, and both pass in it.
- **The rev-4 full-suite failure** is no longer unverified: it is attributed to a
  pre-existing orphaned task from `tests/test_progressive_disclosure.py` (see *The rev-4
  full-suite failure, pinned down*). Whether it surfaces depends on when the garbage
  collector runs.

## Execution Policy

- execute: effort=medium.
- reason: eight source files, about 1272 (+1020/−252) changed lines, on the discovery, startup and
  refresh paths. There is one behaviour change an operator can see: an overlay entry with a
  wrong type is now skipped instead of loading and failing later. Its warning names the
  field.
- Apply the source patch, the new module and the test migration verbatim. Re-run
  Verification 1–4, 7 and 8 against the real tree, and measure main in the same window
  before claiming any number.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below as `342-src.patch` and `git apply` it on `6edf8a4`.
2. Write the test module below to `tests/test_catalog_overlay_discovery.py`.
3. `git apply` the test-migration patch below.
4. Add the CHANGELOG and README text by hand, as in *Documentation impact*.

### Patch — source (`src/pmcp/manifest/{loader,matcher,environment}.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/config/loader.py`, `src/pmcp/cli_commands/secrets.py`, `src/pmcp/server.py`, `src/pmcp/client/manager.py`)

````diff
diff --git a/src/pmcp/cli_commands/secrets.py b/src/pmcp/cli_commands/secrets.py
index 5b8ee60..3ee20df 100644
--- a/src/pmcp/cli_commands/secrets.py
+++ b/src/pmcp/cli_commands/secrets.py
@@ -5,6 +5,7 @@ from __future__ import annotations
 import argparse
 import re
 from pathlib import Path
+from typing import Any
 
 from pmcp.config.loader import load_configs
 from pmcp.env_store import (
@@ -35,6 +36,29 @@ def _mask(value: str) -> str:
     return "*" * min(8, len(value))
 
 
+def manifest_secret_metadata(server: Any) -> tuple[dict[str, object], set[str]]:
+    """A manifest server's auth metadata and remote-header env keys.
+
+    The per-server half of ``_extract_required_keys``, shared with the overlay
+    check so an entry it would fail on is skipped at parse time
+    (Consiliency/pmcp#342 rev 4).
+    """
+    metadata: dict[str, object] = {
+        key: value
+        for key, value in {
+            "protected_resource_metadata_url": server.protected_resource_metadata_url,
+            "authorization_server_metadata_url": server.authorization_server_metadata_url,
+            "oidc_issuer_url": server.oidc_issuer_url,
+            "oidc_discovery_url": server.oidc_discovery_url,
+            "client_id_metadata_document_url": server.client_id_metadata_document_url,
+            "declared_scopes": server.declared_scopes,
+            "supports_url_elicitation": server.supports_url_elicitation,
+        }.items()
+        if value
+    }
+    return metadata, set(collect_remote_header_env_vars(server.headers))
+
+
 def _extract_required_keys(
     project_root: Path,
 ) -> tuple[
@@ -139,29 +163,21 @@ def _extract_required_keys(
             per_server[cfg.name] = server_keys
 
     try:
-        manifest = load_manifest()
-        for server in manifest.servers.values():
-            manifest_metadata: dict[str, object] = {
-                key: value
-                for key, value in {
-                    "protected_resource_metadata_url": server.protected_resource_metadata_url,
-                    "authorization_server_metadata_url": server.authorization_server_metadata_url,
-                    "oidc_issuer_url": server.oidc_issuer_url,
-                    "oidc_discovery_url": server.oidc_discovery_url,
-                    "client_id_metadata_document_url": server.client_id_metadata_document_url,
-                    "declared_scopes": server.declared_scopes,
-                    "supports_url_elicitation": server.supports_url_elicitation,
-                }.items()
-                if value
-            }
-            if manifest_metadata:
-                auth_metadata_by_server.setdefault(server.name, manifest_metadata)
-            server_keys = set(collect_remote_header_env_vars(server.headers))
-            if server_keys:
-                per_server.setdefault(server.name, set()).update(server_keys)
-                all_keys.update(server_keys)
+        manifest_servers = list(load_manifest().servers.values())
     except Exception:
-        pass
+        manifest_servers = []
+    for server in manifest_servers:
+        # Per server: one entry must not drop every later server's metadata
+        # (Consiliency/pmcp#342 rev 4; the overlay check normally skips it first).
+        try:
+            manifest_metadata, header_keys = manifest_secret_metadata(server)
+        except Exception:
+            continue
+        if manifest_metadata:
+            auth_metadata_by_server.setdefault(server.name, manifest_metadata)
+        if header_keys:
+            per_server.setdefault(server.name, set()).update(header_keys)
+            all_keys.update(header_keys)
 
     server_required = {
         server_name: sorted(keys) for server_name, keys in per_server.items()
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index 192c2d8..2fe4fe5 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -73,6 +73,21 @@ except ImportError:
 
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
@@ -1576,7 +1591,7 @@ class ClientManager:
                 status=ServerStatusEnum.LAZY,
                 tool_count=0,
             )
-            logger.info(f"Registered lazy server: {name}")
+            logger.info(f"Registered lazy server: {_lazy_log_name(config)}")
 
     def prune_lazy_configs(self, keep_names: set[str]) -> None:
         """Drop on-demand (lazy) configs whose name is not in ``keep_names``.
diff --git a/src/pmcp/config/loader.py b/src/pmcp/config/loader.py
index c71e7f8..4337924 100644
--- a/src/pmcp/config/loader.py
+++ b/src/pmcp/config/loader.py
@@ -1148,7 +1148,11 @@ def load_configs(
 
         manifest_servers = load_manifest().servers
     except Exception as e:
-        logger.debug(f"Manifest defaults unavailable during config load: {e}")
+        # Class only: an error's text can quote overlay input
+        # (Consiliency/pmcp#342 rev 5).
+        logger.debug(
+            f"Manifest defaults unavailable during config load: {type(e).__name__}"
+        )
 
     def build_resolved_config(
         name: str,
@@ -1160,7 +1164,21 @@ def load_configs(
             resolved_config: McpServerConfig = config
         else:
             normalized = normalize_server_config(config, base_path)
-            local_merged = _merge_manifest_defaults(name, normalized, manifest_servers)
+            # One manifest entry must never abort loading every other config
+            # (Consiliency/pmcp#342 rev 4). If inheriting its defaults fails,
+            # this configured entry loads without them, exactly as when the
+            # manifest is unavailable; the overlay check normally skips such an
+            # entry long before this point.
+            try:
+                local_merged = _merge_manifest_defaults(
+                    name, normalized, manifest_servers
+                )
+            except Exception as exc:
+                logger.warning(
+                    f"Configured server '{name}': ignoring its manifest defaults "
+                    f"({type(exc).__name__})"
+                )
+                local_merged = _merge_manifest_defaults(name, normalized, None)
             if not local_merged:
                 return None
             resolved_config = local_merged
@@ -1349,6 +1367,37 @@ def is_legacy_manifest_auto_start_enabled(
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
@@ -1627,11 +1676,22 @@ def resolve_startup_configs(
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
index e8a36ac..18629b6 100644
--- a/src/pmcp/manifest/environment.py
+++ b/src/pmcp/manifest/environment.py
@@ -59,6 +59,13 @@ def detect_platform() -> Platform:
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
@@ -85,10 +92,10 @@ async def check_cli(name: str, check_command: list[str]) -> CLIInfo | None:
             return CLIInfo(name=name, path=path)
 
     except asyncio.TimeoutError:
-        logger.debug(f"Timeout checking CLI: {name}")
+        logger.debug(f"Timeout checking CLI: {_label(name)}")
         return CLIInfo(name=name, path=path)
     except Exception as e:
-        logger.debug(f"Error checking CLI {name}: {e}")
+        logger.debug(f"Error checking CLI {_label(name)}: {type(e).__name__}")
         return None
 
 
@@ -109,10 +116,10 @@ async def get_cli_help(
         return "\n".join(lines)
 
     except asyncio.TimeoutError:
-        logger.debug(f"Timeout getting help for: {name}")
+        logger.debug(f"Timeout getting help for: {_label(name)}")
         return None
     except Exception as e:
-        logger.debug(f"Error getting help for {name}: {e}")
+        logger.debug(f"Error getting help for {_label(name)}: {type(e).__name__}")
         return None
 
 
@@ -121,8 +128,17 @@ async def probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
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
@@ -132,9 +148,11 @@ async def probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
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
index dc1596a..e512ad4 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -91,6 +91,48 @@ def _shipped_manifest_entries() -> dict[str, dict[str, Any]]:
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
+def yaml_error_text(exc: BaseException) -> str:
+    """A YAML error as text with no value from the document in it.
+
+    PyYAML's message quotes the offending source line and token (a value an
+    operator may have pasted, a tag, an escape), so only the error class and
+    the 1-based line and column of its mark are kept (Consiliency/pmcp#342
+    rev 5). Used for every YAML error from overlay or manifest input.
+    """
+    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
+    where = ""
+    line = getattr(mark, "line", None)
+    column = getattr(mark, "column", None)
+    if isinstance(line, int) and isinstance(column, int):
+        where = f" at line {line + 1}, column {column + 1}"
+    return f"{type(exc).__name__}{where}"
+
+
 def _server_label(name: object) -> str:
     """How a version-pin log line names a server (Consiliency/pmcp#294 piece 1).
 
@@ -415,6 +457,44 @@ def requires_credential(
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
+def category_keyword_index(
+    servers: Mapping[str, ServerConfig],
+) -> dict[str, list[list[str]]]:
+    """For each ``_CATEGORY_MAP`` category, each mapped server's keyword norms.
+
+    The one input of the category tier's statistics (span and score). Built
+    from the base manifest by ``_build_manifest`` (Consiliency/pmcp#342 rev 4).
+    """
+    index: dict[str, list[list[str]]] = {}
+    for cat_name, server_names in _CATEGORY_MAP.items():
+        per_server: list[list[str]] = []
+        for sname in server_names:
+            server = servers.get(sname)
+            if server is not None:
+                per_server.append(_category_keyword_norms(server))
+        index[cat_name] = per_server
+    return index
+
+
 # Category taxonomy used by Manifest.get_category_summary() and get_servers_in_category()
 _CATEGORY_MAP: dict[str, list[str]] = {
     "browser automation": [
@@ -512,6 +592,33 @@ class Manifest:
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
+    # The category tier's statistics, from the BASE manifest only, for the same
+    # reason (Consiliency/pmcp#342 rev 4): for each `_CATEGORY_MAP` category, the
+    # normalized keywords of each mapped base server. Counted over the merged
+    # manifest, an overlay that REPLACED a mapped server with other keywords
+    # moved a keyword's category span (2 -> 3 categories, weight 0.7 -> 0.3) and
+    # emptied `request_capability`'s category tier for other servers. ``None``
+    # for a hand-built Manifest, which is scored from its own servers.
+    base_category_keywords: dict[str, list[list[str]]] | None = field(
+        default=None, compare=False, repr=False
+    )
+    # CLI alternatives an overlay added or replaced (rev 5). An overlay CLI
+    # answers `request_capability` only when no server tier would: it may add a
+    # resolution, never hide the servers a query already finds.
+    overlay_cli_names: frozenset[str] = field(
+        default=frozenset(), compare=False, repr=False
+    )
 
     def get_auto_start_servers(self) -> list[ServerConfig]:
         """Get servers configured for auto-start."""
@@ -566,14 +673,18 @@ class Manifest:
         # Build keyword → set-of-categories map for IDF discounting.
         # A keyword that appears in servers across many different categories is
         # considered generic; one confined to a single category is specific.
+        # Every statistic here comes from the base manifest when there is one
+        # (Consiliency/pmcp#342 rev 4), so an overlay never moves a category's
+        # score or a keyword's span.
+        category_keywords = (
+            self.base_category_keywords
+            if self.base_category_keywords is not None
+            else category_keyword_index(self.servers)
+        )
         kw_cats: dict[str, set[str]] = {}
-        for cat_name, server_names in _CATEGORY_MAP.items():
-            for sname in server_names:
-                server = self.servers.get(sname)
-                if not server:
-                    continue
-                for kw in server.keywords:
-                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
+        for cat_name, per_server in category_keywords.items():
+            for norms in per_server:
+                for kw_norm in norms:
                     kw_cats.setdefault(kw_norm, set()).add(cat_name)
 
         def _kw_weight(kw_norm: str) -> float:
@@ -597,12 +708,8 @@ class Manifest:
             score += len(cat_words & query_words) * 2.0
 
             # Score: keyword hits across servers in this category, category-span weighted
-            for sname in server_names:
-                server = self.servers.get(sname)
-                if not server:
-                    continue
-                for kw in server.keywords:
-                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
+            for norms in category_keywords.get(cat_name, []):
+                for kw_norm in norms:
                     if set(kw_norm.split()).issubset(query_words):
                         score += _kw_weight(kw_norm)
 
@@ -642,8 +749,382 @@ class Manifest:
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
+    overlay check (``_canonical_server``) builds one from it too, so
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
+# The keys an overlay entry is read for: exactly the keys `_parse_server_config`
+# and `_parse_cli_alternative` read (a test derives them from those functions'
+# source and checks these sets). Anything else is ignored, as on main.
+_SERVER_SCHEMA_KEYS = frozenset(
+    {
+        "description",
+        "keywords",
+        "install",
+        "command",
+        "args",
+        "requires_api_key",
+        "env_var",
+        "secret_key",
+        "env_instructions",
+        "extra_env",
+        "api_key_optional_when",
+        "auto_start",
+        "transport",
+        "url",
+        "headers",
+        "protected_resource_metadata_url",
+        "authorization_server_metadata_url",
+        "oidc_issuer_url",
+        "oidc_discovery_url",
+        "client_id_metadata_document_url",
+        "declared_scopes",
+        "supports_url_elicitation",
+        "package",
+        "server_card_url",
+        "declared_capabilities",
+        "discovery_diagnostics",
+        "discovery_metadata",
+        "status",
+        "source",
+        "replacement",
+        "version",
+    }
+)
+_CLI_SCHEMA_KEYS = frozenset(
+    {
+        "keywords",
+        "check_command",
+        "help_command",
+        "description",
+        "examples",
+        "prefer_mcp_for",
+    }
+)
+# Schema keys pmcp parses and stores but no consumer reads (derived: the plan's
+# consumer table; `sync_registry_to_manifest`, the only reader of
+# status/source/replacement, has no in-tree caller). A non-JSON value here is
+# dropped to the default instead of costing the entry, as main never failed on it.
+_STORED_ONLY_KEYS = frozenset(
+    {"discovery_diagnostics", "discovery_metadata", "status", "source", "replacement"}
+)
+
+
+def _is_json_native(value: Any) -> bool:
+    """Whether ``value`` is JSON-native all the way down.
+
+    str, int, finite float, bool, null, lists, and mappings with string keys --
+    the value space of a ``.mcp.json`` entry. Iterative, not recursive: a deeply
+    aliased overlay (thousands of chained anchors) loads on main. A shared
+    (aliased) node is checked once.
+    """
+    stack: list[Any] = [value]
+    seen: set[int] = set()
+    while stack:
+        node = stack.pop()
+        if node is None or isinstance(node, (str, bool, int)):
+            continue
+        if isinstance(node, float):
+            if node != node or node in (float("inf"), float("-inf")):
+                return False
+            continue
+        if isinstance(node, (list, dict)):
+            if id(node) in seen:
+                continue
+            seen.add(id(node))
+            if isinstance(node, list):
+                stack.extend(node)
+                continue
+            if not all(isinstance(key, str) for key in node):
+                return False
+            stack.extend(node.values())
+            continue
+        return False
+    return True
+
+
+def _schema_fields(data: Any, schema: frozenset[str]) -> dict[str, Any]:
+    """The schema fields of an overlay entry, JSON-native; unknown keys dropped.
+
+    YAML can produce bytes (``!!binary``), datetimes, sets, pairs and
+    non-finite floats; no consumer is written for them, and a ``!!binary``
+    command kept raw broke ``filter_self_references`` for every config
+    (Consiliency/pmcp#342 rev 5). Rev 6: only the keys pmcp reads are looked
+    at -- an unknown key (`added: 2026-10-04`) is ignored, as on main -- and a
+    rejection names the schema field, never a key or value from the overlay.
+    """
+    if not isinstance(data, dict):
+        raise _EntryRejected(f"the entry is a {type(data).__name__}, not a mapping")
+    kept: dict[str, Any] = {}
+    for field_name in sorted(schema):
+        if field_name not in data:
+            continue
+        value = data[field_name]
+        if _is_json_native(value):
+            kept[field_name] = value
+        elif field_name in _STORED_ONLY_KEYS:
+            continue  # stored, never read: the default, and the entry is kept
+        else:
+            raise _EntryRejected(f"'{field_name}' holds a value that is not JSON")
+    return kept
+
+
+_METADATA_PLACEHOLDER_URL = "https://metadata-check.invalid/"
+
+
+def _canonical_server(server: ServerConfig) -> ServerConfig:
+    """``server`` as every consumer will see it: validated, converted, typed.
+
+    Parse, don't validate (Consiliency/pmcp#342 rev 5). Each consumer's own
+    model is built from the entry, and the entry that is KEPT carries that
+    model's validated values, so no consumer ever reads a raw value another
+    consumer only validated in passing. Raises if any consumer would fail:
+
+    * the name and keyword scoring every discovery path runs on every server
+      (``normalized_server_name``, ``matcher._keyword_match_score``);
+    * the credential lookups every candidate runs (``requires_credential``,
+      and an environment read of each ``credential_lookup_keys`` key);
+    * the ``CapabilityCandidate`` that ``catalog_search`` builds, from the same
+      ``manifest_candidate_fields`` the handler uses -> description, url,
+      package, server_card_url, declared_*, env_var, env_instructions;
+    * the remote auth view (``RemoteMcpServerConfig``) for EVERY entry, since
+      ``pmcp secrets`` and ``_auth_metadata_for_server`` read the metadata URLs,
+      ``headers``, ``declared_scopes`` and ``supports_url_elicitation``
+      whatever the transport (rev 5, claude N1) -> those fields;
+    * the startup / refresh / provisioning conversion
+      (``config.loader._manifest_server_to_config``, no env value used);
+    * configured-default inheritance (``config.loader._merge_manifest_defaults``)
+      for a command-less ``.mcp.json`` entry of the same name, validated as the
+      ``LocalMcpServerConfig`` startup consumes -> command, args;
+    * ``pmcp secrets``' per-server read (``manifest_secret_metadata``) needs no
+      call of its own: every field it reads (the metadata URLs, ``headers``,
+      ``declared_scopes``, ``supports_url_elicitation``) is the auth view's
+      validated value above (rev 5; a mutant removing the call survived).
+
+    The consumer list is derived (the plan's consumer table), not chosen.
+    """
+    from pmcp.config.loader import _manifest_server_to_config, _merge_manifest_defaults
+    from pmcp.manifest.matcher import _keyword_match_score
+    from pmcp.types import (
+        CapabilityCandidate,
+        LocalMcpServerConfig,
+        RemoteMcpServerConfig,
+    )
+
+    normalized_server_name(server.name)
+    _keyword_match_score("probe", server.keywords)
+    requires_credential(server)
+    for key in credential_lookup_keys(server):
+        os.environ.get(key)
+    candidate = CapabilityCandidate(
+        name=server.name,
+        candidate_type="server",
+        relevance_score=0.0,
+        env_var=server.env_var,
+        env_instructions=server.env_instructions,
+        **manifest_candidate_fields(server),
+    )
+    auth_view = RemoteMcpServerConfig(
+        url=server.url if isinstance(server.url, str) else _METADATA_PLACEHOLDER_URL,
+        headers=server.headers,
+        protected_resource_metadata_url=server.protected_resource_metadata_url,
+        authorization_server_metadata_url=server.authorization_server_metadata_url,
+        oidc_issuer_url=server.oidc_issuer_url,
+        oidc_discovery_url=server.oidc_discovery_url,
+        client_id_metadata_document_url=server.client_id_metadata_document_url,
+        declared_scopes=server.declared_scopes,
+        supports_url_elicitation=server.supports_url_elicitation,
+    )
+    canonical = replace(
+        server,
+        description=candidate.reasoning,
+        url=candidate.url,
+        package=candidate.package,
+        server_card_url=candidate.server_card_url,
+        declared_scopes=list(candidate.declared_scopes),
+        declared_capabilities=list(candidate.declared_capabilities),
+        env_var=candidate.env_var,
+        env_instructions=candidate.env_instructions,
+        headers=dict(auth_view.headers) if auth_view.headers is not None else None,
+        protected_resource_metadata_url=auth_view.protected_resource_metadata_url,
+        authorization_server_metadata_url=auth_view.authorization_server_metadata_url,
+        oidc_issuer_url=auth_view.oidc_issuer_url,
+        oidc_discovery_url=auth_view.oidc_discovery_url,
+        client_id_metadata_document_url=auth_view.client_id_metadata_document_url,
+        supports_url_elicitation=auth_view.supports_url_elicitation,
+    )
+    # The real conversion first, on the entry's own args/command: inheritance
+    # below would read a `str` args as characters, which startup would not.
+    _manifest_server_to_config(canonical, lambda _key: None)
+    inherited = _merge_manifest_defaults(
+        canonical.name,
+        LocalMcpServerConfig(command="", args=[]),
+        {canonical.name: canonical},
+    )
+    if inherited is not None:
+        # warnings=False: pydantic's serializer warning quotes the value.
+        local = LocalMcpServerConfig.model_validate(
+            inherited.model_dump(warnings=False)
+        )
+        canonical = replace(canonical, command=local.command, args=list(local.args))
+        _manifest_server_to_config(canonical, lambda _key: None)
+    return canonical
+
+
+def _canonical_cli(cli: CLIAlternative) -> CLIAlternative:
+    """``cli`` as its consumers will see it (see ``_canonical_server``).
+
+    ``rank_cli_hints`` scores every CLI on every query and builds a ``CLIHint``
+    through ``_rank_one_cli``; the kept entry carries that hint's validated
+    values. ``probe_clis`` runs ``check_command`` and needs a program in slot 0.
+    """
+    from pmcp.manifest.matcher import _rank_one_cli
+
+    match = _rank_one_cli(
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
+    if match is None:  # pragma: no cover - min_score=-1 always yields a match
+        raise _EntryRejected("the entry could not be scored")
+    hint = match.hint
+    if not hint.check_command:
+        raise _EntryRejected("'check_command' names no program")
+    return replace(
+        cli,
+        description=hint.description,
+        check_command=list(hint.check_command),
+        help_command=list(hint.help_command),
+        examples=list(hint.examples),
+        prefer_mcp_for=list(hint.prefer_mcp_for),
+    )
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
@@ -671,14 +1152,16 @@ def _parse_extra_env(
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
@@ -687,7 +1170,7 @@ def _parse_extra_env(
             parsed[key] = str(value)
         else:
             logger.warning(
-                f"Skipping '{field_label}' key '{key}' for server '{name}': "
+                f"Skipping a '{field_label}' key for {_server_label(name)}: "
                 f"unsupported value type {type(value).__name__}"
             )
     return parsed
@@ -708,7 +1191,7 @@ def _parse_api_key_optional_when(
         return []
     if not isinstance(raw, list):
         logger.warning(
-            f"Ignoring 'api_key_optional_when' for server '{name}': not a list"
+            f"Ignoring 'api_key_optional_when' for {_server_label(name)}: not a list"
         )
         return []
 
@@ -716,13 +1199,14 @@ def _parse_api_key_optional_when(
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
@@ -994,7 +1478,14 @@ def _materialize_version_pin_soft(server: ServerConfig) -> ServerConfig:
 
 
 def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
-    """Parse a server config from raw YAML data."""
+    """Parse a server config from raw YAML data.
+
+    A YAML ``null`` means the field is absent and takes its default, for every
+    field (Consiliency/pmcp#342): a blank ``description:`` must not cost an
+    override its entry. Types are not checked here; an overlay entry is checked
+    against its consumers by ``_canonical_server``.
+    """
+    data = _without_nulls(data)
     install_data = data.get("install", {})
     install: dict[Platform, list[str]] = {}
 
@@ -1198,8 +1689,14 @@ def _parse_overlay_document(
     """
     try:
         data = yaml.safe_load(content)
-    except yaml.YAMLError as exc:
-        logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
+    except Exception as exc:
+        # Every exception, not only YAMLError: PyYAML's constructors raise
+        # ValueError (`!!int x`), KeyError (`!!bool x`) or AttributeError
+        # (`!!timestamp x`), whose text quotes the value, and one escaping here
+        # stopped the shipped manifest from loading (Consiliency/pmcp#342 rev 6).
+        logger.warning(
+            f"Skipping unreadable manifest overlay {path}: {yaml_error_text(exc)}"
+        )
         if failures is not None:
             failures.append("parse")
         return {}, {}, {}, {}
@@ -1217,11 +1714,18 @@ def _parse_overlay_document(
     if isinstance(raw_servers, dict):
         for name, server_data in raw_servers.items():
             try:
-                servers[name] = _parse_server_config(name, server_data)
+                server = _canonical_server(
+                    _parse_server_config(
+                        name, _schema_fields(server_data, _SERVER_SCHEMA_KEYS)
+                    )
+                )
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
 
@@ -1230,12 +1734,18 @@ def _parse_overlay_document(
     if isinstance(raw_clis, dict):
         for name, cli_data in raw_clis.items():
             try:
-                cli_alternatives[name] = _parse_cli_alternative(name, cli_data)
+                cli = _canonical_cli(
+                    _parse_cli_alternative(
+                        name, _schema_fields(cli_data, _CLI_SCHEMA_KEYS)
+                    )
+                )
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
 
@@ -1525,6 +2035,13 @@ def _build_manifest(
     for name, server_data in data.get("servers", {}).items():
         servers[name] = _parse_server_config(name, server_data)
 
+    # Weights come from the base alone, before any overlay
+    # (Consiliency/pmcp#342): an overlay may add or replace servers, but it
+    # never changes how much another server's keyword counts.
+    base_weights = keyword_weights(servers.values())
+    base_categories = category_keyword_index(servers)
+    overlay_cli_names: set[str] = set()
+
     # Merge private/custom overlays over the shipped manifest (default path only).
     if apply_overlays:
         for source in overlays:
@@ -1572,10 +2089,11 @@ def _build_manifest(
                 if name in servers:
                     logger.warning(
                         f"Manifest overlay ({label}) from {overlay_path} overrides "
-                        f"existing server '{name}'"
+                        f"existing {_server_label(name)}"
                     )
             servers.update(overlay_servers)
             cli_alternatives.update(overlay_clis)
+            overlay_cli_names.update(overlay_clis)
 
             # Applied AFTER this source's whole-entry replaces, so a patch can
             # refine an entry the same overlay just replaced. Patching merges
@@ -1585,7 +2103,8 @@ def _build_manifest(
                 if existing is None:
                     logger.warning(
                         f"Manifest overlay ({label}) from {overlay_path} has a "
-                        f"'server_env' patch for unknown server '{name}': skipped"
+                        f"'server_env' patch for an unknown server "
+                        f"({_server_label(name)}): skipped"
                     )
                     continue
                 servers[name] = replace(
@@ -1619,6 +2138,9 @@ def _build_manifest(
         discovery_queue_path=data.get(
             "discovery_queue_path", ".mcp-gateway/discovery_queue.json"
         ),
+        base_keyword_weights=base_weights,
+        base_category_keywords=base_categories,
+        overlay_cli_names=frozenset(overlay_cli_names),
     )
 
     logger.info(
diff --git a/src/pmcp/manifest/matcher.py b/src/pmcp/manifest/matcher.py
index f9dd835..0ad1ebb 100644
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
+
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
 
-    return {
-        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()
-    }
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
+        except Exception as exc:
+            logger.warning(
+                f"rank_cli_hints: skipping an unusable entry ({_cli_label(name)}): "
+                f"{type(exc).__name__}"
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
-            )
-        )
+            continue
+        if match is not None:
+            matches.append(match)
 
     return sorted(matches, key=lambda match: (-match.score, match.hint.name))
 
@@ -208,8 +244,17 @@ def _keyword_match(
     best_match: MatchResult | None = None
     best_score = 0.0
 
-    # Check detected CLIs first (preferred)
-    for match in rank_cli_hints(query, manifest, available_clis=detected_clis):
+    # Check detected CLIs first (preferred). An overlay CLI ranks below every
+    # server (Consiliency/pmcp#342 rev 6): it is considered only if nothing
+    # else reaches the threshold.
+    overlay_cli_names: frozenset[str] = getattr(
+        manifest, "overlay_cli_names", frozenset()
+    )
+    ranked = rank_cli_hints(query, manifest, available_clis=detected_clis)
+    overlay_ranked = [m for m in ranked if m.hint.name in overlay_cli_names]
+    for match in ranked:
+        if match.hint.name in overlay_cli_names:
+            continue
         cli = manifest.cli_alternatives[match.hint.name]
         if match.score > best_score:
             best_score = match.score
@@ -225,7 +270,14 @@ def _keyword_match(
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
@@ -242,6 +294,17 @@ def _keyword_match(
     if best_match and best_score >= 0.2:  # Minimum threshold
         return best_match
 
+    for match in overlay_ranked:
+        if match.score >= 0.2:
+            return MatchResult(
+                matched=True,
+                entry_name=match.hint.name,
+                entry_type="cli",
+                confidence=match.score,
+                reasoning=f"Keyword match for installed CLI: {match.hint.name}",
+                cli_config=manifest.cli_alternatives[match.hint.name],
+            )
+
     return MatchResult(
         matched=False,
         entry_name="",
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 290c030..4086d46 100644
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
@@ -737,7 +737,10 @@ class GatewayServer:
             manifest = load_manifest()
             manifest_servers = manifest.servers
         except Exception as e:
-            logger.warning(f"Failed to load manifest startup configs: {e}")
+            # Class only: an error's text can quote overlay input (Consiliency/pmcp#342).
+            logger.warning(
+                f"Failed to load manifest startup configs: {type(e).__name__}"
+            )
 
         enabled_auto_start = load_enabled_auto_start(
             project_root=self._project_root,
@@ -770,21 +773,7 @@ class GatewayServer:
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
index 830b877..0f50da5 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -13,7 +13,7 @@ import platform
 from collections import deque
 from datetime import datetime, timezone
 from pathlib import Path
-from collections.abc import Callable, Mapping
+from collections.abc import Callable, Iterable, Mapping
 from typing import Any, Literal, cast, NamedTuple
 
 import anyio
@@ -39,6 +39,7 @@ from pmcp.client.manager import (
 )
 from pmcp.config.guidance import GuidanceConfig
 from pmcp.config.loader import (
+    startup_skip_message,
     registry_allow_private_from_config,
     StartupObservationSnapshot,
     StartupSkipReason,
@@ -84,7 +85,14 @@ from pmcp.manifest.installer import (
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
@@ -789,6 +797,36 @@ def _summarize_arg_schema(
     return prop_type, None, ""
 
 
+_PASCAL_RE = re.compile(r"^[A-Z][a-z]{3,}$")
+
+
+def _names_an_unknown_service(query: str, server_names: Iterable[str]) -> bool:
+    """Whether a non-initial PascalCase word names no known server (issue #56).
+
+    Shared by the category pre-check and the overlay-CLI rule
+    (Consiliency/pmcp#342 rev 5), so both read the query the same way.
+    """
+    known = {
+        n.lower().replace("-", "").replace("_", "").replace(" ", "")
+        for n in server_names
+        if isinstance(n, str)
+    }
+    return any(
+        idx > 0
+        and _PASCAL_RE.match(w)
+        and w.lower().replace("-", "").replace("_", "") not in known
+        for idx, w in enumerate(query.split())
+    )
+
+
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
 
@@ -1152,16 +1190,23 @@ class GatewayTools:
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
@@ -1175,16 +1220,17 @@ class GatewayTools:
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
@@ -1193,18 +1239,16 @@ class GatewayTools:
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
@@ -1929,7 +1973,11 @@ class GatewayTools:
                 manifest = load_manifest()
                 manifest_servers = manifest.servers
             except Exception as e:
-                logger.warning(f"Failed to load manifest startup configs: {e}")
+                # Class only: an error's text can quote overlay input
+                # (Consiliency/pmcp#342 rev 5).
+                logger.warning(
+                    f"Failed to load manifest startup configs: {type(e).__name__}"
+                )
 
             provisioned: dict[str, str | None] = {}
             try:
@@ -1967,21 +2015,7 @@ class GatewayTools:
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
@@ -3334,6 +3368,9 @@ class GatewayTools:
             cli_alternatives=dict(manifest.cli_alternatives),
             servers=merged_servers,
             discovery_queue_path=manifest.discovery_queue_path,
+            base_keyword_weights=manifest.base_keyword_weights,
+            base_category_keywords=manifest.base_category_keywords,
+            overlay_cli_names=manifest.overlay_cli_names,
         )
 
     def _get_server_env_metadata(
@@ -3587,6 +3624,32 @@ class GatewayTools:
         )
         return issue_title, body
 
+    def _use_cli_resolution(self, hint: Any) -> CapabilityResolution:
+        """The `use_cli` resolution for one CLI hint (shared by both CLI tiers)."""
+        return CapabilityResolution(
+            status="use_cli",
+            message=(
+                f"Use Bash/direct CLI with '{hint.name}'. PMCP is recommending "
+                "the native command here; it is not executing the command or "
+                "provisioning an MCP server for this path."
+            ),
+            cli=CLIResolution(
+                name=hint.name,
+                path=hint.path,
+                description=hint.description,
+                available=hint.available,
+                check_command=hint.check_command,
+                help_command=hint.help_command,
+                examples=hint.examples,
+                prefer_mcp_for=hint.prefer_mcp_for,
+                reason=hint.reason,
+            ),
+            recommendation=(
+                f"Run '{hint.name}' directly via Bash/direct CLI. "
+                "Use gateway.request_capability again only if you need an MCP server."
+            ),
+        )
+
     async def request_capability(
         self, input_data: dict[str, Any]
     ) -> CapabilityResolution:
@@ -3620,10 +3683,32 @@ class GatewayTools:
         if cli_hint_matches:
             logger.debug(
                 "Matched CLI hints for future response plumbing: %s",
-                ", ".join(match.hint.name for match in cli_hint_matches[:3]),
+                ", ".join(
+                    _cli_label(match.hint.name) for match in cli_hint_matches[:3]
+                ),
             )
+        # One rule, applied where CLI and server results are combined
+        # (Consiliency/pmcp#342 rev 6): an overlay CLI ranks below EVERY server
+        # tier. Shipped CLIs keep main's precedence (`cli_hint_match`, which the
+        # name tier may yield to and which answers before the category tier).
+        # An overlay CLI (`overlay_cli_match`) answers only after the name,
+        # category and configured-server tiers all found nothing.
         cli_hint_match = next(
-            (match for match in cli_hint_matches if match.hint.available),
+            (
+                match
+                for match in cli_hint_matches
+                if match.hint.available
+                and match.hint.name not in manifest.overlay_cli_names
+            ),
+            None,
+        )
+        overlay_cli_match = next(
+            (
+                match
+                for match in cli_hint_matches
+                if match.hint.available
+                and match.hint.name in manifest.overlay_cli_names
+            ),
             None,
         )
 
@@ -3641,10 +3726,17 @@ class GatewayTools:
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
+                # First wins: base (shipped) names come first in the merged
+                # dict, so an overlay name that normalises alike
+                # (`play_wright`) cannot take a shipped name's slot
+                # (Consiliency/pmcp#342 rev 4).
+                norm_to_server.setdefault(normalized_server_name(n), n)
+            except Exception as exc:
+                _log_unusable_manifest_entry("request_capability", n, exc)
         name_match: str | None = None
         for window_size in (3, 2, 1):
             for i in range(len(query_words) - window_size + 1):
@@ -3667,28 +3759,39 @@ class GatewayTools:
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
@@ -3708,42 +3811,15 @@ class GatewayTools:
             )
 
         if cli_hint_match is not None:
-            hint = cli_hint_match.hint
-            return CapabilityResolution(
-                status="use_cli",
-                message=(
-                    f"Use Bash/direct CLI with '{hint.name}'. PMCP is recommending "
-                    "the native command here; it is not executing the command or "
-                    "provisioning an MCP server for this path."
-                ),
-                cli=CLIResolution(
-                    name=hint.name,
-                    path=hint.path,
-                    description=hint.description,
-                    available=hint.available,
-                    check_command=hint.check_command,
-                    help_command=hint.help_command,
-                    examples=hint.examples,
-                    prefer_mcp_for=hint.prefer_mcp_for,
-                    reason=hint.reason,
-                ),
-                recommendation=(
-                    f"Run '{hint.name}' directly via Bash/direct CLI. "
-                    "Use gateway.request_capability again only if you need an MCP server."
-                ),
-            )
+            return self._use_cli_resolution(cli_hint_match.hint)
 
         # --- Tier 2 pre-check: detect unknown named services (Fix C, issue #56) ---
         # If the query contains a PascalCase word (not the first word of the sentence)
         # that is NOT a known server name, the user is likely requesting a specific
         # external service not in the manifest. Skip category matching and fall
         # through to not_available so search_registry guidance is surfaced.
-        _pascal_re = re.compile(r"^[A-Z][a-z]{3,}$")
-        _unknown_service = any(
-            idx > 0
-            and _pascal_re.match(w)
-            and w.lower().replace("-", "").replace("_", "") not in norm_to_server
-            for idx, w in enumerate(parsed.query.split())
+        _unknown_service = _names_an_unknown_service(
+            parsed.query, merged_manifest.servers
         )
 
         # --- Tier 2: category keyword match ---
@@ -3758,16 +3834,17 @@ class GatewayTools:
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
@@ -3778,7 +3855,10 @@ class GatewayTools:
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
@@ -3900,6 +3980,11 @@ class GatewayTools:
                 recommendation=f"Call gateway.provision(server_name='{server_name}')",
             )
 
+        # An overlay CLI answers only here, after every server tier found
+        # nothing (Consiliency/pmcp#342 rev 6).
+        if overlay_cli_match is not None:
+            return self._use_cli_resolution(overlay_cli_match.hint)
+
         registry_candidates = (
             await self._registry_candidates_for_query(parsed.query)
             if _unknown_service
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
import re
import sys
import itertools
import json
import warnings
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pydantic
import pytest
import yaml

from pmcp.cli import async_main, parse_args
from pmcp.cli_commands.secrets import _extract_required_keys
from pmcp.config.loader import load_configs, resolve_startup_configs
from pmcp.identity import filter_self_references
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
    assert view.base_category_keywords is not None
    assert view.base_category_keywords == manifest.base_category_keywords
    assert view.overlay_cli_names == manifest.overlay_cli_names


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
GOOD_REMOTE = {
    "keywords": ["zzremote"],
    "url": "https://example.invalid/remote",
    "oidc_issuer_url": "https://issuer.invalid",
}
# ~/.mcp.json for the walk: a partial (command-less) entry named like the bad
# overlay entry makes load_configs inherit its defaults (codex F051), next to a
# healthy sibling that must always load.
PARTIAL_CONFIGS = {
    "mcpServers": {
        "puppeteer": {"args": []},
        "healthy": {"command": "healthy-command"},
    }
}


def _refresh_tools(monkeypatch: pytest.MonkeyPatch) -> GatewayTools:
    """A GatewayTools whose refresh runs on the REAL load_manifest and the
    REAL load_configs (rev 4: stubbing load_configs hid codex F051)."""
    tools = GatewayTools(
        client_manager=RefreshClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )
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

    # Configured-default inheritance (rev 4): ~/.mcp.json holds partial
    # entries named like the overlay entries, plus a healthy sibling.
    configured = {c.name for c in load_configs()}
    assert "healthy" in configured
    # pmcp secrets: one entry must not drop a later server's metadata.
    _, _, auth_metadata, _ = _extract_required_keys(Path.cwd())
    assert "zz-good-remote" in auth_metadata


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
    _write(Path.home() / ".mcp.json", json.dumps(PARTIAL_CONFIGS))
    # Every shape, both remote and local (rev 4: rev 3 stripped `url` for some
    # shapes, so a remote entry's `args` was never walked).
    for (_, value), remote in itertools.product(SHAPES, (True, False)):
        bad = {"keywords": ["zzbad", "screenshot"], "command": "npx"}
        if remote:
            bad["url"] = "https://example.invalid/mcp"
        bad[field_name] = value
        overlay = {
            "servers": {
                # aaa- sorts first, so `pmcp secrets` meets it before the good
                # remote sibling (claude F1).
                "aaa-" + SECRET_NAME: bad,
                SECRET_NAME: bad,
                "puppeteer": bad,
                "good": GOOD_SERVER,
                "zz-good-remote": GOOD_REMOTE,
            },
            "cli_alternatives": {"goodcli": {"keywords": ["zzcli"]}},
        }
        _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
        with (
            caplog.at_level(logging.DEBUG),
            warnings.catch_warnings(record=True) as seen,
        ):
            warnings.simplefilter("always")
            _drive_every_consumer(tools)
        _assert_nothing_leaked(caplog)
        # rev 4: a pydantic serializer warning quoted the value (input_value=)
        assert not [
            w
            for w in seen
            if SECRET in str(w.message) or "input_value" in str(w.message)
        ]
        caplog.clear()


@pytest.mark.parametrize("field_name", CLI_FIELDS)
def test_every_cli_field_with_every_bad_shape_is_contained(
    field_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`git` is overridden too: it is on PATH, so rank_cli_hints scores it."""
    tools = _refresh_tools(monkeypatch)
    _write(Path.home() / ".mcp.json", json.dumps(PARTIAL_CONFIGS))
    for _, value in SHAPES:
        bad: dict[str, Any] = {"keywords": ["git", "commits"]}
        bad[field_name] = value
        overlay = {
            "servers": {"good": GOOD_SERVER, "zz-good-remote": GOOD_REMOTE},
            "cli_alternatives": {SECRET_NAME: bad, "git": bad, "goodcli": {}},
        }
        _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
        with (
            caplog.at_level(logging.DEBUG),
            warnings.catch_warnings(record=True) as seen,
        ):
            warnings.simplefilter("always")
            _drive_every_consumer(tools)
        _assert_nothing_leaked(caplog)
        # rev 4: a pydantic serializer warning quoted the value (input_value=)
        assert not [
            w
            for w in seen
            if SECRET in str(w.message) or "input_value" in str(w.message)
        ]
        caplog.clear()


@pytest.mark.parametrize(
    ("key", "label"), [(5, "int"), (True, "bool"), (None, "null"), ("", "empty")]
)
def test_a_non_string_server_or_cli_key_is_contained(
    key: Any, label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _refresh_tools(monkeypatch)
    _write(Path.home() / ".mcp.json", json.dumps(PARTIAL_CONFIGS))
    overlay = {
        "servers": {
            key: {"keywords": ["screenshot"]},
            "good": GOOD_SERVER,
            "zz-good-remote": GOOD_REMOTE,
        },
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
        loader._canonical_server(server)
    for cli in manifest.cli_alternatives.values():
        loader._canonical_cli(cli)


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


# --- revision 4: every scoring statistic is base-only (round 3, grok F001) -----


def _ask(query: str, available: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    result = asyncio.run(
        _gateway().request_capability(
            {"query": query, "available_clis": list(available)}
        )
    )
    return result.status, sorted(c.name for c in result.candidates or [])


def test_a_replaced_category_server_does_not_empty_another_category() -> None:
    """Grok F001's falsifier: `markdown` spans 2 categories; a replacement of
    `playwright` with `keywords: [markdown]` made it 3 (weight 0.7 -> 0.3)."""
    before = _ask("markdown")
    assert before[0] == "pick_from_category"
    assert {"firecrawl", "jina"} <= set(before[1])
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n  playwright:\n    description: x\n    command: npx\n"
        "    keywords: [markdown]\n",
    )
    assert _ask("markdown") == before


def _category_vocabulary() -> list[str]:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    words: set[str] = set()
    for names in loader._CATEGORY_MAP.values():
        for name in names:
            if name in shipped.servers:
                words.update(shipped.servers[name].keywords)
    words.update(loader._CATEGORY_MAP)
    return sorted(words)


def _overlays() -> dict[str, dict[str, Any]]:
    """Generated overlays: additive, replacing a shipped name, replacing a
    `_CATEGORY_MAP` name -- each declaring keywords from OTHER categories."""
    import random

    rng = random.Random(342)
    vocab = _category_vocabulary()
    mapped = sorted({n for ns in loader._CATEGORY_MAP.values() for n in ns})
    shipped = sorted(load_manifest(_SHIPPED_MANIFEST_PATH).servers)
    unmapped = [n for n in shipped if n not in mapped]

    def entry() -> dict[str, Any]:
        return {"command": "npx", "keywords": rng.sample(vocab, 25)}

    def cli() -> dict[str, Any]:
        return {"keywords": rng.sample(vocab, 25), "check_command": ["git"]}

    return {
        "additive": {"servers": {f"zz-added-{i}": entry() for i in range(4)}},
        "replace-shipped": {
            "servers": {rng.choice(unmapped): entry() for _ in range(3)}
        },
        "replace-mapped": {
            "servers": {name: entry() for name in rng.sample(mapped, 6)}
        },
        "grok-f001": {
            "servers": {"playwright": {"command": "npx", "keywords": ["markdown"]}}
        },
        "everything": {"servers": {"zz-all": {"command": "npx", "keywords": vocab}}},
        # rev 5 (claude F001): overlay CLIs, new and overriding shipped `git`,
        # each AVAILABLE, competing with the category tier.
        "cli-new": {"cli_alternatives": {"zzcli": cli()}},
        "cli-override-git": {"cli_alternatives": {"git": cli()}},
        "cli-everything": {
            "cli_alternatives": {
                "zzcli-all": {"keywords": vocab, "check_command": ["git"]}
            }
        },
    }


@pytest.mark.parametrize("case", sorted(_overlays()))
def test_no_overlay_removes_a_non_overlay_server_from_any_discovery_entry_point(
    case: str,
) -> None:
    """Differential over the category vocabulary, on request_capability,
    catalog_search (limit lifted: top-N displacement is the documented
    exception) and match_capability (top-1: displaced only by an overlay).

    Exception, documented in D2: a query naming an overlay server resolves to
    it in request_capability's name tier (precedence, like top-N)."""
    document = _overlays()[case]
    overlay_servers = document.get("servers", {})
    available = tuple(document.get("cli_alternatives", {}))
    queries = _category_vocabulary()
    tools = _gateway()

    def snapshot() -> dict[str, Any]:
        manifest = load_manifest()
        out: dict[str, Any] = {}
        for q in queries:
            catalog = {
                c.name
                for c in tools._manifest_candidates_for_query(
                    q,
                    manifest=manifest,
                    configured_servers={},
                    exclude_servers=set(),
                    limit=1000,
                )
            }
            km = _keyword_match(q, manifest, set())
            out[q] = (
                _ask(q, available),
                catalog,
                km.entry_name if km.matched else None,
            )
        return out

    before = snapshot()
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(document),
    )
    after = snapshot()
    ours = set(overlay_servers)
    names = {loader.normalized_server_name(n) for n in ours}
    lost = []
    for q in queries:
        (st0, rc0), cat0, km0 = before[q]
        (st1, rc1), cat1, km1 = after[q]
        if (
            not set(q.lower().replace("-", " ").replace("_", " ").split())
            & {w for n in ours for w in n.lower().replace("-", " ").split()}
            and loader.normalized_server_name(q) not in names
        ):
            gone = (set(rc0) - ours) - set(rc1)
            if gone:
                lost.append((q, "request_capability", sorted(gone)))
        gone = (cat0 - ours) - cat1
        if gone:
            lost.append((q, "catalog_search", sorted(gone)))
        if km0 and km0 not in ours and km1 != km0 and km1 not in ours:
            lost.append((q, "match_capability", [km0]))
    assert lost == []
    assert sum(1 for q in queries if before[q][0][1]) > 50  # not vacuous


def test_an_overlay_name_aliasing_a_shipped_name_keeps_the_shipped_match() -> None:
    """request_capability's name index: the base name wins a normalised tie."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"play_wright": {"command": "npx"}}}),
    )
    assert _ask("playwright")[1] == ["playwright"]


# --- revision 4: every field against every consumer that reads it (codex F051) --


def test_a_remote_entry_with_bad_args_cannot_abort_load_configs() -> None:
    """Codex F051's falsifier, through the real loader and load_configs."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n  remote-bad:\n    url: https://example.invalid/mcp\n"
        "    command: npx\n    args: 5\n",
    )
    config = _write(
        Path.home() / ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "remote-bad": {"args": []},
                    "healthy": {"command": "healthy-command"},
                }
            }
        ),
    )
    assert "remote-bad" not in load_manifest().servers
    resolved = load_configs(user_config_paths=[config])
    assert "healthy" in {entry.name for entry in resolved}


def test_guard_configured_default_inheritance_contains_one_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The second line: a manifest that bypassed the overlay check."""
    bad = _bad_server(name="remote-bad", args=5, url="https://example.invalid/mcp")
    manifest = _with({"remote-bad": bad})
    monkeypatch.setattr("pmcp.manifest.loader.load_manifest", lambda: manifest)
    config = _write(
        tmp_path / "cfg.json",
        json.dumps(
            {
                "mcpServers": {
                    # partial: inheriting the bad entry's `args` is what raised
                    "remote-bad": {"args": []},
                    "healthy": {"command": "healthy-command"},
                }
            }
        ),
    )
    resolved = {c.name: c for c in load_configs(user_config_paths=[config])}
    assert "healthy" in resolved
    # With no usable defaults and no command, it is skipped, exactly as when
    # the manifest is unavailable; it never takes its siblings down.
    assert "remote-bad" not in resolved


def test_guard_secrets_contains_one_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude F1: one bad entry dropped every later server's auth metadata."""
    bad = _bad_server(name="aaa-bad", headers=5)
    good = _bad_server(
        name="zz-good-remote",
        url="https://example.invalid/r",
        oidc_issuer_url="https://issuer.invalid",
    )
    manifest = _with({"aaa-bad": bad, "zz-good-remote": good})
    monkeypatch.setattr("pmcp.cli_commands.secrets.load_manifest", lambda: manifest)
    _, _, auth_metadata, _ = _extract_required_keys(Path.cwd())
    assert "zz-good-remote" in auth_metadata


@pytest.mark.parametrize(
    ("entry", "loads"),
    [
        ({"url": "https://example.invalid/m", "args": 5}, False),
        ({"url": "https://example.invalid/m", "command": 5}, False),
        ({"url": "https://example.invalid/m", "args": "x"}, True),  # chars, as on main
        ({"command": "npx", "headers": 5}, False),  # pmcp secrets reads it
        ({"url": "https://example.invalid/m", "extra_env": {"A": 1}}, True),
    ],
)
def test_a_field_is_checked_against_every_consumer_that_reads_it(
    entry: dict[str, Any], loads: bool
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"odd": entry, "good": GOOD_SERVER}}),
    )
    servers = load_manifest().servers
    assert ("odd" in servers) is loads and "good" in servers


# --- revision 5: YAML errors are value-free (round 4, codex F002 / grok F001) ---

YAML_SENTINEL = "sk-yaml-SENTINEL-9c1e"


def _malformed_documents() -> dict[str, str]:
    """Malformed YAML from the grammar, the sentinel at different positions."""
    s = YAML_SENTINEL
    good = "  good: {keywords: [zzgood], command: npx}\n"
    return {
        "flow-seq-unterminated": f"servers:\n  bad:\n    args: [{s}\n" + good,
        "flow-seq-unterminated-first": f"servers:\n  bad: {{args: [{s}, x\n",
        "flow-map-unterminated": f"servers:\n  bad: {{command: {s}\n" + good,
        "bad-indentation": f"servers:\n  bad:\n    command: npx\n   args: {s}\n",
        "tab-indentation": f"servers:\n  bad:\n\tcommand: {s}\n",
        "unclosed-double-quote": f'servers:\n  bad:\n    command: "{s}\n',
        "unclosed-single-quote": f"servers:\n  bad:\n    command: '{s}\n",
        "invalid-escape": f'servers:\n  bad:\n    command: "\\q{s}"\n',
        "bad-tag": f"servers:\n  bad:\n    command: !<!{s}> x\n",
        "python-tag": f"servers:\n  bad:\n    command: !!python/object:os.{s} x\n",
        "undefined-alias": f"servers:\n  bad:\n    command: *{s}\n",
        "key-in-error-token": f"servers:\n  {s}: [\n",
        "sentinel-last-line": f"servers:\n  bad:\n    args: [x\n# {s}\n",
        "block-in-flow": f"servers: [{s}, {{a: b}}\n  c: d]\n",
    }


@pytest.mark.parametrize("case", sorted(_malformed_documents()))
@pytest.mark.parametrize("source", ["user", "env", "project"])
def test_a_malformed_overlay_is_reported_without_any_value(
    case: str,
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _malformed_documents()[case]
    if source == "user":
        path = _write(Path.home() / ".pmcp" / "manifest.yaml", text)
    elif source == "env":
        path = _write(tmp_path / "env.yaml", text)
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(path))
    else:
        path = _write(tmp_path / "proj" / ".pmcp" / "manifest.yaml", text)
        approve_project_file(path)
        monkeypatch.chdir(tmp_path / "proj")
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
        _candidates(_gateway(), "screenshot")
    assert "playwright" in manifest.servers  # the shipped manifest still loads
    messages = [r.getMessage() for r in caplog.records]
    assert not [m for m in messages if YAML_SENTINEL in m]
    unreadable = [m for m in messages if "Skipping unreadable manifest overlay" in m]
    assert unreadable, messages[-5:]
    assert all(re.search(r"Error at line \d+, column \d+$", m) for m in unreadable)


def test_codex_f002_falsifier_parse_overlay_document_directly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    content = (
        f"servers:\n  malformed:\n    command: npx\n    args: [{YAML_SENTINEL}\n"
    ).encode()
    with caplog.at_level(logging.WARNING):
        result = loader._parse_overlay_document(Path("overlay.yaml"), content)
    assert result == ({}, {}, {}, {})
    assert YAML_SENTINEL not in caplog.text


def test_a_load_failure_is_logged_by_class_on_every_catch_site(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The sites that catch load_manifest() and log it (refresh, load_configs)."""

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise yaml.YAMLError(f"bad token {YAML_SENTINEL}")

    tools = _refresh_tools(monkeypatch)
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", boom)
    monkeypatch.setattr("pmcp.manifest.loader.load_manifest", boom)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(tools.refresh({"reason": "test"}))
        load_configs()
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "Failed to load manifest startup configs: YAMLError" in m for m in messages
    )
    assert any("Manifest defaults unavailable" in m for m in messages)
    assert not [m for m in messages if YAML_SENTINEL in m]


# --- revision 5: parse, don't validate (round 4, codex F001) -------------------

YAML_TAG_VALUES = {
    "binary": "!!binary bnB4",
    "timestamp": "2001-12-14t21:59:43.10-05:00",
    "set": "!!set {a, b}",
    "omap": "!!omap [{a: 1}]",
    "float-tag": '!!float "1.5"',
    "inf": ".inf",
    "nan": ".nan",
    "python-tag": "!!python/name:os.system",
}


def _json_native(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, list):
        return all(_json_native(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_native(v) for k, v in value.items())
    return False


@pytest.mark.parametrize("tag", sorted(YAML_TAG_VALUES))
def test_no_consumer_ever_sees_a_non_json_value(
    tag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A YAML tag on EVERY field, one entry per field, local and remote, each
    with a partial `.mcp.json` entry of the same name: the kept entries are
    JSON-native all the way down, and every consumer runs."""
    value = YAML_TAG_VALUES[tag]
    lines = ["base: &base {keywords: [zzbad]}", "servers:"]
    partial: dict[str, Any] = {"healthy": {"command": "healthy-command"}}
    for field_name in SERVER_FIELDS:
        for kind, extra in (
            ("local", "command: npx"),
            ("remote", "url: https://example.invalid/mcp"),
        ):
            # zz- names: ties rank by name, so playwright stays in the top 5
            name = f"zz-bad-{kind}-{field_name.replace('_', '-')}"
            lines += [
                f"  {name}:",
                "    <<: *base",
                f"    {extra}",
                f"    {field_name}: {value}",
            ]
            partial[name] = {"args": []}
    lines += [
        "  good: {keywords: [zzgood], command: npx}",
        "  zz-good-remote: {keywords: [zzremote], url: https://example.invalid/r, "
        "oidc_issuer_url: https://issuer.invalid}",
        "cli_alternatives:",
        "  goodcli: {keywords: [zzcli]}",
    ]
    for field_name in CLI_FIELDS:
        lines += [f"  badcli-{field_name.replace('_', '-')}: {{{field_name}: {value}}}"]
    _write(Path.home() / ".pmcp" / "manifest.yaml", "\n".join(lines) + "\n")
    _write(Path.home() / ".mcp.json", json.dumps({"mcpServers": partial}))
    manifest = load_manifest()
    for server in manifest.servers.values():
        assert _json_native(dataclasses.asdict(server)), server.name
    for cli in manifest.cli_alternatives.values():
        assert _json_native(dataclasses.asdict(cli)), cli.name
    if tag == "python-tag":
        assert "good" not in manifest.servers  # the whole document is refused
        return
    assert "good" in manifest.servers
    _drive_every_consumer(_refresh_tools(monkeypatch))
    configured = load_configs()
    assert "healthy" in {c.name for c in filter_self_references(configured)}


def test_codex_f001_falsifier_binary_command_does_not_abort_siblings() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n  bad:\n    command: !!binary bnB4\n    args: []\n",
    )
    config = _write(
        Path.home() / ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "bad": {"args": []},
                    "healthy": {"command": "healthy-command"},
                }
            }
        ),
    )
    configs = load_configs(user_config_paths=[config])
    assert "healthy" in {entry.name for entry in configs}
    usable = filter_self_references(configs)
    assert "healthy" in {entry.name for entry in usable}


def test_consumers_read_the_validated_value_not_the_raw_one() -> None:
    """`supports_url_elicitation: "false"` validates to False; on rev 4 the kept
    entry held the raw (truthy) string, so `pmcp secrets` reported "false" as
    an elicitation-capable server's metadata."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "remote": {
                        "url": "https://example.invalid/m",
                        "supports_url_elicitation": "false",
                        "declared_scopes": ["a"],
                    }
                }
            }
        ),
    )
    server = load_manifest().servers["remote"]
    assert server.supports_url_elicitation is False
    _, _, auth_metadata, _ = _extract_required_keys(Path.cwd())
    assert "supports_url_elicitation" not in auth_metadata.get("remote", {})


def test_every_shipped_entry_is_already_canonical() -> None:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    for server in shipped.servers.values():
        assert loader._canonical_server(server) == server, server.name
    for cli in shipped.cli_alternatives.values():
        assert loader._canonical_cli(cli) == cli, cli.name


@pytest.mark.parametrize(
    ("entry", "loads"),
    [
        ({"command": "npx", "oidc_issuer_url": 5}, False),  # claude N1
        ({"command": "npx", "supports_url_elicitation": "maybe"}, False),
        ({"command": "npx", "supports_url_elicitation": "true"}, True),
        ({"command": "npx", "headers": {"X-Key": "${TOKEN}"}}, True),
    ],
)
def test_metadata_fields_are_checked_on_local_entries_too(
    entry: dict[str, Any], loads: bool
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"odd": entry, "good": GOOD_SERVER}}),
    )
    servers = load_manifest().servers
    assert ("odd" in servers) is loads and "good" in servers


# --- revision 5: an overlay CLI never pre-empts the server tiers (claude F001) ---


def test_claude_f001_falsifier_an_overlay_cli_does_not_hide_category_servers() -> None:
    assert "playwright" in _ask("browser automation")[1]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "zzcli": {
                        "description": "d",
                        "keywords": ["browser automation"],
                        "check_command": [sys.executable, "--version"],
                    }
                }
            }
        ),
    )
    result = asyncio.run(_gateway().request_capability({"query": "browser automation"}))
    assert "playwright" in {c.name for c in result.candidates or []}


def test_an_overlay_cli_still_answers_when_no_server_tier_does() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "zzcli": {"keywords": ["zzonlycli"], "check_command": ["git"]}
                }
            }
        ),
    )
    result = asyncio.run(
        _gateway().request_capability(
            {"query": "zzonlycli", "available_clis": ["zzcli"]}
        )
    )
    assert result.status == "use_cli"


def test_a_shipped_cli_keeps_mains_precedence() -> None:
    """Only an overlay CLI yields; shipped `git` answers as on main."""
    status, _ = _ask("git commits", ("git",))
    assert status == "use_cli"


def test_inherited_fields_are_kept_converted() -> None:
    """A remote entry's `args: "ab"` is read by configured-default inheritance
    as characters (as on main); the KEPT entry holds that converted list, so
    no other consumer reads the raw string."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {"servers": {"remote": {"url": "https://example.invalid/m", "args": "ab"}}}
        ),
    )
    assert load_manifest().servers["remote"].args == ["a", "b"]


def test_an_overlay_cli_is_recorded_as_one() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": {"zzcli": {}, "git": {}}}),
    )
    assert load_manifest().overlay_cli_names == frozenset({"zzcli", "git"})
    assert load_manifest(_SHIPPED_MANIFEST_PATH).overlay_cli_names == frozenset()


# --- revision 6: an overlay CLI never outranks a server (round 5, F001/F003) ----


def test_claude_f001_falsifier_an_overlay_cli_named_like_a_server() -> None:
    """Round 5: `request_capability("airbnb")` went to `use_cli`, no candidates."""
    assert "airbnb" in _ask("airbnb")[1]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "airbnb": {
                        "description": "d",
                        "keywords": ["airbnb"],
                        "check_command": [sys.executable, "--version"],
                    }
                }
            }
        ),
    )
    result = asyncio.run(_gateway().request_capability({"query": "airbnb"}))
    assert "airbnb" in {c.name for c in result.candidates or []}


def test_codex_f003_falsifier_an_overlay_cli_cannot_hide_an_exact_match() -> None:
    before = _ask("redis", ("redis",))
    assert "redis" in before[1]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "redis": {"keywords": ["redis"], "check_command": ["git"]}
                }
            }
        ),
    )
    assert "redis" in _ask("redis", ("redis",))[1]


def _every_discovery_term() -> list[str]:
    """Every shipped server name, every category word, every shipped keyword."""
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    terms: set[str] = set(shipped.servers)
    for cat_name in loader._CATEGORY_MAP:
        terms.update(cat_name.replace("/", " ").split())
        terms.add(cat_name)
    for server in shipped.servers.values():
        terms.update(server.keywords)
    return sorted(terms)


def test_no_overlay_cli_outranks_a_server_or_a_shipped_cli_anywhere() -> None:
    """The class, derived from the tiers: one overlay CLI per discovery term --
    named like the term when it is a server name -- all available at once. For
    every term: request_capability keeps every non-overlay server and every
    shipped-CLI answer; catalog_search's manifest candidates are unchanged;
    match_capability keeps its server or shipped-CLI match."""
    terms = _every_discovery_term()
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    clis: dict[str, Any] = {}
    for i, term in enumerate(terms):
        name = term if term in shipped.servers else f"zzcli-{i}"
        if name in shipped.cli_alternatives:
            name = f"zzcli-{i}"
        clis[name] = {"keywords": [term], "check_command": ["git"]}
    available = tuple(clis)
    tools = _gateway()

    def snapshot() -> dict[str, Any]:
        manifest = load_manifest()
        out: dict[str, Any] = {}
        for term in terms:
            result = asyncio.run(
                tools.request_capability(
                    {"query": term, "available_clis": list(available)}
                )
            )
            catalog = {
                c.name
                for c in tools._manifest_candidates_for_query(
                    term,
                    manifest=manifest,
                    configured_servers={},
                    exclude_servers=set(),
                    limit=1000,
                )
            }
            km = _keyword_match(term, manifest, set(available))
            out[term] = (
                result.status,
                result.cli.name if result.cli else None,
                {c.name for c in result.candidates or []},
                catalog,
                (km.entry_type, km.entry_name) if km.matched else None,
            )
        return out

    before = snapshot()
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": clis}),
    )
    assert set(load_manifest().overlay_cli_names) == set(clis)
    after = snapshot()
    lost = []
    for term in terms:
        st0, cli0, rc0, cat0, km0 = before[term]
        st1, cli1, rc1, cat1, km1 = after[term]
        if rc0 - rc1:
            lost.append((term, "request_capability servers", sorted(rc0 - rc1)))
        if st0 == "use_cli" and (st1, cli1) != (st0, cli0):
            lost.append((term, "request_capability shipped CLI", cli0))
        if cat0 != cat1:
            lost.append((term, "catalog_search", sorted(cat0 ^ cat1)))
        if km0 is not None and km1 != km0:
            lost.append((term, "match_capability", km0))
    assert lost == []
    # Not vacuous: an overlay CLI still answers where nothing else does.
    gained = [
        t for t in terms if before[t][0] == "not_available" and after[t][0] == "use_cli"
    ]
    assert gained
    assert sum(1 for t in terms if before[t][2]) > 100


def test_gemini_note_category_selection_reads_no_cli() -> None:
    """Round 5 (gemini): the claimed `kw in category_keywords.get(best_cat, [])`
    CLI comparison is not in the code; category selection reads servers only,
    so an overlay CLI with a category's whole vocabulary changes nothing."""
    import inspect

    source = inspect.getsource(Manifest.get_servers_in_category)
    assert "cli_alternatives" not in source
    before = load_manifest().get_servers_in_category("browser automation")
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {"cli_alternatives": {"zzcli": {"keywords": _category_vocabulary()}}}
        ),
    )
    assert load_manifest().get_servers_in_category("browser automation") == before


# --- revision 6: only schema fields are checked (round 5, codex F002 / claude F002) --


def test_claude_f002_falsifier_unknown_keys_neither_drop_nor_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sk_live_planted_4f9a"
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n"
        "  mytool:\n"
        "    command: npx\n"
        "    keywords: [mytoolkw]\n"
        "    added: 2026-10-04\n"
        f"    {secret}: !!binary aGVsbG8=\n",
    )
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
    assert "mytool" in manifest.servers
    assert not any(secret in r.getMessage() for r in caplog.records)


def test_codex_f002_falsifier_a_rejection_names_no_overlay_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "sk-review-secret-field"
    document = (
        f"servers:\n  bad:\n    {sentinel}: .nan\n"
        f"    extra_env: {{{sentinel}-k: .nan}}\n"
        f"    args: [!!binary aGVsbG8=]\n"
    ).encode()
    with caplog.at_level(logging.DEBUG):
        loader._parse_overlay_document(Path("overlay.yaml"), document)
    assert caplog.records
    assert sentinel not in caplog.text
    assert any(
        "'args' holds a value that is not JSON" in r.getMessage()
        for r in caplog.records
    )


STORED_ONLY = [
    "discovery_diagnostics",
    "discovery_metadata",
    "replacement",
    "source",
    "status",
]


def test_the_stored_only_keys_are_the_loaders() -> None:
    assert set(STORED_ONLY) == set(loader._STORED_ONLY_KEYS)


@pytest.mark.parametrize("field_name", STORED_ONLY)
def test_a_stored_only_field_with_a_yaml_value_keeps_the_entry(field_name: str) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        f"servers:\n  odd:\n    command: npx\n    {field_name}: 2026-10-04\n",
    )
    assert "odd" in load_manifest().servers


def test_the_schema_keys_are_exactly_the_keys_the_parsers_read() -> None:
    """Derived from the parsers' own source: every `data.get("<key>")`."""
    import ast
    import inspect

    def read_keys(func: Any) -> set[str]:
        tree = ast.parse(inspect.getsource(func))
        return {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "data"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }

    assert read_keys(loader._parse_server_config) == set(loader._SERVER_SCHEMA_KEYS)
    assert read_keys(loader._parse_cli_alternative) == set(loader._CLI_SCHEMA_KEYS)


# --- revision 6: every exception from the YAML load is contained (codex F001) ----

TAG_SENTINEL = "sk-tag-SENTINEL-61d"


def _invalid_tag_documents() -> dict[str, str]:
    s = TAG_SENTINEL
    entry = "servers:\n  bad:\n    command: {}\n  good: {{keywords: [zzgood], command: npx}}\n"
    docs = {
        tag: entry.format(value)
        for tag, value in {
            "int": f"!!int {s}",
            "float": f"!!float {s}",
            "bool": f"!!bool {s}",
            "timestamp": f"!!timestamp {s}",
            "binary": f"!!binary {s}!",
            "set": f"!!set [{s}]",
            "omap": f"!!omap {{{s}: 1}}",
            "pairs": f"!!pairs {s}",
            "map": f"!!map [{s}]",
            "seq": f"!!seq {{{s}: 1}}",
            "str-map": f"!!str {{{s}: 1}}",
            "custom": f"!custom {s}",
            "python": f"!!python/name:{s}",
        }.items()
    }
    docs["merge-scalar"] = f"servers:\n  bad:\n    <<: {s}\n    command: npx\n"
    docs["merge-list-scalar"] = f"servers:\n  bad:\n    <<: [{s}]\n    command: npx\n"
    return docs


@pytest.mark.parametrize("case", sorted(_invalid_tag_documents()))
@pytest.mark.parametrize("source", ["user", "env", "project"])
def test_an_invalid_tag_is_contained_without_any_value(
    case: str,
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _invalid_tag_documents()[case]
    if source == "user":
        _write(Path.home() / ".pmcp" / "manifest.yaml", text)
    elif source == "env":
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(_write(tmp_path / "e.yaml", text)))
    else:
        path = _write(tmp_path / "proj" / ".pmcp" / "manifest.yaml", text)
        approve_project_file(path)
        monkeypatch.chdir(tmp_path / "proj")
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
        _candidates(_gateway(), "screenshot")
    assert "playwright" in manifest.servers
    assert "bad" not in manifest.servers
    messages = [r.getMessage() for r in caplog.records]
    assert not [m for m in messages if TAG_SENTINEL.lower() in m.lower()]
    # Either the document is refused (the load raised), or it parses and the
    # entry is skipped (`!!binary` with lenient base64 decodes to bytes).
    assert any(
        "Skipping unreadable manifest overlay" in m
        or "Skipping invalid server entry" in m
        for m in messages
    )


def test_codex_f001_falsifier_a_bad_numeric_tag_keeps_shipped_servers() -> None:
    base = loader._SHIPPED_MANIFEST_PATH
    overlay = loader._OverlaySource(
        "env",
        Path("overlay.yaml"),
        b"servers:\n  bad: {command: !!int sk-review-sentinel}\n",
        "read",
    )
    manifest, _ = loader._build_manifest(
        base, base.read_bytes(), [overlay], trusted=True
    )
    assert "playwright" in manifest.servers
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

### `consumers_ast.py` (rev 4: every consumer of every field, derived)

````python
"""Derive every consumer of a manifest ServerConfig / CLIAlternative mechanically.

A function is a consumer if its body mentions a manifest source
(load_manifest, get_server, manifest_servers, .servers of a Manifest, a
parameter annotated ServerConfig/ManifestServerConfig/CLIAlternative, or
cli_alternatives). For each consumer, list the ServerConfig / CLIAlternative
field names it reads as attributes. Run from the worktree root.
"""

import ast
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, "src")
from pmcp.manifest.loader import CLIAlternative, ServerConfig  # noqa: E402

SERVER_FIELDS = {f.name for f in dataclasses.fields(ServerConfig)}
CLI_FIELDS = {f.name for f in dataclasses.fields(CLIAlternative)}
MARKERS = (
    "load_manifest",
    "get_server",
    "manifest_servers",
    "manifest_server",
    "ManifestServerConfig",
    "ServerConfig",
    "CLIAlternative",
    "cli_alternatives",
    "manifest.servers",
    "merged_manifest",
    "manifest_by_name",
)

rows = []
for path in sorted(Path("src/pmcp").rglob("*.py")):
    text = path.read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        src = ast.get_source_segment(text, node) or ""
        if not any(m in src for m in MARKERS):
            continue
        attrs = {
            n.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Attribute)
        }
        getattrs = {
            n.args[1].value
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "getattr"
            and len(n.args) >= 2
            and isinstance(n.args[1], ast.Constant)
            and isinstance(n.args[1].value, str)
        }
        read = (attrs | getattrs)
        sf = sorted(read & SERVER_FIELDS)
        cf = sorted(read & CLI_FIELDS - SERVER_FIELDS)
        if sf or cf:
            rows.append((str(path), node.name, node.lineno, sf, cf))

for path, name, line, sf, cf in rows:
    print(f"{path}:{line} {name} | server: {', '.join(sf)} | cli-only: {', '.join(cf)}")
print(len(rows), "candidate consumer functions")
````

Its output on the rev-5 spike (`consumers_ast.out`):

````text
src/pmcp/cli.py:1229 run_status | server: headers, name, status | cli-only: 
src/pmcp/cli.py:1628 run_init | server: args, command, description, env_var | cli-only: 
src/pmcp/cli.py:1891 run_config | server: name, source | cli-only: 
src/pmcp/cli_commands/secrets.py:62 _extract_required_keys | server: authorization_server_metadata_url, client_id_metadata_document_url, declared_scopes, env_var, extra_env, headers, name, oidc_discovery_url, oidc_issuer_url, protected_resource_metadata_url, supports_url_elicitation | cli-only: 
src/pmcp/client/manager.py:74 _lazy_log_name | server: name, source | cli-only: 
src/pmcp/client/manager.py:973 _remote_headers | server: headers | cli-only: 
src/pmcp/client/manager.py:1156 _connect_all_unlocked | server: name | cli-only: 
src/pmcp/client/manager.py:1191 _connect_singleflight | server: name, status | cli-only: 
src/pmcp/client/manager.py:1264 register_lazy_configs | server: name | cli-only: 
src/pmcp/client/manager.py:1358 connect_server | server: name, status | cli-only: 
src/pmcp/client/manager.py:1505 restart_server | server: name | cli-only: 
src/pmcp/client/manager.py:1526 _connect_with_retry | server: name | cli-only: 
src/pmcp/client/manager.py:2457 _connect_stdio | server: args, command, name, status | cli-only: 
src/pmcp/client/manager.py:2587 _connect_sse | server: name, url | cli-only: 
src/pmcp/client/manager.py:2603 _connect_streamable_http | server: name, url | cli-only: 
src/pmcp/client/manager.py:2800 _connect_remote_stream | server: name, status | cli-only: 
src/pmcp/client/manager.py:3190 _reconnect_loop | server: status | cli-only: 
src/pmcp/client/manager.py:3832 adopt_process | server: status | cli-only: 
src/pmcp/config/loader.py:1022 _merge_manifest_defaults | server: args, command, env_var, extra_env | cli-only: 
src/pmcp/config/loader.py:1100 normalize_server_config | server: command | cli-only: 
src/pmcp/config/loader.py:1401 _coerce_manifest_servers | server: name | cli-only: 
src/pmcp/config/loader.py:1413 _manifest_server_to_config | server: args, authorization_server_metadata_url, client_id_metadata_document_url, command, declared_scopes, env_var, extra_env, headers, name, oidc_discovery_url, oidc_issuer_url, protected_resource_metadata_url, supports_url_elicitation, transport, url | cli-only: 
src/pmcp/config/loader.py:1502 _eager_requires_credential | server: requires_api_key | cli-only: 
src/pmcp/config/loader.py:1530 resolve_startup_configs | server: auto_start, env_var, headers, name | cli-only: 
src/pmcp/config/loader.py:1557 add_config | server: env_var, headers, name | cli-only: 
src/pmcp/identity.py:41 is_self_reference | server: args, command, name | cli-only: 
src/pmcp/identity.py:94 filter_self_references | server: args, command, name | cli-only: 
src/pmcp/manifest/installer.py:590 build_install_child_env | server: env_var, extra_env | cli-only: 
src/pmcp/manifest/installer.py:634 check_api_key | server: env_instructions, env_var | cli-only: 
src/pmcp/manifest/installer.py:662 install_server | server: install, name | cli-only: 
src/pmcp/manifest/installer.py:733 verify_installation | server: args, command, name | cli-only: 
src/pmcp/manifest/installer.py:167 start_install | server: install, name, status | cli-only: 
src/pmcp/manifest/loader.py:312 credential_storage_key | server: env_var, secret_key | cli-only: 
src/pmcp/manifest/loader.py:383 credential_requirement | server: api_key_optional_when, env_var, extra_env, requires_api_key, secret_key, url | cli-only: 
src/pmcp/manifest/loader.py:460 _category_keyword_norms | server: keywords, name | cli-only: 
src/pmcp/manifest/loader.py:752 keyword_weights | server: keywords | cli-only: 
src/pmcp/manifest/loader.py:791 manifest_candidate_fields | server: declared_capabilities, declared_scopes, description, package, server_card_url, transport, url | cli-only: 
src/pmcp/manifest/loader.py:810 cli_hint_fields | server: description, name | cli-only: check_command, examples, help_command, prefer_mcp_for
src/pmcp/manifest/loader.py:880 _canonical_server | server: args, authorization_server_metadata_url, client_id_metadata_document_url, command, declared_capabilities, declared_scopes, env_instructions, env_var, headers, keywords, name, oidc_discovery_url, oidc_issuer_url, package, protected_resource_metadata_url, server_card_url, supports_url_elicitation, url | cli-only: 
src/pmcp/manifest/loader.py:979 _canonical_cli | server: description, name | cli-only: check_command, examples, help_command, prefer_mcp_for
src/pmcp/manifest/loader.py:1317 _materialize_version_pin | server: args, command, env_var, extra_env, install, name, url, version | cli-only: 
src/pmcp/manifest/loader.py:1389 _materialize_version_pin_soft | server: name | cli-only: 
src/pmcp/manifest/loader.py:1935 _build_manifest | server: extra_env | cli-only: 
src/pmcp/manifest/loader.py:623 get_auto_start_servers | server: auto_start | cli-only: 
src/pmcp/manifest/loader.py:731 search_by_keyword | server: keywords | cli-only: 
src/pmcp/manifest/loader.py:1339 refuse | server: name | cli-only: 
src/pmcp/manifest/matcher.py:111 _rank_one_cli | server: description, keywords, name | cli-only: examples, prefer_mcp_for
src/pmcp/manifest/matcher.py:170 rank_cli_hints | server: name | cli-only: 
src/pmcp/manifest/matcher.py:238 _keyword_match | server: keywords, name | cli-only: 
src/pmcp/manifest/refresher.py:237 _identity_env_overlay | server: extra_env | cli-only: 
src/pmcp/manifest/refresher.py:269 refresh_server | server: args, command, description, name, package, version | cli-only: 
src/pmcp/manifest/refresher.py:418 refresh_all | server: args, command, package | cli-only: 
src/pmcp/manifest/refresher.py:527 check_staleness | server: args, command, package, version | cli-only: 
src/pmcp/manifest/refresher.py:460 refresh_target | server: args, command, package | cli-only: 
src/pmcp/manifest/sync.py:62 sync_registry_to_manifest | server: name, package, replacement, status | cli-only: 
src/pmcp/provision_gate.py:166 _config_runs_exactly | server: args, command, install | cli-only: 
src/pmcp/provision_gate.py:253 _manifest_package_names | server: args, command, install, package | cli-only: 
src/pmcp/provision_gate.py:365 _unresolvable_remedy | server: package | cli-only: 
src/pmcp/provision_gate.py:388 _evaluate | server: name | cli-only: 
src/pmcp/provision_gate.py:447 evaluate_provision | server: name | cli-only: 
src/pmcp/server.py:707 initialize | server: name, status | cli-only: 
src/pmcp/server.py:854 _kill_orphan_processes | server: args, command, name | cli-only: 
src/pmcp/tools/handlers.py:264 _refresh_config_unchanged | server: args, command, headers, name, url | cli-only: 
src/pmcp/tools/handlers.py:357 _materialised_pin | server: args, command, version | cli-only: 
src/pmcp/tools/handlers.py:871 _build_cli_probe_configs | server:  | cli-only: check_command, help_command
src/pmcp/tools/handlers.py:996 _auth_metadata_for_server | server: authorization_server_metadata_url, client_id_metadata_document_url, declared_scopes, oidc_discovery_url, oidc_issuer_url, protected_resource_metadata_url | cli-only: 
src/pmcp/tools/handlers.py:1156 _manifest_candidates_for_query | server: keywords, name, status | cli-only: 
src/pmcp/tools/handlers.py:1928 refresh | server: name, status | cli-only: 
src/pmcp/tools/handlers.py:2357 config_status | server: name, status | cli-only: 
src/pmcp/tools/handlers.py:2450 get_startup_policy | server: name | cli-only: 
src/pmcp/tools/handlers.py:2720 _configured_duplicate_missing_credential | server: env_var, requires_api_key | cli-only: 
src/pmcp/tools/handlers.py:2907 _load_configured_servers | server: name | cli-only: 
src/pmcp/tools/handlers.py:2921 _load_all_configured_servers | server: name | cli-only: 
src/pmcp/tools/handlers.py:2929 _status_value | server: status | cli-only: 
src/pmcp/tools/handlers.py:2995 _missing_remote_header_env_vars | server: headers | cli-only: 
src/pmcp/tools/handlers.py:3007 _remote_header_missing_lifecycle_output | server: name | cli-only: 
src/pmcp/tools/handlers.py:3048 _resolve_lifecycle_target | server: env_var | cli-only: 
src/pmcp/tools/handlers.py:3264 _keywords_for_config_server | server: args, command, name, url | cli-only: 
src/pmcp/tools/handlers.py:3301 _build_manifest_with_config_servers | server: args, command, headers, url, version | cli-only: 
src/pmcp/tools/handlers.py:3354 _get_server_env_metadata | server: env_instructions, env_var, requires_api_key | cli-only: 
src/pmcp/tools/handlers.py:3596 request_capability | server: description, env_var, keywords, name, requires_api_key, status | cli-only: check_command, examples, help_command, prefer_mcp_for
src/pmcp/tools/handlers.py:3979 provision | server: env_instructions, env_var, url | cli-only: 
src/pmcp/tools/handlers.py:4441 auth_connect | server: env_var | cli-only: 
src/pmcp/tools/handlers.py:4975 update_server | server: args, command, description, package, source, version | cli-only: 
src/pmcp/tools/handlers.py:5499 register_discovered_server | server: description, name, package | cli-only: 
src/pmcp/tools/handlers.py:5761 _finalize_server_ready | server: env_var, status | cli-only: 
86 candidate consumer functions
````

### `aggr.txt` (rev 4: every aggregation over a manifest collection, derived)

Produced by `grep -rnE "(\.servers|manifest_servers|merged_servers|_CATEGORY_MAP|cli_alternatives|manifest_by_name)(\.(values|items|keys)\(\))?" src/pmcp --include='*.py' | grep -E "for |sum\(|len\(|set\(|\{ *[a-z_]+ *[:f]"`,
plus `keyword_weights`' own loop (`grep -n 'for server in servers' src/pmcp/manifest/loader.py`):

````text
src/pmcp/config/loader.py:1410:    return {server.name: server for server in manifest_servers}
src/pmcp/config/loader.py:1662:    for name, server in manifest_by_name.items():
src/pmcp/config/loader.py:1696:    known_names = configured_names | set(manifest_by_name)
src/pmcp/cli.py:931:        print(f"\nRefreshed {len(cache.servers)} servers:")
src/pmcp/cli.py:932:        for name, desc in cache.servers.items():
src/pmcp/tools/handlers.py:877:            for name, cli in manifest.cli_alternatives.items()
src/pmcp/tools/handlers.py:1181:        for name, server in manifest.servers.items():
src/pmcp/tools/handlers.py:2388:            server.name: server for server in (await self.health()).servers
src/pmcp/tools/handlers.py:2821:        for entry in (await self._load_registry_candidates()).servers:
src/pmcp/tools/handlers.py:3668:        for n in merged_manifest.servers:
src/pmcp/summary/generator.py:47:    missing = [s for s in server_names if s not in cache.servers]
src/pmcp/manifest/loader.py:488:    for cat_name, server_names in _CATEGORY_MAP.items():
src/pmcp/manifest/loader.py:625:        return [s for s in self.servers.values() if s.auto_start]
src/pmcp/manifest/loader.py:637:        total = len(self.servers)
src/pmcp/manifest/loader.py:642:        for cat_name, server_names in _CATEGORY_MAP.items():
src/pmcp/manifest/loader.py:643:            matched = [n for n in server_names if n in self.servers]
src/pmcp/manifest/loader.py:703:        for cat_name, server_names in _CATEGORY_MAP.items():
src/pmcp/manifest/loader.py:727:            self.servers[n] for n in _CATEGORY_MAP[best_cat] if n in self.servers
src/pmcp/manifest/loader.py:739:            for cli in self.cli_alternatives.values()
src/pmcp/manifest/loader.py:745:            for server in self.servers.values()
src/pmcp/manifest/loader.py:1951:    for name, cli_data in data.get("cli_alternatives", {}).items():
src/pmcp/manifest/loader.py:2068:        f"Loaded manifest: {len(cli_alternatives)} CLI alternatives, "
src/pmcp/manifest/sync.py:39:        _server_identity(entry): entry for entry in base.servers
src/pmcp/manifest/sync.py:41:    for entry in delta.servers:
src/pmcp/manifest/sync.py:72:        _norm(name): server for name, server in manifest.servers.items()
src/pmcp/manifest/sync.py:76:        for server in manifest.servers.values()
src/pmcp/manifest/sync.py:80:    for entry in registry.servers:
src/pmcp/manifest/sync.py:108:        for server in manifest.servers.values():
src/pmcp/manifest/refresher.py:127:            for name, desc in cache.servers.items()
src/pmcp/manifest/refresher.py:547:    for name, desc in existing_cache.servers.items():
src/pmcp/manifest/matcher.py:189:    for name, cli in manifest.cli_alternatives.items():
src/pmcp/manifest/matcher.py:263:    for name, server in manifest.servers.items():
src/pmcp/cli_commands/secrets.py:169:    for server in manifest_servers:
src/pmcp/server.py:717:                f"Loaded pre-built descriptions for {len(self._descriptions_cache.servers)} servers"
src/pmcp/server.py:844:                    f"Cached descriptions for {len(self._descriptions_cache.servers)} servers"
src/pmcp/policy/policy.py:642:        if set(self._policy.servers.allowlist) != {"firecrawl", "brightdata"}:
src/pmcp/manifest/loader.py:762:    for server in servers:
````

### `falsifiers.py` (rev 4: round 3's falsifiers, main vs spike)

````python
"""Round-3 falsifiers (grok F001, codex F051) and claude F1, run in-process.

Run from a scratch cwd with HOME set to an empty scratch home.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock

logging.disable(logging.CRITICAL)
from pmcp.manifest.loader import clear_manifest_cache  # noqa: E402
from pmcp.tools.handlers import GatewayTools  # noqa: E402

home = Path(os.environ["HOME"])
overlay = home / ".pmcp" / "manifest.yaml"


def ask(query):
    clear_manifest_cache()
    client = MagicMock()
    client.get_all_server_statuses.return_value = []
    policy = MagicMock()
    policy.is_server_allowed.return_value = True
    tools = GatewayTools(client_manager=client, policy_manager=policy, project_root=Path.cwd())
    r = asyncio.run(tools.request_capability({"query": query, "available_clis": []}))
    return r.status, sorted(c.name for c in r.candidates or [])


overlay.unlink(missing_ok=True)
print("F001 before:", ask("markdown"))
overlay.parent.mkdir(parents=True, exist_ok=True)
overlay.write_text("servers:\n  playwright:\n    description: x\n    command: npx\n    keywords: [markdown]\n")
print("F001 after :", ask("markdown"))

from pmcp.config.loader import load_configs  # noqa: E402

overlay.write_text("servers:\n  remote-bad:\n    url: https://example.invalid/mcp\n    command: npx\n    args: 5\n")
cfg = home / ".mcp.json"
cfg.write_text(json.dumps({"mcpServers": {"remote-bad": {"args": []}, "healthy": {"command": "healthy-command"}}}))
clear_manifest_cache()
try:
    names = {c.name for c in load_configs(project_root=Path.cwd(), user_config_paths=[cfg])}
    print("F051 load_configs:", sorted(names))
except Exception as e:
    print("F051 load_configs RAISES", type(e).__name__, e)

from pmcp.cli_commands.secrets import _extract_required_keys  # noqa: E402

overlay.write_text(
    "servers:\n  aaa-bad:\n    command: npx\n    headers: 5\n"
    "  zzz-good:\n    url: https://example.invalid/mcp\n    oidc_issuer_url: https://issuer.invalid\n"
)
cfg.write_text(json.dumps({"mcpServers": {}}))
clear_manifest_cache()
_, _, auth, _ = _extract_required_keys(Path.cwd())
print("F1 secrets: zzz-good metadata present:", "zzz-good" in auth)
overlay.unlink()
cfg.unlink()
````

### `derive5.txt` (rev 5: YAML sites, exception-logging sites, generic readers)

````text
# YAML parse and YAML-error handler sites in src/pmcp (grep -rnE "YAMLError|safe_load|load_yaml|yaml\.load|_parse_trusted\b|MarkedYAMLError")
src/pmcp/config/guidance.py:170:            data = yaml.safe_load(f)
src/pmcp/config/guidance.py:235:            loaded = yaml.safe_load(config_path.read_text())
src/pmcp/config/guidance.py:275:            loaded = yaml.safe_load(config_path.read_text())
src/pmcp/manifest/loader.py:47:def _parse_trusted_yaml(content: bytes) -> Any:
src/pmcp/manifest/loader.py:49:    return yaml.load(content, Loader=_trusted_yaml_loader())  # noqa: S506 - safe loaders only
src/pmcp/manifest/loader.py:58:def _parse_trusted_document(content: bytes) -> Any:
src/pmcp/manifest/loader.py:59:    """``_parse_trusted_yaml(content)``, parsed once per distinct ``content``."""
src/pmcp/manifest/loader.py:67:    data = _parse_trusted_yaml(content)
src/pmcp/manifest/loader.py:84:        servers = (_parse_trusted_yaml(path.read_bytes()) or {}).get("servers") or {}
src/pmcp/manifest/loader.py:85:    except (OSError, yaml.YAMLError, AttributeError):
src/pmcp/manifest/loader.py:1030:        data = _parse_trusted_yaml(_SHIPPED_MANIFEST_PATH.read_bytes()) or {}
src/pmcp/manifest/loader.py:1032:    except (OSError, yaml.YAMLError, AttributeError):
src/pmcp/manifest/loader.py:1610:        data = yaml.safe_load(content)
src/pmcp/manifest/loader.py:1611:    except yaml.YAMLError as exc:
src/pmcp/manifest/loader.py:1937:    data = _parse_trusted_document(base) if trusted else yaml.safe_load(base)
src/pmcp/manifest/code_patterns_loader.py:43:                data = yaml.safe_load(f)
src/pmcp/manifest/refresher.py:65:            data = yaml.safe_load(f)
src/pmcp/policy/policy.py:405:        and an empty YAML file: ``yaml.safe_load`` returns ``list``, ``str`` and
src/pmcp/policy/policy.py:416:                data = yaml.safe_load(content)
src/pmcp/templates/code_snippets_loader.py:47:                data = yaml.safe_load(f)

# logger calls that interpolate an exception, in the manifest/overlay loaders and the load_manifest catch sites
src/pmcp/config/loader.py:282:        logger.warning(f"Failed to parse config file {file_path}: {e}")
src/pmcp/config/loader.py:319:        logger.warning(f"Failed to parse config file {file_path}: {e}")
src/pmcp/server.py:483:                logger.error(f"Tool execution error: {e}")
src/pmcp/server.py:847:                logger.warning(f"Failed to auto-generate cache: {e}")
src/pmcp/server.py:1013:            logger.error(f"Error during shutdown: {e}")
src/pmcp/tools/handlers.py:1058:            logger.warning(f"Could not load provisioned registry: {e}")
src/pmcp/tools/handlers.py:1069:            logger.warning(f"Could not save provisioned registry: {e}")
src/pmcp/tools/handlers.py:1964:                logger.warning(f"Failed to restore provisioned servers: {e}")
src/pmcp/tools/handlers.py:4342:                logger.error(f"Failed to connect remote server {server_name}: {e}")
src/pmcp/tools/handlers.py:4424:            logger.error(f"Failed to start provisioning {server_name}: {e}")
src/pmcp/tools/handlers.py:5750:            logger.error(f"provision_status handler failed: {e}", exc_info=True)
src/pmcp/tools/handlers.py:5844:            logger.error(f"Handoff failed for {job_server_name}: {e}", exc_info=True)
src/pmcp/tools/handlers.py:5890:            logger.error(f"Failed to refresh after install: {e}")

# generic field readers (claude N3): asdict( vars( __dict__ fields( model_dump
src/pmcp/auth.py:150:        for name, value in vars(AuthMessage).items()
src/pmcp/cli.py:1949:            "diagnostics": [d.model_dump() for d in policy.diagnostics],
src/pmcp/tools/handlers.py:2995:    def _missing_remote_header_env_vars(
src/pmcp/tools/handlers.py:3083:            missing_env_vars = self._missing_remote_header_env_vars(configured)
src/pmcp/tools/handlers.py:3223:            missing_env_vars = self._missing_remote_header_env_vars(resolved)
src/pmcp/tools/handlers.py:3548:                    event.model_dump(mode="json", exclude_none=True)
src/pmcp/tools/handlers.py:4027:            missing_env_vars = self._missing_remote_header_env_vars(configured)
src/pmcp/tools/handlers.py:4211:                missing_env_vars = self._missing_remote_header_env_vars(resolved_config)
src/pmcp/tools/handlers.py:6242:        task_data = task.model_dump(mode="json")
src/pmcp/remote_auth.py:37:def collect_remote_header_env_vars(headers: Mapping[str, str] | None) -> list[str]:
src/pmcp/transport/http.py:508:                "gateway_diagnostics": diagnostics.model_dump(exclude_none=True),
src/pmcp/manifest/loader.py:961:            inherited.model_dump(warnings=False)
src/pmcp/manifest/registry.py:713:    payload = json.dumps(asdict(cache), indent=2, sort_keys=True) + "\n"
src/pmcp/cli_commands/secrets.py:59:    return metadata, set(collect_remote_header_env_vars(server.headers))
src/pmcp/cli_commands/secrets.py:87:            remote_header_keys = set(collect_remote_header_env_vars(cfg.config.headers))
src/pmcp/client/manager.py:1004:    return parsed.model_dump(exclude_none=True)
src/pmcp/client/manager.py:1595:        `model_dump()` in python mode, not `mode="json"`: every field here was
src/pmcp/client/manager.py:1602:                k: v.model_dump()
src/pmcp/client/manager.py:1607:                k: v.model_dump()
src/pmcp/client/manager.py:1612:                k: v.model_dump()
src/pmcp/client/manager.py:3281:                    payload = message.message.model_dump(
src/pmcp/client/manager.py:4053:                all_tasks.append(record.model_dump())
src/pmcp/server.py:451:                    result = result.model_dump()
````

### `leakfind.py` (rev 5: attribute an orphaned asyncio task to the test that leaked it)

````python
"""pytest plugin: attribute every orphaned asyncio task exception to the test
that leaked it, independent of GC timing.

After each test's teardown, force gc.collect(). A Task whose exception was
never retrieved logs "Task exception was never retrieved" from Task.__del__
during that collection, so the line is attributed to the test that just
finished (the leaker), not to whichever later test happened to trigger GC.
Results go to $LEAKFIND_OUT (one JSON line per leak).
"""

import gc
import json
import logging
import os

_OUT = os.environ.get("LEAKFIND_OUT", "leakfind.jsonl")


class _Grab(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


_grab = _Grab()
logging.getLogger("asyncio").addHandler(_grab)


def pytest_runtest_teardown(item, nextitem):  # noqa: ARG001
    pass


def pytest_runtest_logfinish(nodeid, location):  # noqa: ARG001
    _grab.records.clear()
    gc.collect()
    gc.collect()
    for record in _grab.records:
        msg = record.getMessage()
        if "never retrieved" in msg:
            detail = ""
            if record.exc_info and record.exc_info[1] is not None:
                detail = repr(record.exc_info[1])[:200]
            with open(_OUT, "a") as fh:
                fh.write(
                    json.dumps(
                        {"leaker": nodeid, "message": msg[:300], "exc": detail}
                    )
                    + "\n"
                )
    _grab.records.clear()
````

### `flake_runs.sh`, `flake_runs2.sh`, `leak_runs.sh` (rev 5: the full-suite failure)

````bash
#!/usr/bin/env bash
# Four full-suite runs in the scratch worktree: rev-4 patch x2, then main bb6ddde x2.
set -u
R=$WORKTREE_ROOT/pmcp-342-repro
W=$WORKTREE_ROOT/pmcp-342-flake
B=/var/tmp/pmcp-342-bt-viperjuice
cd "$W" || exit 1
run() {
  local label=$1
  env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
    "$W/.venv/bin/python" -m pytest tests/ -q --tb=short --cov --cov-report= -p no:cacheprovider \
    --basetemp="$B/flake-$label" > "$R/flake-$label.log" 2>&1
  echo "$label: $(tail -1 "$R/flake-$label.log")" >> "$R/flake-summary.txt"
}
: > "$R/flake-summary.txt"
git apply "$R/final4/src.patch" && git apply "$R/final4/migration.patch" \
  && cp "$R/final4/test_catalog_overlay_discovery.py" tests/ || exit 2
run patch1
run patch2
git apply -R "$R/final4/migration.patch" && git apply -R "$R/final4/src.patch" \
  && rm tests/test_catalog_overlay_discovery.py || exit 3
echo "tree: $(git status --short | wc -l) changes" >> "$R/flake-summary.txt"
run main1
run main2
echo DONE >> "$R/flake-summary.txt"

#!/usr/bin/env bash
# Resume: patch2 (the first attempt died at 13% when /mnt/workspace hit 0 bytes free), then main x2.
set -u
R=$WORKTREE_ROOT/pmcp-342-repro
W=$WORKTREE_ROOT/pmcp-342-flake
B=/var/tmp/pmcp-342-bt-viperjuice
cd "$W" || exit 1
space_ok() {
  local avail
  avail=$(df --output=avail -k /mnt/workspace | tail -1)
  [ "$avail" -gt 307200 ] || { echo "$1: SKIPPED, only ${avail} KiB free on /mnt/workspace" >> "$R/flake-summary.txt"; return 1; }
}
run() {
  local label=$1
  space_ok "$label" || return 1
  rm -rf "$B/flake-$label"
  env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
    "$W/.venv/bin/python" -m pytest tests/ -q --tb=short --cov --cov-report= -p no:cacheprovider \
    --basetemp="$B/flake-$label" > "$R/flake-$label.log" 2>&1
  echo "$label: $(tail -1 "$R/flake-$label.log") [free $(df --output=avail -h /mnt/workspace | tail -1)]" >> "$R/flake-summary.txt"
}
echo "--- resumed $(date +%T)" >> "$R/flake-summary.txt"
run patch2
space_ok reverse || exit 4
git apply -R "$R/final4/migration.patch" && git apply -R "$R/final4/src.patch" \
  && rm tests/test_catalog_overlay_discovery.py || exit 3
echo "tree after reverse: $(git status --short | wc -l) changes" >> "$R/flake-summary.txt"
run main1
run main2
echo DONE >> "$R/flake-summary.txt"

#!/usr/bin/env bash
# Attribute every orphaned asyncio task exception to the test that leaked it
# (gc.collect after each test, plug/leakfind.py), on main bb6ddde and on rev 4.
# Only the test files that run before test_project_source_consent_config.py,
# plus that file, in collection order.
set -u
R=$WORKTREE_ROOT/pmcp-342-repro
W=$WORKTREE_ROOT/pmcp-342-flake
B=/var/tmp/pmcp-342-bt-viperjuice
cd "$W" || exit 1
run() {
  local label=$1
  rm -f "$R/leak-$label.jsonl"
  LEAKFIND_OUT="$R/leak-$label.jsonl" PYTHONPATH="$R/plug" \
    env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
    "$W/.venv/bin/python" -m pytest tests/ -q --tb=no -p no:cacheprovider -p leakfind \
    --no-cov --cov-fail-under=0 --basetemp="$B/leak-$label" \
    > "$R/leak-$label.log" 2>&1
  echo "$label: $(tail -1 "$R/leak-$label.log") leaks=$(wc -l < "$R/leak-$label.jsonl" 2>/dev/null || echo 0)" >> "$R/leak-summary.txt"
}
: > "$R/leak-summary.txt"
run main
git apply "$R/final4/src.patch" && git apply "$R/final4/migration.patch" \
  && cp "$R/final4/test_catalog_overlay_discovery.py" tests/ || exit 2
run patch
git apply -R "$R/final4/migration.patch" && git apply -R "$R/final4/src.patch" \
  && rm tests/test_catalog_overlay_discovery.py || exit 3
echo "tree after: $(git status --short | wc -l) changes" >> "$R/leak-summary.txt"
echo DONE >> "$R/leak-summary.txt"
````

### `gcatcall.py` (rev 5: the minimal reproducing order on main)

````python
"""pytest plugin: run gc.collect() at the start of every test's call phase,
inside the test's own log capture, so an orphaned task freed by an earlier test
is reported in the next test that captures logs (deterministic stand-in for
the garbage collector happening to run there)."""

import gc

import pytest


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_call(item):  # noqa: ARG001
    gc.collect()
    yield
````

### `mutants.py` (mutation driver, revision 6)

````python
"""Mutation driver for the Consiliency/pmcp#342 spike (revision 2).

Each mutant is an exact-text replacement that must match exactly once. The file
is restored from a saved copy (never git) and the restore is cmp-checked.

Each run stops at the first failure (-x, rev 4: the module takes ~200 s).
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
SECRETS = "src/pmcp/cli_commands/secrets.py"
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
    ("M5", "R2: overlay servers neither gated nor checked (raw parse kept)", LOADER,
     "                server = _canonical_server(\n                    _parse_server_config(\n                        name, _schema_fields(server_data, _SERVER_SCHEMA_KEYS)\n                    )\n                )\n",
     "                server = _parse_server_config(name, server_data)\n", TESTS),
    ("M7", "R2: the CapabilityCandidate is built without validation", LOADER,
     "    candidate = CapabilityCandidate(\n", "    candidate = CapabilityCandidate.model_construct(\n", TESTS),
    ("M8", "R2: check skips the startup/refresh conversion", LOADER,
     "    # below would read a `str` args as characters, which startup would not.\n    _manifest_server_to_config(canonical, lambda _key: None)\n",
     "    # below would read a `str` args as characters, which startup would not.\n", TESTS),
    ("M9", "R2: overlay CLI alternatives neither gated nor checked", LOADER,
     "                cli = _canonical_cli(\n                    _parse_cli_alternative(\n                        name, _schema_fields(cli_data, _CLI_SCHEMA_KEYS)\n                    )\n                )\n",
     "                cli = _parse_cli_alternative(name, cli_data)\n", TESTS),
    ("M12", "R2: CLI check accepts an empty check_command (probe needs slot 0)", LOADER,
     "    if not hint.check_command:\n", "    if False:\n", TESTS),
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
     "    match = _rank_one_cli(\n", "    match = (lambda *a, **k: type(\"M\", (), {\"hint\": cli})())(\n", TESTS),
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

    # --- revision 4 ---
    ("C1", "stats: the category tier counts the merged manifest", LOADER,
     "            if self.base_category_keywords is not None\n",
     "            if False\n", TESTS),
    ("C2", "stats: the config-server view drops the base category statistics", HANDLERS,
     "            base_category_keywords=manifest.base_category_keywords,\n", "", TESTS),
    ("C3", "stats: the name index lets an aliasing overlay name take a base slot", HANDLERS,
     "                norm_to_server.setdefault(normalized_server_name(n), n)\n",
     "                norm_to_server[normalized_server_name(n)] = n\n", TESTS),
    ("F1", "fields: check skips configured-default inheritance", LOADER,
     "    inherited = _merge_manifest_defaults(\n", "    inherited = None and _merge_manifest_defaults(\n", TESTS),
    ("F2", "fields: the auth view skips `headers` (pmcp secrets reads them on every entry)", LOADER,
     "        headers=server.headers,\n", "        headers=None,\n", TESTS),
    ("F3", "guard: load_configs lets one entry's inheritance abort every config", CONFIG,
     "            except Exception as exc:\n                logger.warning(\n                    f\"Configured server '{name}': ignoring its manifest defaults \"\n",
     "            " + GUARD_EXC + "\n                logger.warning(\n                    f\"Configured server '{name}': ignoring its manifest defaults \"\n", TESTS),
    ("F4", "guard: pmcp secrets lets one entry drop every later server", SECRETS,
     "        except Exception:\n            continue\n",
     "        except ZeroDivisionError:\n            continue\n", TESTS),
    ("F5", "logs: the inheritance check lets pydantic's warning quote a value", LOADER,
     "inherited.model_dump(warnings=False)", "inherited.model_dump()", TESTS),

    # --- revision 5 ---
    ("Y1", "yaml: the overlay parse warning quotes the YAML error (values reach the log)", LOADER,
     "{path}: {yaml_error_text(exc)}", "{path}: {exc}", TESTS),
    ("Y2", "yaml: gateway.refresh logs a manifest-load error's text", HANDLERS,
     "f\"Failed to load manifest startup configs: {type(e).__name__}\"",
     "f\"Failed to load manifest startup configs: {e}\"", TESTS),
    ("Y3", "yaml: load_configs logs a manifest-load error's text", CONFIG,
     "f\"Manifest defaults unavailable during config load: {type(e).__name__}\"",
     "f\"Manifest defaults unavailable during config load: {e}\"", TESTS),
    ("P1", "parse: the kept server is the raw parse, not the canonical one", LOADER,
     "                server = _canonical_server(\n                    _parse_server_config(\n                        name, _schema_fields(server_data, _SERVER_SCHEMA_KEYS)\n                    )\n                )\n",
     "                server = _parse_server_config(\n                    name, _schema_fields(server_data, _SERVER_SCHEMA_KEYS)\n                )\n                _canonical_server(server)\n", TESTS),
    ("P2", "parse: server schema fields are not required to be JSON-native", LOADER,
     "                        name, _schema_fields(server_data, _SERVER_SCHEMA_KEYS)\n",
     "                        name, server_data\n", TESTS),
    ("P3", "parse: CLI schema fields are not required to be JSON-native", LOADER,
     "                        name, _schema_fields(cli_data, _CLI_SCHEMA_KEYS)\n",
     "                        name, cli_data\n", TESTS),
    ("P4", "parse: the auth view's validated value is not kept", LOADER,
     "        supports_url_elicitation=auth_view.supports_url_elicitation,\n",
     "        supports_url_elicitation=server.supports_url_elicitation,\n", TESTS),
    ("P5", "parse: a local entry's metadata URLs are not checked (claude N1)", LOADER,
     "        oidc_issuer_url=server.oidc_issuer_url,\n        oidc_discovery_url=server.oidc_discovery_url,\n        client_id_metadata_document_url=server.client_id_metadata_document_url,\n        declared_scopes=server.declared_scopes,\n        supports_url_elicitation=server.supports_url_elicitation,\n    )\n",
     "        oidc_issuer_url=None,\n        oidc_discovery_url=server.oidc_discovery_url,\n        client_id_metadata_document_url=server.client_id_metadata_document_url,\n        declared_scopes=server.declared_scopes,\n        supports_url_elicitation=server.supports_url_elicitation,\n    )\n", TESTS),
    ("P6", "parse: inheritance's converted command/args are not kept", LOADER,
     "        canonical = replace(canonical, command=local.command, args=list(local.args))\n",
     "        pass\n", TESTS),
    ("K1", "cli: an overlay CLI takes the shipped-CLI tier (outranks the name and category tiers)", HANDLERS,
     "                and match.hint.name not in manifest.overlay_cli_names\n", "", TESTS),
    ("K2", "cli: overlay CLIs are not recorded as overlay CLIs", LOADER,
     "            overlay_cli_names.update(overlay_clis)\n", "", TESTS),

    # --- revision 6 ---
    ("K3", "cli: the overlay-CLI fallback after every server tier is removed", HANDLERS,
     "        if overlay_cli_match is not None:\n            return self._use_cli_resolution(overlay_cli_match.hint)\n", "", TESTS),
    ("K4", "cli: match_capability lets an overlay CLI outrank a server", MATCHER,
     "        if match.hint.name in overlay_cli_names:\n            continue\n", "", TESTS),
    ("J1", "schema: unknown keys are checked (main ignores them)", LOADER,
     "    for field_name in sorted(schema):\n        if field_name not in data:\n",
     "    for field_name in sorted(set(schema) | {k for k in data if isinstance(k, str)}):\n        if field_name not in data:\n", TESTS),
    ("J2", "schema: a rejection quotes the value", LOADER,
     "raise _EntryRejected(f\"'{field_name}' holds a value that is not JSON\")",
     "raise _EntryRejected(f\"'{field_name}' holds {value!r}\")", TESTS),
    ("J3", "schema: a stored-only field's YAML value costs the entry", LOADER,
     "        elif field_name in _STORED_ONLY_KEYS:\n", "        elif False:\n", TESTS),
    ("E1", "yaml: only YAMLError is contained at the overlay parse boundary", LOADER,
     "        data = yaml.safe_load(content)\n    except Exception as exc:\n",
     "        data = yaml.safe_load(content)\n    except yaml.YAMLError as exc:\n", TESTS),

]

env = dict(os.environ)
for k in ("npm_config_cache", "npm_config_store_dir", "pnpm_config_store_dir"):
    env.pop(k, None)
bt = Path(os.environ.get("PMCP342_MUT_BASETEMP", "/var/tmp/pmcp-342-bt-viperjuice/mut"))
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
             "--no-cov", "--cov-fail-under=0", "--tb=line", "-x", f"--basetemp={bt}/{mid}"],
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
