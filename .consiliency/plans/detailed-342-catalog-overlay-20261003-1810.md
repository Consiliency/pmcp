# Detailed plan: an overlay never hides another server from discovery — weights from the base manifest, and a malformed entry stays inside its entry (Consiliency/pmcp#342)

> Written on main `c9206a9` (dev0, a team host), worktree `pmcp-342`, branch
> `plan/342-catalog-overlay`. Every number below was measured this session on that tree,
> on the commit before the manifest cache (`a622ec5`), or on the spike of this plan applied
> to `c9206a9`. The spike was then removed, and this PR carries only this file.
>
> **Bounded-plan verdict: within threshold.** Three source files change:
> `src/pmcp/manifest/loader.py` (+95/−2), `src/pmcp/manifest/matcher.py` (+15/−9) and
> `src/pmcp/tools/handlers.py` (+1). One test module is new
> (`tests/test_catalog_overlay_discovery.py`, 34 tests). No existing test changes. The docs
> change is one CHANGELOG bullet and one README sentence.

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

### Root cause R2: an unvalidated overlay entry crashes discovery for every server

The issue's "none at all" has a second, independent way to happen. `_parse_server_config`
(`loader.py:996`) copies fields without type checks. The overlay parser's per-entry
`try/except` (`loader.py:1218`) promises "one malformed entry is skipped without dropping
siblings". But a wrong type only fails later, in consumers that iterate every server.
Probe (`probe.py`): a user overlay with a bad entry `x1` and a good sibling `goodone`
(`yyq`), via `GatewayTools.catalog_search`.

| bad entry | main: `zzq` (bad's keyword) | main: `yyq` (sibling) | main: `screenshot` (shipped) | spike (all three) |
|---|---|---|---|---|
| `keywords: null` / `[1, 2]` / `[["a"]]` | **raises** | **raises** | **raises** | entry skipped; `[]`, `[goodone]`, `[playwright]` |
| server name `123` / `true` | **raises** | **raises** | **raises** | same |
| `description: null` / `5` | **raises** (`ValidationError`) | ok | ok | same |
| `declared_scopes: null`, `declared_capabilities: "x"` | **raises** | ok | ok | same |
| `url` / `package` / `server_card_url: 5` | **raises** | ok | ok | same |
| `env_var` / `secret_key: 5` | **raises** (`TypeError` in `os.environ`) | ok | ok | same |
| `env_instructions: 5` | **raises** | ok | ok | same |
| `keywords: "zzq, foo"` (a string) | `[]` (iterated as characters) | ok | ok | entry skipped |
| `transport: carrier-pigeon`, `args: null`, `requires_api_key: "no"` | ok | ok | ok | unchanged (accepted) |

The same project overlay with `keywords: null` behind an approval makes every
`catalog_search` raise. A raised tool call reaches an MCP client as an error result, which
reads as "no candidates". `load_manifest()` itself returned normally in every row, so the
call sites that wrap it in `try/except` and degrade silently cannot fire on either cause:
`server.py:736`, `config/loader.py:1149` and `cli.py:1647`.

### Every consumer of the merged manifest, included or ruled out (measured)

`consumers.py` takes the user and project overlays above and snapshots each consumer with
the project overlay approved and then revoked, through the real CLI. Main `c9206a9` and the
spike were run back to back.

| consumer | how it reads the manifest | main approved vs revoked | verdict |
|---|---|---|---|
| `catalog_search` manifest candidates (`handlers.py:1121`) | corpus-weighted keyword score over all servers | `demo` and `screenshot` lose every candidate | **R1, R2: fixed** |
| `match_capability` / `_keyword_match` (`matcher.py:202`; exported from `pmcp.manifest`, no in-tree caller) | same weights | `demo → None`, `screenshot → None` (were `useronly`, `playwright`) | **R1: fixed** (same function) |
| `request_capability` (`handlers.py:3560`): tier 1 name match, tier 2 `get_servers_in_category` | names; category IDF over the fixed `_CATEGORY_MAP` names only | identical for all 9 queries (`demo`, `screenshot`, `database sql`, `web scraping`, `payments`, …) | ruled out. An overlay can change a category only by replacing a mapped name, which is operator intent. The base weights are carried into its merged view (`_build_manifest_with_config_servers`) so a later scorer cannot regress |
| `describe`, `provision`, `auth_connect`, `refresh`, `update_server` / `pmcp update` (`_get_server_config_for_update`), `_resolve_lifecycle_target`, `_finalize_server_ready`, `sync_environment`, `_materialised_pin`, `_auth_env_options` | `get_server(name)` | `get_server` differs for **0** shared names. The only difference is `projonly` itself | ruled out (name lookup) |
| `config_status`, `get_startup_policy`, `pmcp config`, `load_configs` defaults, gateway startup (`server.py:736`), `pmcp init`, `secrets`, `manifest/sync.py`, refresher | name sets, or per-name fields | same name set ± `projonly`; `load_manifest` never raised in any probe row | ruled out |
| `gateway.health` | client-manager statuses, not the manifest | — | ruled out |
| `Manifest.search_by_keyword` | substring over keywords | no in-tree caller. With R2, `keywords` is always a list of str | ruled out; R2 covers its crash |

### The test surface this touches

- `tests/test_manifest.py`'s IDF tests use hand-built `Manifest`s and pass unchanged:
  `test_keyword_match_generic_api_alone_stays_below_threshold`,
  `…_duplicate_server_keywords_do_not_dilute_threshold` and the real-manifest keyword table.
  A hand-built manifest has no base (`base_keyword_weights is None`), so it is weighted by
  its own servers as before.
- `tests/test_manifest_cache.py` (47) passes. `Manifest` gains one defaulted field, which
  pickles with the cached result. Its equality, cache-sharing and no-shared-mutation tests
  are unchanged.
- `tests/test_offline_discovery.py`'s Consiliency/pmcp#78 candidate tests pass unchanged.
  Shipped-only weights equal main's.
- Touched-module subset (14 files, spike): **829 passed, 1 failed, 1 skipped**. The failure
  is `tests/test_manifest.py::TestMonitorInstall::test_monitor_reads_stderr`, which runs an
  install subprocess and is unrelated to manifest parsing. It passed 3 of 3 alone on the
  spike and 2 of 2 on main. It is load-sensitive: the load average on dev0 was 9–11 on 24
  cores.

### Cost

`load_manifest()` cache hit, median of 200 calls, 3 rounds, alternating:
- main: 0.93–1.03 ms;
- spike: 0.95–1.02 ms.

The weights dict (569 keys) adds about 9 KB to a 90 KB pickle. No measurable difference.

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

### D4. R2: reject a wrongly typed entry at parse time, field list bounded by the measured consumers

`_parse_server_config` raises `ValueError` when:
- the server name is not a non-empty `str`;
- `description` is not a `str`;
- `keywords`, `declared_scopes` or `declared_capabilities` is present and not a `list` of
  `str`, explicit `null` included;
- `env_var`, `secret_key`, `env_instructions`, `url`, `package` or `server_card_url` is
  neither `str` nor absent/`null`.

The overlay parser's existing per-entry `try/except` turns the error into "Skipping invalid
server entry … in overlay …", and the siblings and shipped servers load.

- **Why reject the entry, not coerce or drop the field:** a server with silently changed
  discovery metadata is worse than one that is plainly skipped with a warning naming the
  field. `extra_env` coerces scalars because ports are naturally unquoted; keywords and
  URLs have no such idiom, and `yes` coerced to `"True"` would be wrong. An overlay entry
  that *replaces* a shipped name and is malformed is skipped, so the shipped entry stays.
  That is the existing behaviour for any skipped entry.
- **Why this field list:** each field is one that `catalog_search` reads off every server
  (name, keywords) or copies into the typed `CapabilityCandidate` (the rest), and each
  crashed it in the probe. Fields that did not crash discovery stay as they are:
  `transport`, `args`, `requires_api_key`, `install`, `headers`, `command`. A bad `args`
  breaks only provisioning *that* server, which is per-entry by nature (Non-goals).
- **No value in any message.** The `ValueError` names the field and the offending
  *type* only. Overlay names and values are operator free text, and `_server_label`
  already treats an overlay key as possibly a pasted token. M12 is red when the message
  echoes the value.
- **The shipped manifest runs through the same checks.** Its parse has no per-entry
  `try/except` (`loader.py:1526`), so a future shipped typo fails loudly at import-time
  tests rather than at a user's query. All 107 shipped entries pass.

### D5. No change to consent, the cache key or warnings

The defect is downstream of the gate. The approved bytes are parsed and merged correctly.
The cache key is unchanged: the weights are a pure function of the base bytes already in
the key. Warnings are unchanged, apart from the new skip reason for a malformed entry,
which uses the existing once-per-transition logging.

### D6. No-query behaviour is unchanged

`catalog_search` without a query returns no manifest candidates in every consent state.
That is by design: a candidate needs a query to match. A test pins it.

## Changes

### `src/pmcp/manifest/loader.py` (modify, +95/−2)
- `Manifest.base_keyword_weights` (new defaulted field, D2).
- `keyword_weights(servers)`: the IDF formula, moved from `matcher.py` unchanged, so that
  the loader can compute the base weights without an import cycle (`matcher` imports
  `loader`).
- `_require_str_list`, `_require_optional_str`, `_STR_LIST_FIELDS`, `_OPTIONAL_STR_FIELDS`,
  and the checks at the top of `_parse_server_config` (D4). `description` is read once and
  reused.
- `_build_manifest`: `base_weights = keyword_weights(servers.values())` before the overlay
  loop, passed to `Manifest(...)`.

### `src/pmcp/manifest/matcher.py` (modify, +15/−9)
- `_manifest_keyword_weights` returns `dict(manifest.base_keyword_weights)` when set, and
  `keyword_weights(manifest.servers.values())` otherwise.

### `src/pmcp/tools/handlers.py` (modify, +1)
- `_build_manifest_with_config_servers` passes `base_keyword_weights` through (D3).

### `tests/test_catalog_overlay_discovery.py` (new, 34 tests; verbatim below)
- R1 end to end through the real `pmcp trust approve|revoke` CLI entry path (`parse_args`
  → `async_main`), the real loader and consent gate, and `GatewayTools` with the real
  `PolicyManager`:
  - approve gives shipped + user + project;
  - revoke gives shipped + user;
  - unapproved gives the project nothing.
- No query gives no candidates in either state.
- Every overlay source (user, project, env) leaves the weights equal to the shipped ones,
  on a miss and a hit.
- The copycat class test over all 569 shipped keywords.
- `match_capability` keeps its match.
- A hand-built manifest is unchanged.
- The config-server view keeps the base weights.
- An explicit-path load is weighted by its own file.
- R2:
  - the 17-row malformed table (each skipped; the sibling, shipped and bad-keyword queries
    do not raise; a warning is logged; a planted secret is never logged);
  - the same entry behind an approved project overlay via the CLI;
  - 5 well-typed or absent shapes still accepted;
  - every shipped entry passes.

## Documentation impact

- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed` (`CHANGELOG.md:439` on
  `c9206a9`):

  > **An overlay no longer hides other servers from `gateway.catalog_search`.** Discovery
  > weighs a keyword by how many servers declare it, and it counted over the merged
  > manifest. So an approved project overlay (or a user or `PMCP_MANIFEST_PATH` overlay)
  > whose server shared a keyword pushed every server with that keyword below the match
  > threshold. The query then returned no candidate at all, not even the shipped or user
  > server. Keyword weights now come from the shipped manifest alone, so an overlay can add
  > candidates but never remove one. A server entry whose name, `keywords`, `description`,
  > `declared_scopes`, `declared_capabilities`, `env_var`, `secret_key`,
  > `env_instructions`, `url`, `package` or `server_card_url` has the wrong type is now
  > skipped with a warning naming the field. Before, it made `catalog_search` fail for
  > every query. See [Consiliency/pmcp#342](https://github.com/Consiliency/pmcp/issues/342).

  Never put a closing keyword next to the number.
- `README.md` § "Private manifest overlay", the "fail-soft" paragraph (`README.md:1157`):
  after "a malformed file or a single bad entry logs a warning and is skipped", add "(an
  entry is bad when its name is not a string, or a list field such as `keywords` is not a
  list of strings)". Then add one sentence: "An overlay's servers are found by their own
  keywords; sharing a keyword with another server never hides either one from
  `gateway.catalog_search`."
- No `SECURITY.md` change. Consent semantics are unchanged (D5). Run
  `scripts/check_security_claims.py`.

## Dependencies & order

1. Applies to `c9206a9` as is. Consiliency/pmcp#298 is already on main (PR 337). The
   other open change in this area is Consiliency/pmcp#297. Its plan branch,
   `origin/plan/297-validation-echo` @ `48b7a89`, was compared this session. Its
   `loader.py` patch touches the YAML parse sites, `_shipped_manifest_entries`, and the
   overlay skip warnings (`{exc}` → `exception_text(exc)`). It has no hunk in
   `_parse_server_config`, `Manifest`, `_build_manifest`'s server loop, `matcher.py` or
   `_build_manifest_with_config_servers`. The one meeting point is the overlay's
   "Skipping invalid server entry" warning, which will render this plan's `ValueError`.
   Its messages carry only a field name and a type name, so they pass through
   `exception_text` unchanged. Whichever lands second re-runs M12 and the R2 table. The
   #233 cache stores the new `Manifest` field without change.
2. Within the plan, apply the patch and the test module together. The R1 and R2 halves are
   separable: R2 is the `_require_*`/`_STR_LIST_FIELDS` hunk plus its tests (M5–M18), and
   R1 is the rest (M1–M4). Land them together; they are one issue's two causes.
3. Docs last.

## Verification (measured this session on the spike; the implementer re-runs each step)

Run from the worktree. A fresh worktree needs `uv sync --all-extras -p 3.10` first, or `uv run`
silently uses the system pytest. On dev0, first run
`unset npm_config_cache npm_config_store_dir pnpm_config_store_dir`. Keep `--basetemp` off
`/tmp` and outside the checkout, e.g. `--basetemp=$WORKTREE_ROOT/pmcp-342-bt/<run>`.

```bash
# 1. The new module
uv run pytest tests/test_catalog_overlay_discovery.py -q -p no:cacheprovider --no-cov --cov-fail-under=0
#   spike: 34 passed (25-27 s on a loaded host; copycat test 4.4 s)
#   main c9206a9 with the same file: 25 failed, 9 passed (the 9: no-query, hand-built,
#   explicit-path, 5 well-typed shapes, shipped-entries -- the guards of unchanged behaviour)

# 2. Lint, format, types
uv run ruff check src/pmcp/manifest/loader.py src/pmcp/manifest/matcher.py src/pmcp/tools/handlers.py tests/test_catalog_overlay_discovery.py
uv run ruff format --check src/pmcp/manifest/loader.py src/pmcp/manifest/matcher.py src/pmcp/tools/handlers.py tests/test_catalog_overlay_discovery.py
uv run mypy src/pmcp/manifest/loader.py src/pmcp/manifest/matcher.py src/pmcp/tools/handlers.py tests/test_catalog_overlay_discovery.py
#   -> All checks passed! / 4 files already formatted / Success: no issues found in 4 source files

# 3. Touched-module subset (14 files)
uv run pytest tests/test_catalog_overlay_discovery.py tests/test_manifest.py tests/test_manifest_overlay.py \
  tests/test_manifest_cache.py tests/test_offline_discovery.py tests/test_credential_gates_handlers.py \
  tests/test_project_source_consent_manifest.py tests/test_trust_cli.py tests/test_version_pin.py \
  tests/test_env_overlay_provenance.py tests/test_tools.py tests/test_scoped_advisor_audit.py \
  tests/test_credential_optionality_e2e.py tests/test_phase4_e2e.py -q -p no:cacheprovider --no-cov --cov-fail-under=0
#   spike: 829 passed, 1 failed (test_monitor_reads_stderr: install-subprocess timing under load;
#   3/3 alone on the spike, 2/2 on main), 1 skipped

# 4. The real gateway, end to end (repro dir, appendix): start `pmcp --transport http` in proj,
#    call.py before / after `pmcp trust approve` / after `pmcp trust revoke`
#   -> the table in Research summary (spike column)

# 5. Class and consumer differentials (appendix)
python copycat.py      # main: 329 of 440 queries lose a candidate, 91 servers; spike: 0, 0
python consumers.py    # spike: only additions (projonly) differ; get_server equal for every shared name
python probe.py        # spike: every malformed row skipped, nothing raises

# 6. Mutation driver (18 mutants, appendix): exact-text replacement, run the module, restore from
#    a saved copy and cmp-check; M4 also runs the existing generic-api test
python mutants.py "$PWD"
#   -> 18 of 18 red, every restore identical (table below)

# 7. Cost: load_manifest() hit, median of 200, 3 rounds, main vs spike alternating
#   main 0.93-1.03 ms, spike 0.95-1.02 ms

# 8. The full suite, once, alone, detached (CI command minus -v)
nohup uv run pytest tests/ -q --tb=short --cov --cov-report= -p no:cacheprovider \
  --basetemp=$WORKTREE_ROOT/pmcp-342-bt/full > full.log 2>&1 & disown
#   -> 5557 passed, 3 skipped, 80 deselected, 0 failed in 637.84s (0:10:37); coverage 90.06% (alone, 3.10.21, npm vars unset)

# 9. Gates
python3 scripts/check_security_claims.py
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-342-catalog-overlay-*.md
#   -> OK, 129 cited node ids / blocking inconsistencies: 0

# 10. Embedding proof: extract the ````diff block and the ````python test block from THIS file;
#     on a clean c9206a9 tree: git apply --check, git apply, write the test module, run it
#   -> both cmp-identical to the spike; apply-check clean; 34 passed
```

## Acceptance criteria

- [ ] With a user overlay and an approved project overlay sharing a keyword with each
  other and with a shipped server, `catalog_search` returns shipped + user + project
  candidates. After `pmcp trust revoke`, it returns shipped + user. This is measured through
  the real CLI, loader and consent gate (test) and through a real `pmcp --transport http`
  gateway (Verification 4).
- [ ] No overlay source (user, project, env) changes any keyword weight, on a cache miss
  or a hit (M1, M2).
- [ ] An approved overlay declaring every shipped keyword removes no shipped server from
  any shipped-keyword query: 0 of 440 (main: 329) (M1, M2).
- [ ] `match_capability` keeps its match under a sharing overlay (M1).
- [ ] Hand-built manifests and explicit-path loads keep main's weighting. The existing
  IDF tests pass unchanged, and raising the floor instead (M4) is red.
- [ ] `request_capability`'s merged view carries the base weights (M3).
- [ ] Each malformed shape in the R2 table is skipped with a warning, and its siblings and
  the shipped servers stay discoverable. No `catalog_search` raises, and no value appears
  in a log line (M5–M18).
- [ ] Well-typed and absent fields are accepted as before, and every shipped entry passes.
- [ ] Without a query, `catalog_search` returns no manifest candidates in every consent
  state.
- [ ] The consumer table holds: `get_server` is equal approved vs revoked for every shared
  name, and `request_capability` is identical for the measured queries.
- [ ] `load_manifest()` hit cost is within main's run-to-run spread.
- [ ] The full suite passes. ruff, ruff format and mypy are clean.
  `check_security_claims.py` is OK, and `check_plan_consistency.py` reports 0 blocking. The
  CHANGELOG and README text is as above, with no closing keyword.

## Mutation table

Each mutant is an exact-text replacement that must match exactly once, in
`src/pmcp/manifest/loader.py` (M2, M4–M18), `src/pmcp/manifest/matcher.py` (M1) or
`src/pmcp/tools/handlers.py` (M3). The driver (`mutants.py`, appendix) applies it, then
runs `tests/test_catalog_overlay_discovery.py`; M4 also runs
`tests/test_manifest.py::test_keyword_match_generic_api_alone_stays_below_threshold`. It
restores the file from a saved copy, never with git, and checks the restore with `cmp`. It
was run on the spike text embedded below (Python 3.10.21).

| id | mutant (the rule it breaks) | result | first failure | failing tests |
|---|---|---|---|---|
| M1 | R1: scoring ignores the base weights (counts the merged manifest) | **RED** 6 failed, 28 passed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable, test_no_overlay_source_changes_a_keyword_weight[user], test_no_overlay_source_changes_a_keyword_weight[project], test_no_overlay_source_changes_a_keyword_weight[env], test_a_copycat_overlay_hides_no_shipped_server_on_any_shipped_keyword, test_match_capability_keeps_its_match_when_an_overlay_shares_the_keyword |
| M2 | R1: base weights computed after the overlays are merged | **RED** 6 failed, 28 passed (restored=True) | assert [] == ['projonly', 'useronly'] | test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable, test_no_overlay_source_changes_a_keyword_weight[user], test_no_overlay_source_changes_a_keyword_weight[project], test_no_overlay_source_changes_a_keyword_weight[env], test_a_copycat_overlay_hides_no_shipped_server_on_any_shipped_keyword, test_match_capability_keeps_its_match_when_an_overlay_shares_the_keyword |
| M3 | R1: request_capability's config-server view drops the base weights | **RED** 1 failed, 33 passed (restored=True) | assert None is not None | test_the_config_server_view_keeps_the_base_weights |
| M4 | instance fix instead of the class: raise the IDF floor so a shared keyword passes | **RED** 3 failed, 32 passed (restored=True) | assert {'api': 0.7} == {'api': 0.5} | test_a_hand_built_manifest_is_still_weighted_by_its_own_servers, test_an_explicit_path_load_weights_that_file_alone, test_keyword_match_generic_api_alone_stays_below_threshold |
| M5 | R2: a non-string server name is accepted | **RED** 2 failed, 32 passed (restored=True) |  | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[name-int], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[name-bool] |
| M6 | R2: a non-string description is accepted | **RED** 2 failed, 32 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[description-null], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[description-int] |
| M7 | R2: keywords not list-checked | **RED** 6 failed, 28 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-null], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-int-items], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-nested], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-string], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-mapping], test_a_malformed_entry_in_an_approved_project_overlay_is_skipped |
| M8 | R2: declared_scopes not list-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[declared_scopes-null] |
| M9 | R2: declared_capabilities not list-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[declared_capabilities-string] |
| M10 | R2: list items not type-checked | **RED** 2 failed, 32 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-int-items], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-nested] |
| M11 | R2: an explicit null list passes (only present-and-non-null checked) | **RED** 3 failed, 31 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-null], test_a_malformed_entry_is_skipped_and_discovery_keeps_working[declared_scopes-null], test_a_malformed_entry_in_an_approved_project_overlay_is_skipped |
| M12 | R2: the type error echoes the value (a pasted secret reaches the log) | **RED** 1 failed, 33 passed (restored=True) |  | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[keywords-mapping] |
| M13 | R2: env_var not string-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[env_var-int] |
| M14 | R2: secret_key not string-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[secret_key-int] |
| M15 | R2: env_instructions not string-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[env_instructions-int] |
| M16 | R2: url not string-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[url-int] |
| M17 | R2: package not string-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[package-int] |
| M18 | R2: server_card_url not string-checked | **RED** 1 failed, 33 passed (restored=True) | assert 'bad' not in {'playwright': ServerConfig(name='playwright', description='Browser au | test_a_malformed_entry_is_skipped_and_discovery_keeps_working[server_card_url-int] |

**18 of 18 mutants red; every restore byte-identical.**

## Non-goals

- **Search quality for generic keywords** (`browser` today returns no candidate even for
  `playwright`, because 5 servers share it). That is shipped-only behaviour and
  overlay-independent. Changing it is a ranking decision for its own issue (D2,
  alternatives).
- **Type checks for fields discovery does not read** (`args`, `install`, `headers`,
  `command`, `transport`). A bad value there breaks only that server's own provisioning.
  No such value crashed `catalog_search` in the probe.
- **Making overlay-only servers reachable by `request_capability`'s category tier.** The
  category map is a fixed list of shipped names. That is a separate feature, and approval
  does not change it (measured).

## Unverified

- **The reviewer's exact overlay.** It was not recorded. Both causes produce the reported
  symptom ("no candidates from any source; revoke restores"), and both are fixed and
  tested. If the reviewer's file is found, re-run it through `call.py`.
- **Python 3.11/3.12.** The new code is version-independent: no pickle-depth or
  recursion-sensitive behaviour. The module was run on 3.10.21 only.

## Execution Policy

- execute: effort=low.
- reason: three source files, about 110 lines. One defaulted dataclass field, one moved
  pure function, and parse-time type checks. No consent, cache-key or wire change. The
  risk is R2 rejecting an overlay entry an operator relies on, if its types were wrong
  but "worked". Such an entry now logs a warning naming the field.
- Apply the patch and the test module verbatim. Re-run Verification 1–3, 6 and 8 against
  the real tree, and measure main in the same window before claiming any number.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the patch below (between the ```` fences) as `342-src.patch`, and run
   `git apply 342-src.patch` on `c9206a9`.
2. Write the test module below to `tests/test_catalog_overlay_discovery.py`.
3. Add the CHANGELOG and README text by hand, as in *Documentation impact*.

### Patch — `src/pmcp/manifest/{loader.py, matcher.py}`, `src/pmcp/tools/handlers.py`

````diff
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index aa5ec3d..17c598f 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -512,6 +512,17 @@ class Manifest:
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
@@ -642,6 +653,22 @@ class Manifest:
         return matching_clis, matching_servers
 
 
+def keyword_weights(servers: Iterable[ServerConfig]) -> dict[str, float]:
+    """Inverse document frequency of each normalized keyword, floored at 0.5.
+
+    A keyword one server declares weighs 1.0; one that N servers share weighs
+    max(1/N, 0.5). Duplicates within one server count once.
+    """
+    frequencies: dict[str, int] = {}
+    for server in servers:
+        for keyword in set(server.keywords):
+            keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
+            frequencies[keyword_norm] = frequencies.get(keyword_norm, 0) + 1
+    return {
+        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()
+    }
+
+
 def _parse_cli_alternative(name: str, data: dict[str, Any]) -> CLIAlternative:
     """Parse a CLI alternative from raw YAML data."""
     return CLIAlternative(
@@ -993,8 +1020,68 @@ def _materialize_version_pin_soft(server: ServerConfig) -> ServerConfig:
         return replace(server, version=None)
 
 
+def _require_str_list(field_name: str, value: Any) -> list[str]:
+    """``value`` if it is a list of strings; otherwise ValueError.
+
+    The message names the field and the offending type, never the value: an
+    overlay is free text an operator may have pasted a token into.
+    """
+    if not isinstance(value, list):
+        raise ValueError(
+            f"'{field_name}' must be a list of strings, not {type(value).__name__}"
+        )
+    for item in value:
+        if not isinstance(item, str):
+            raise ValueError(
+                f"'{field_name}' must be a list of strings; an item is {type(item).__name__}"
+            )
+    return value
+
+
+def _require_optional_str(field_name: str, value: Any) -> str | None:
+    """``value`` if it is a string or absent (``None``); otherwise ValueError."""
+    if value is not None and not isinstance(value, str):
+        raise ValueError(f"'{field_name}' must be a string, not {type(value).__name__}")
+    return value
+
+
+# Fields a discovery consumer reads off EVERY server, or copies into a typed
+# response for a matching one. A wrong type here does not stay inside its own
+# entry: `catalog_search` scores all servers and builds a pydantic candidate,
+# so one `keywords: null` raised for every query (Consiliency/pmcp#342 R2).
+# Rejecting the entry at parse time lets the overlay's per-entry try/except
+# skip it with a warning, which is the isolation the loader already promises.
+_STR_LIST_FIELDS = ("keywords", "declared_scopes", "declared_capabilities")
+_OPTIONAL_STR_FIELDS = (
+    "env_var",
+    "secret_key",
+    "env_instructions",
+    "url",
+    "package",
+    "server_card_url",
+)
+
+
 def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
-    """Parse a server config from raw YAML data."""
+    """Parse a server config from raw YAML data.
+
+    Raises ``ValueError`` for an entry whose name, or a field discovery reads,
+    has the wrong type (see ``_STR_LIST_FIELDS``); an overlay skips that entry.
+    """
+    if not isinstance(name, str) or not name:
+        raise ValueError(
+            f"server name must be a non-empty string, not {type(name).__name__}"
+        )
+    description = data.get("description", "")
+    if not isinstance(description, str):
+        raise ValueError(
+            f"'description' must be a string, not {type(description).__name__}"
+        )
+    for field_name in _STR_LIST_FIELDS:
+        if field_name in data:
+            _require_str_list(field_name, data[field_name])
+    for field_name in _OPTIONAL_STR_FIELDS:
+        _require_optional_str(field_name, data.get(field_name))
     install_data = data.get("install", {})
     install: dict[Platform, list[str]] = {}
 
@@ -1023,7 +1110,7 @@ def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
 
     return ServerConfig(
         name=name,
-        description=data.get("description", ""),
+        description=description,
         keywords=data.get("keywords", []),
         install=install,
         command=data.get("command", ""),
@@ -1525,6 +1612,11 @@ def _build_manifest(
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
@@ -1619,6 +1711,7 @@ def _build_manifest(
         discovery_queue_path=data.get(
             "discovery_queue_path", ".mcp-gateway/discovery_queue.json"
         ),
+        base_keyword_weights=base_weights,
     )
 
     logger.info(
diff --git a/src/pmcp/manifest/matcher.py b/src/pmcp/manifest/matcher.py
index f9dd835..d66ab5a 100644
--- a/src/pmcp/manifest/matcher.py
+++ b/src/pmcp/manifest/matcher.py
@@ -8,7 +8,12 @@ from dataclasses import dataclass
 from typing import Literal
 
 from pmcp.manifest.environment import CLIInfo
-from pmcp.manifest.loader import CLIAlternative, Manifest, ServerConfig
+from pmcp.manifest.loader import (
+    CLIAlternative,
+    Manifest,
+    ServerConfig,
+    keyword_weights,
+)
 from pmcp.types import CLIHint
 
 logger = logging.getLogger(__name__)
@@ -88,15 +93,16 @@ def _keyword_match_score(
 
 
 def _manifest_keyword_weights(manifest: Manifest) -> dict[str, float]:
-    frequencies: dict[str, int] = {}
-    for server in manifest.servers.values():
-        for keyword in set(server.keywords):
-            keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
-            frequencies[keyword_norm] = frequencies.get(keyword_norm, 0) + 1
+    """Keyword weights for discovery scoring.
 
-    return {
-        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()
-    }
+    A manifest from ``load_manifest`` carries the weights of its base alone, so
+    an overlay server never lowers another server's score (Consiliency/pmcp#342).
+    A keyword only an overlay declares is absent and scores at the default 1.0.
+    A hand-built Manifest has no base and is weighted by its own servers.
+    """
+    if manifest.base_keyword_weights is not None:
+        return dict(manifest.base_keyword_weights)
+    return keyword_weights(manifest.servers.values())
 
 
 def rank_cli_hints(
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 09e9f34..36d1d84 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -3312,6 +3312,7 @@ class GatewayTools:
             cli_alternatives=dict(manifest.cli_alternatives),
             servers=merged_servers,
             discovery_queue_path=manifest.discovery_queue_path,
+            base_keyword_weights=manifest.base_keyword_weights,
         )
 
     def _get_server_env_metadata(
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
* **R2, a malformed entry stays inside its entry.** ``_parse_server_config``
  type-checked nothing, so ``keywords: null`` (or an int name, an int keyword)
  in one overlay entry made ``catalog_search`` raise for EVERY query, and a
  null/int ``description``, ``url``, ``env_var``... made it raise for any query
  that entry matched. Those entries are now rejected at parse time, which the
  overlay's existing per-entry ``try/except`` turns into "skip with a warning".

Everything runs through the real loader, the real consent gate, and the real
``pmcp trust approve|revoke`` CLI entry path; only the client manager (no
servers running) is a stub.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from pmcp.cli import async_main, parse_args
from pmcp.manifest.loader import (
    _SHIPPED_MANIFEST_PATH,
    Manifest,
    ServerConfig,
    load_manifest,
)
from pmcp.manifest.matcher import _keyword_match, _manifest_keyword_weights
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools

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


# --- R2: a malformed entry stays inside its entry ------------------------------

GOOD = {"keywords": ["zzgood"], "command": "npx"}
BAD = {"keywords": ["zzbad"], "command": "npx"}
SECRET = "sk-live-DO-NOT-LOG-0123456789"

# (case, servers mapping). Each crashed catalog_search on main: the first group
# for every query, the second for any query that matched the bad entry.
MALFORMED: list[tuple[str, dict[Any, Any]]] = [
    ("keywords-null", {"bad": {"keywords": None, "command": "npx"}}),
    ("keywords-int-items", {"bad": {"keywords": [1, 2], "command": "npx"}}),
    ("keywords-nested", {"bad": {"keywords": [["zzbad"]], "command": "npx"}}),
    ("keywords-string", {"bad": {"keywords": "zzbad, other", "command": "npx"}}),
    ("keywords-mapping", {"bad": {"keywords": {SECRET: SECRET}, "command": "npx"}}),
    ("name-int", {123: BAD}),
    ("name-bool", {True: BAD}),
    ("description-null", {"bad": {**BAD, "description": None}}),
    ("description-int", {"bad": {**BAD, "description": 5}}),
    ("declared_scopes-null", {"bad": {**BAD, "declared_scopes": None}}),
    ("declared_capabilities-string", {"bad": {**BAD, "declared_capabilities": "x"}}),
    ("url-int", {"bad": {**BAD, "url": 5}}),
    ("package-int", {"bad": {**BAD, "package": 5}}),
    ("server_card_url-int", {"bad": {**BAD, "server_card_url": 5}}),
    (
        "env_var-int",
        {"bad": {**BAD, "requires_api_key": True, "env_var": 5}},
    ),
    (
        "secret_key-int",
        {"bad": {**BAD, "requires_api_key": True, "env_var": "X", "secret_key": 5}},
    ),
    (
        "env_instructions-int",
        {
            "bad": {
                **BAD,
                "requires_api_key": True,
                "env_var": "X",
                "env_instructions": 5,
            }
        },
    ),
]


@pytest.mark.parametrize(("case", "servers"), MALFORMED, ids=[c for c, _ in MALFORMED])
def test_a_malformed_entry_is_skipped_and_discovery_keeps_working(
    case: str,
    servers: dict[Any, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {**servers, "good": GOOD}}),
    )
    tools = _gateway()
    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()
    assert "good" in manifest.servers
    assert not any(not isinstance(n, str) for n in manifest.servers)
    assert "bad" not in manifest.servers
    assert any(
        "Skipping invalid server entry" in r.getMessage() for r in caplog.records
    )
    assert not any(SECRET in r.getMessage() for r in caplog.records)

    # None of these may raise, and the sibling and the shipped servers surface.
    assert _candidates(tools, "zzbad") == []
    assert _candidates(tools, "zzgood") == ["good"]
    assert _candidates(tools, "screenshot") == ["playwright"]


def test_a_malformed_entry_in_an_approved_project_overlay_is_skipped(
    project: Path,
) -> None:
    """The same isolation behind the consent gate, via the real CLI."""
    project.write_text(
        yaml.safe_dump(
            {"servers": {"bad": {"keywords": None, "command": "npx"}, "good": GOOD}}
        )
    )
    _run_cli("trust", "approve", str(project))
    tools = _gateway()
    assert "good" in load_manifest().servers
    assert _candidates(tools, "zzgood") == ["good"]
    assert _candidates(tools, "demo") == ["useronly"]
    assert _candidates(tools, "screenshot") == ["playwright"]


@pytest.mark.parametrize(
    "entry",
    [
        {"command": "npx"},
        {"command": "npx", "keywords": []},
        {"command": "npx", "env_var": None, "url": None, "description": ""},
        {"command": "npx", "declared_scopes": ["a"], "declared_capabilities": []},
        {"command": "npx", "args": None, "transport": "carrier-pigeon"},
    ],
)
def test_well_typed_and_absent_fields_are_still_accepted(entry: dict[str, Any]) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"ok": entry}}),
    )
    assert "ok" in load_manifest().servers


def test_every_shipped_entry_passes_the_new_checks() -> None:
    """The shipped parse has no per-entry try/except: it must stay valid."""
    manifest = load_manifest(_SHIPPED_MANIFEST_PATH)
    assert len(manifest.servers) > 100
    assert all(isinstance(s.keywords, list) for s in manifest.servers.values())
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

### `probe.py` (R2 malformed-entry probe)

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

### `mutants.py` (mutation driver)

````python
"""Mutation driver for the Consiliency/pmcp#342 spike.

Each mutant is an exact-text replacement that must match exactly once. The file
is restored from a saved copy (never git) and the restore is cmp-checked.
Usage: python mutants.py <worktree> [ids...]
"""

import filecmp
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
ONLY = set(sys.argv[2:])
LOADER = "src/pmcp/manifest/loader.py"
MATCHER = "src/pmcp/manifest/matcher.py"
HANDLERS = "src/pmcp/tools/handlers.py"
TESTS = ["tests/test_catalog_overlay_discovery.py"]
GENERIC = "tests/test_manifest.py::test_keyword_match_generic_api_alone_stays_below_threshold"

MUTANTS = [
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
    ("M5", "R2: a non-string server name is accepted", LOADER,
     "    if not isinstance(name, str) or not name:\n        raise ValueError(\n            f\"server name must",
     "    if False:\n        raise ValueError(\n            f\"server name must", TESTS),
    ("M6", "R2: a non-string description is accepted", LOADER,
     "    if not isinstance(description, str):\n", "    if False:\n", TESTS),
    ("M7", "R2: keywords not list-checked", LOADER,
     '_STR_LIST_FIELDS = ("keywords", "declared_scopes", "declared_capabilities")\n',
     '_STR_LIST_FIELDS = ("declared_scopes", "declared_capabilities")\n', TESTS),
    ("M8", "R2: declared_scopes not list-checked", LOADER,
     '_STR_LIST_FIELDS = ("keywords", "declared_scopes", "declared_capabilities")\n',
     '_STR_LIST_FIELDS = ("keywords", "declared_capabilities")\n', TESTS),
    ("M9", "R2: declared_capabilities not list-checked", LOADER,
     '_STR_LIST_FIELDS = ("keywords", "declared_scopes", "declared_capabilities")\n',
     '_STR_LIST_FIELDS = ("keywords", "declared_scopes")\n', TESTS),
    ("M10", "R2: list items not type-checked", LOADER,
     "        if not isinstance(item, str):\n", "        if False:\n", TESTS),
    ("M11", "R2: an explicit null list passes (only present-and-non-null checked)", LOADER,
     "        if field_name in data:\n", "        if data.get(field_name) is not None:\n", TESTS),
    ("M12", "R2: the type error echoes the value (a pasted secret reaches the log)", LOADER,
     '            f"\'{field_name}\' must be a list of strings, not {type(value).__name__}"\n',
     '            f"\'{field_name}\' must be a list of strings, not {value!r}"\n', TESTS),
]
for i, f in enumerate(["env_var", "secret_key", "env_instructions", "url", "package", "server_card_url"]):
    MUTANTS.append((f"M{13 + i}", f"R2: {f} not string-checked", LOADER,
                    f'    "{f}",\n', "", TESTS))

env = dict(os.environ)
for k in ("npm_config_cache", "npm_config_store_dir", "pnpm_config_store_dir"):
    env.pop(k, None)
bt = Path(os.environ["WORKTREE_ROOT"]) / "pmcp-342-bt" / "mut"

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
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
        )
        out = proc.stdout.splitlines()
        if os.environ.get("MUTDEBUG"): print(proc.stdout[-3000:], proc.stderr[-3000:])
        summary = next((ln for ln in reversed(out) if " passed" in ln or " failed" in ln), "?")
        failing = [ln.split("::", 1)[1].split(" ")[0] for ln in out if ln.startswith("FAILED ")]
        first = next((ln.split("Error: ", 1)[-1][:110] for ln in out if "Error" in ln and ln.startswith("/")), "")
    finally:
        shutil.copy2(saved, path)
        same = filecmp.cmp(saved, path, shallow=False)
        saved.unlink()
    verdict = "RED" if proc.returncode != 0 else "SURVIVED"
    print(f"| {mid} | {rule} | **{verdict}** {summary.strip('= ')} (restored={same}) | {first} | {', '.join(failing)} |", flush=True)
````
