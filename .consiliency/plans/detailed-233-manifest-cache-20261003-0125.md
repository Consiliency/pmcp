# Detailed plan: cache the parsed manifest, and parse pmcp's own manifest with libyaml (P-01; Consiliency/pmcp#233)

> **Revision 2** (2026-10-03): board round 1 on `4584302` (Consiliency/pmcp#331). Codex raised two
> blocking defects; the claude seat's F1 overlaps the second, and gemini found nothing blocking.
> **B1**: pickling a deeply aliased overlay raised `RecursionError` where main loads fine. Now a
> result that cannot be serialized or read back is returned uncached (D4.4). **B2 / F1**: a
> transition back into a state still held in the LRU was silent. Warnings are now owed per
> *transition*, and the refusal's identity is part of the key (D5). The claude nits are taken:
> the parser-equality test compares exact types, and the fallback test covers
> `_shipped_manifest_entries()`. See "Round 1 board findings".

> **Bounded-plan verdict: within threshold.** One source file changes
> (`src/pmcp/manifest/loader.py`, +282/−59). One test file is new
> (`tests/test_manifest_cache.py`, 37 tests), and two test files change: `tests/conftest.py`
> gets one reset line, and `tests/test_version_pin.py` loses PR 327's `_memoized_yaml` fixture.
> The docs change is one CHANGELOG bullet and one README sentence. It is one conceptual
> change: `load_manifest()` returns a cached result keyed by everything it reads, and pmcp's
> own shipped manifest goes through libyaml.
>
> **Base: `origin/main` = `89559db`.** The production diff and the test file below are
> verbatim from a spike worktree (`plan/233-manifest-cache` at `$WORKTREE_ROOT/pmcp-233`,
> dev0) off `89559db`. Every number in this plan was measured on it this session (Python
> 3.10.21, PyYAML 6.0.3 with libyaml, Linux 7.0). dev0 is a shared team host: the load
> average was 10–31 on 24 cores throughout, so absolute timings are noisy. Each comparison
> below ran back to back in one window, and the ratios are the claim.
>
> **Scope: P-01 only.** The issue also names **P-02**, the lifecycle lock held across connect
> retry sleeps (`client/manager.py:1107`, `RETRY_DELAYS = [1.0, 2.0, 4.0]` at `:358`). That
> is a separate concurrency change in a different subsystem, with its own failure modes. It
> is a non-goal here. Recommendation: its own plan, linked from Consiliency/pmcp#233 (see
> Non-goals).

## Round 1 board findings (Consiliency/pmcp#331 @ `4584302`)

| finding | seat | reproduced on the round-1 spike | resolution |
|---|---|---|---|
| **B1** pickling breaks a manifest main loads | codex (F041–F044) | yes: 500 chained anchors (11 KB) → `RecursionError` while pickling. Main returns 108 servers at depth 500 and 2000. The pickle limit is nesting **496** on 3.10.21 (recursion limit 1000), and the C unpickler loads what it dumped | D4.4: `_serialize` / `_deserialize` contain **any** exception. The result is returned uncached, the log line is DEBUG and names only the exception class, and a failed read-back rebuilds. The second-level cache's `pickle.loads` was unguarded too; round 2's read-back test found it, and it is now guarded |
| **B2** a consent transition into a cached state is silent | codex (F045–F047), claude F1 | yes: unapproved → approve → revoke warned `[1, 0, 0]` (main `[1, 0, 1]`); retargeting a symlink between two unapproved files warned once | D5: `_last_served_key`. A call whose key differs from the previous call's is rebuilt, which emits its warnings, even when the key is in the LRU. The key gains the refusal identity: resolved path, reason, remediation |
| N1 `==` treats `1 == True == 1.0` | claude | yes, by reading | the equality test compares type-tagged trees |
| N2 the fallback test misses `_shipped_manifest_entries()` | claude | yes, by reading | that test clears the `lru_cache` and runs the fallback parse |

The claude seat's 18 stale-result and concurrency attack tests (`scratchpad/331/seat1/test_seat_attacks.py`)
were re-run against the round-2 spike: **18 passed**.

## Task

From the 2026-09-01 codebase review, P-01 (Consiliency/pmcp#233): `load_manifest()`
(`src/pmcp/manifest/loader.py:1236`) is uncached. Each call re-parses the 78 KB shipped
`manifest.yaml` with the pure-Python `yaml.safe_load` (`:1256`), plus every overlay (`:1165`),
and request handlers call it many times per request. The issue asks for two "S" changes,
**measured before and after in the same PR**:

1. Cache the parsed manifest, invalidated when its inputs change.
2. Use `CSafeLoader` when libyaml is available, falling back to `SafeLoader`.

The issue quotes the review's figures: ~200 ms per call, ~22 ms with `CSafeLoader`, and
"up to ~15×" per `catalog_search`. The issue asks for them to be reproduced, not inherited.
The reproductions are below: 102–125 ms, 11–13 ms and 13×.

## Research summary

### Where the time goes (measured on `89559db`)

Harness: `count2.py` (appendix). It builds a `GatewayTools` with mocked client and policy
managers. HOME is isolated, with a user `.mcp.json` and a one-line user overlay, and a
5-entry registry stub matches the query. Each `load_manifest`, `yaml.safe_load` and
`load_configs` call is wrapped to count it, attribute it to its first non-harness frame, and
time it. Figures are per search, averaged over 3 warm searches.

| `catalog_search` input | `load_manifest` calls | `yaml.safe_load` calls | main (pure Python) | main + libyaml only (no cache) | spike (cache + libyaml) |
|---|---|---|---|---|---|
| query + `include_offline` (worst case) | **13** | 26 | **1556–1589 ms** | 355–403 ms | **25–37 ms** (round 2: 31–39 ms) |
| query only | 6 | 12 | 674–768 ms | 155–186 ms | 3 ms (round 2: 5–11 ms) |
| no query | 0 | 0 | 0 ms | 0 ms | 0 ms |

Where the 13 calls come from, worst case:

| site | calls/search |
|---|---|
| `handlers.py:1211` `catalog_search` (CLI hints) | 1 |
| `handlers.py:1319` `catalog_search` (manifest candidates) | 1 |
| `config/loader.py:1149` `load_configs` (via `_load_configured_servers`, `handlers.py:2875`) | 1 |
| `handlers.py:2667` `_auth_env_options`: one per manifest candidate (≤5) and one per registry candidate (≤5) | 10 |

Each call parses the shipped manifest once and each overlay once, which is where the 26
`yaml.safe_load` calls come from. In that run the parse was more than 99% of
`load_manifest`'s time (1570 of 1583 ms). `load_manifest()` has 22 other call sites:
`handlers.py` `refresh`, `config_status`, `get_startup_policy`, `request_capability`,
`provision`, `auth_connect`, `_finalize_server_ready`, `sync_environment`,
`_resolve_lifecycle_target`, `_get_server_config_for_update`,
`_configured_duplicate_missing_credential` and `_materialised_pin`; `server.py:733` at
startup; `cli.py:1647/1895`; `refresher.py:437/538`; `cli_commands/secrets.py:53/142`. Each
one is a full parse on main.

### Per call (`percall.py`, appendix)

| path | median | min |
|---|---|---|
| main, every call (pure-Python `SafeLoader`) | 102.5–124.6 ms | 92.4 ms |
| main with `yaml.safe_load` → `CSafeLoader` (no cache) | 11.4–13.0 ms | 9.4 ms |
| spike, **hit** | **0.65–0.67 ms** (round 2: 0.46–0.50 ms) | 0.42 ms |
| spike, miss with the shipped parse cached (an overlay or consent change), which is also the cost of a transition (D5) | 1.9–2.4 ms (round 2: 1.21–1.23 ms) | 1.16 ms |
| spike, cold miss (process start: parses the shipped manifest) | 12.9–20.8 ms (round 2: 12.2–18.9 ms) | 10.0 ms |
| a result that cannot be pickled (500 chained anchors), round 2: returned uncached, every call | 34.4 ms (main on the same overlay: **189.0 ms**) | — |
| raw `yaml.safe_load(shipped bytes)` / `yaml.load(…, CSafeLoader)` | 301 / 41 ms (median, 15 runs) | 278 / 19 ms |

So libyaml alone is a ~9–10× win per call, and the cache takes the steady state to under
1 ms per call. A hit costs: `pickle.loads` of the cached blob, about two-thirds; re-running
overlay discovery and the reads, about a quarter (`cProfile` over 100 hits: 0.22 s
`_pickle.loads` and 0.083 s `_gather_overlay_sources`, of 0.324 s).

### libyaml availability, and the fallback

- Locked environment: `yaml.__with_libyaml__ is True` (PyYAML 6.0.3).
  `yaml.load(shipped, CSafeLoader) == yaml.load(shipped, SafeLoader)`: **True**.
- `uv.lock` pins PyYAML 6.0.3. Its wheels, which bundle libyaml, cover cp310–cp314 on
  manylinux x86_64/aarch64/s390x, musllinux x86_64/aarch64, macOS x86_64/arm64 and Windows
  win32/amd64, plus win_arm64 for cp312+. **There is no wheel for win_arm64 on cp310/cp311**,
  and a source build without libyaml headers is pure Python too. `pyproject.toml` declares
  `pyyaml>=6.0` and `requires-python >=3.10`.
- Fallback: `getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader`, evaluated at call time.
  `yaml.CSafeLoader` exists only when the C extension imported. A test removes the attribute
  and loads the manifest (mutant M17 is the "no fallback" variant).
- **The two parsers do not accept the same language.** A differential over 10 edge inputs
  (Python 3.10, PyYAML 6.0.3) found two differences:

  | input | `SafeLoader` | `CSafeLoader` |
  |---|---|---|
  | `a:\t1` (tab after the colon) | `ScannerError` @1:3 | accepted |
  | 2000 nested `[` | `RecursionError` | accepted |
  | tab indent, NEL, UTF-16 BOM, duplicate key, `\x07`, a 1100-char key, a trailing document | same | same |

  This is why D6 keeps overlays on `SafeLoader`.

### Inputs `load_manifest()` reads (the cache key must cover all of them)

From reading `loader.py`, and from `grep -n 'os\.environ\|getenv\|Path\.home\|cwd()\|os\.name\|sys\.platform'`:

| input | where it is read | how a change is seen |
|---|---|---|
| shipped `manifest.yaml` bytes (or the explicit `manifest_path`) | `load_manifest` | sha256 in the key |
| `~/.pmcp/manifest.yaml` (HOME is read at call time) | `_overlay_manifest_paths` `:1078` | path + sha256 in the key |
| project overlay (cwd walk, stops at tempdir and HOME) | `_find_project_manifest` `:1013` | path, consent state and sha256 of the approved bytes |
| project consent: `trust_store.is_approved` reads the trust store, located from HOME and the active project root (`trust_store.py:98,447`) | `read_and_gate` `project_consent.py:258` | the gate runs every call; its decision is in the key |
| `$PMCP_MANIFEST_PATH` and its provenance (`env_key_is_operator_supplied`) | `_overlay_manifest_paths` `:1104` | the env source's path + sha256, or the ignored-redirect notice text, is in the key |
| platform (`os.name == "nt"`) | `_on_windows` `:838`, decides bare-`npx` spellings when a pin is materialised | `_on_windows()` in the key |
| `_shipped_declared_keys` / `_shipped_manifest_entries` | `lru_cache`, shipped file only | constant per process |

Nothing else: `_parse_server_config`, `_parse_overlay_document` and
`_materialize_version_pin` read only their arguments (and the logger).

### Who mutates a result, and who writes an overlay

- A grep of `src/` for in-place mutation of a `load_manifest()` result (`servers[...] =`,
  `.servers.update/pop`, `.args.append`, `.extra_env[...] =`, attribute assignment on a
  `ServerConfig`) finds none. `_build_manifest_with_config_servers` (`handlers.py:3267`)
  copies the dict first. Tests that mutate a `Manifest` (`test_tools.py:524/538/5016`,
  `test_manifest.py:802`) build their own and never use `load_manifest`. So nothing breaks
  today if results are shared. The copy is for the next caller that does mutate one, and a
  test pins it.
- **pmcp never writes an overlay manifest.** `refresher.py:134` writes
  `.mcp-gateway/descriptions.yaml`, and `config/guidance.py` writes the guidance config.
  `pmcp trust approve` and `pmcp trust revoke` write the trust store, which is a consent
  change and already part of the key. So no refresh or update path needs an invalidation
  hook.
- `load_manifest` is synchronous. Every call site runs it on the event-loop thread, and
  `grep 'to_thread\|run_in_executor'` finds none that wrap it. It is still made thread-safe
  (D9) because it is a sync, process-global function.

### Test-suite cost (the PR 327 context)

- Main's CI "Run tests with coverage" step (GitHub API step timestamps):
  - `89559db` (run 36828688561): 3.10 **11m26s**, 3.11 **15m45s**, 3.12 13m24s.
  - `3f19243` (run 36826697449): 3.10 10m08s, 3.11 11m05s, 3.12 12m24s.

  The variance between consecutive runs (11–16 min on 3.11) is larger than any effect
  claimed below.
- Full suite on the spike, **with coverage**, the CI command minus `-v` (3.10, dev0, under
  load): **4929 passed, 3 skipped, 80 deselected in 610.19 s (10:10)**. A counting plugin
  saw **1048 `load_manifest` calls and 888 misses, 8.53 s total in builds**. Most tests load
  once after the per-test reset, so most calls are misses. Each miss parses only the
  overlays: the shipped document is cached by its bytes across tests (D7).
  **Round 2:** the full suite was re-run, alone, on the **embedded round-2 text**: **4966 passed,
  3 skipped, 80 deselected in 605.40 s, coverage 89.88%**. The plugin counted 1162 calls and 973
  builds, which includes transitions. The round-1 note follows.
  **What the round-1 run covered:** it used the spike revision before the final one. That revision
  had D1–D9 and D7, but it picked the loader with a module constant rather than
  `_trusted_yaml_loader()`, it still contained the then-dead `_load_overlay_file`, and
  `_memoized_yaml` was still in place. The final, embedded text was then run on all 28 files
  that name `load_manifest` (2103 passed, the A/B below), on `test_version_pin.py` with the new
  tests (196 passed), and through all 17 mutants. The implementing PR re-runs the full suite
  on the final text.
- **Controlled before/after, with coverage, on every test file that names `load_manifest`** (28 files, 2103 tests; `ab.sh`, appendix; main then spike, back to back, same window): **main 252.49 s → spike 103.91 s** (2103 passed, 1 skipped, 24 deselected both times; −148.6 s, 2.4×; peak RSS 138 MB → 136 MB). **Round 2, on the embedded text, same procedure: main 212.38 s → spike 79.72 s** (−132.7 s, 2.7×; 2103 passed both times). Tests that reach `load_manifest` only indirectly are outside that set: for example, `test_offline_discovery.py` and `test_progressive_disclosure.py` drive `catalog_search`. In the full run a plugin counted 1048 calls suite-wide; the A/B did not count. So −148.6 s is a **lower bound** on the saving at this host's speed, not a model of CI. CI timing is reported in the implementing PR, not gated on (see Verification).
- PR 327's two exhaustive tests (`test_the_invalid_pin_text_is_true_*`, 432
  `load_manifest()` calls), no coverage:

  | variant | time |
  |---|---|
  | main, with `_memoized_yaml` | 1.70 s |
  | first spike (cache, no shipped-document cache), with the fixture | 21.76 s: the fixture patches `yaml.safe_load`, which no longer parses the shipped manifest, and every case is a miss |
  | spike with D7, with the fixture | 2.69 s |
  | spike with D7, **fixture removed** | **2.49 s** |

  The fixture is redundant under D7 (D11).
- Manifest-related subset, without coverage. These 15 files cover the manifest, overlays,
  consent, trust boundaries, pin and credential gates:

  | variant | result |
  |---|---|
  | main | 582 passed, 1 skipped in **104.79 s** |
  | first spike | 582 passed, 1 skipped in **61.75 s** |

  Same files, same window.

### Other files parsed on the request path (fix the class)

| file / parse | when | per request? | decision |
|---|---|---|---|
| policy file (`policy/policy.py:416/418`) | `PolicyManager.__init__` (`server.py:146`) | **no**, once at startup | nothing to do |
| `.mcp.json` user/project/custom (`config/loader.py:298/366`, through `load_configs`) | `_load_configured_servers` and others | yes: 1× per `catalog_search`, JSON (C parser), small files; **1 ms/search** with the manifest cached | non-goal (below) |
| registry cache `registry-cache.json` (`registry.py:691`) | `_load_registry_candidates` (`handlers.py:2756`), on every query-bearing search | yes: **6.1–6.9 ms** for a real 150-entry, 379 KB cache (fetched this session) | non-goal (below) |
| `.env`, `.env.pmcp`, `pmcp.env` (`load_dotenv`, `handlers.py:2645`) | `_check_api_key_available`, per candidate, only when the key is not already in `os.environ` | yes, small | non-goal: it has side effects (it loads keys into `os.environ` and records provenance) and is not a pure parse |
| descriptions cache, code patterns, snippets, guidance | startup or module singletons (`get_code_patterns_loader`) | no | nothing to do |
| `_shipped_manifest_entries` | `lru_cache`, once per process | no | moved to the libyaml parser (same bytes, same data) |

## Design decisions (made explicitly)

### D1. Cache the merged `Manifest`, keyed by the content of every input, not by mtime
**Decision:** a process-global LRU maps a key to the pickled `Manifest`. The key is
`(base path, apply_overlays, sha256(base bytes), ((label, path, state, sha256(bytes),
consent identity) per overlay), notices, _on_windows())`, where:
- `state` is `"read"` for the user and env overlays. For the project overlay it is
  `"approved"`, or the consent refusal reason. It is `"unreadable"` when the read failed.
- `notices` is the ignored-`PMCP_MANIFEST_PATH` text, when there is one.
- the consent identity (round 2, B2) is `(resolved path, reason, remediation)` of the project
  overlay's `ConsentDecision`, or `None` for the user and env overlays. A symlink retargeted
  from one unapproved file to another is a different refusal with a different remediation,
  and the unresolved path alone cannot tell them apart. Mutant **M21** drops it, and it is red.

**Why bytes, not `(mtime_ns, size)`:**
1. Each call has to read every overlay's bytes anyway: the consent gate judges the bytes it
   hands over (`read_and_gate`, "re-opening would parse bytes the operator never
   approved"). Hashing them costs **0.06 ms for the 78 KB shipped file** (1000 runs of
   read + sha256), and `stat` alone saves almost nothing (0.0016 ms).
2. A stat key can be fooled. On this host, 2000 same-size rewrites never produced an
   identical `(mtime_ns, size, ino)`: 0/2000 on ext4 and on tmpfs, because Linux 7.0 uses
   multigrain timestamps. That guarantee does not hold on HFS+ (1 s) or FAT (2 s), on many
   network filesystems, or on older kernels. Any tool can also set the mtime back (`cp -p`,
   `rsync -t`, `tar x`, `os.utime`).

   The stale-cache tests rewrite a file at the same size, restore its mtime with
   `os.utime`, and assert the new content is seen. Mutant **M1**, a stat key, is red on
   exactly those two tests.

**Rejected:**
- `functools.lru_cache` on `load_manifest`: its key would be `manifest_path` only, so it is
  stale on every overlay, consent or env change.
- mtime keys (above).
- Caching only the YAML parse and re-merging every call. That is safe, but the merge and
  the pin materialisation cost 1.9–2.4 ms per call, against 0.65 ms for a hit, and
  per-call merge warnings would continue (D5).

### D2. Every call re-derives the key; nothing is trusted from the previous call
**Decision:** `load_manifest()` always runs discovery: HOME, the cwd walk and
`PMCP_MANIFEST_PATH` with its provenance. It reads every overlay's bytes, runs the
project consent gate, and reads the shipped bytes. Only after that does it look the key
up. The bytes read for the key are the bytes parsed on a miss (`_OverlaySource.content`);
no source is ever re-opened. This is what makes the cache correct by construction: there is
no invalidation logic to get wrong. It is also why a hit still costs ~0.2 ms of I/O.
Mutant **M13** re-reads the file at parse time, and it is red: a rewrite between read and
parse would cache the bytes of one version under another version's key.

### D3. Never share mutable results: the cache holds pickled bytes, and each call gets `pickle.loads`
**Decision:** the cache values are `bytes`, `pickle.dumps(manifest, HIGHEST_PROTOCOL)`, so no
caller can reach the cached state. Each hit returns `pickle.loads(blob)`, a complete deep copy
by construction. A miss returns the object it built, which is no longer referenced by the
cache.

**Measured copy cost** for the full 107-server, 12-CLI `Manifest`:

| copy | cost |
|---|---|
| `copy.deepcopy` | 2.36 ms (median of 200) |
| `pickle.loads` of a cached blob (79 KB) | **0.31 ms** |
| `pickle.dumps` + `loads` | 0.52 ms |
| shallow dict copies | 0.001 ms, but they share every `ServerConfig` and its lists |

The pickles are made in-process from pmcp's own dataclasses and never persisted or read from
outside, so nothing untrusted is ever unpickled. This is stated in a code comment.

**Rejected:**
- Frozen dataclasses and `MappingProxyType` views. `ServerConfig` and `CLIAlternative` are
  mutable dataclasses with list and dict fields, used across the codebase, so this is an
  API change far outside this issue.
- Shallow copies: mutant **M6** is red on both no-sharing tests.

The no-sharing test does not list fields. It walks every list, dict, set and dataclass
reachable from two results and asserts their `id()` sets are disjoint, so a field added later
is covered.

### D4. Never cache a failure
**Decision:** three rules.

1. **An exception is never cached.** It escapes before the store: a missing explicit path,
   an error parsing the shipped manifest, any bug in a build. The next call recomputes.
   Tested with a one-shot `RuntimeError` from `_parse_server_config`, and with an explicit
   path that is missing and then created.
2. **A degraded result is never cached.** That is any result in which a source could not
   be read (`OSError`, or a consent refusal for reason `unreadable`) or could not be parsed
   (a YAML error, or a top level that is not a mapping). It is returned, it is not stored,
   and the next call recomputes it and warns again. That matches main's behaviour while the
   file is broken, and the first call after the fix applies the fixed file.

   `_parse_overlay_document` reports the failure through a new optional `failures` list
   argument. Its return type does not change, and it has no callers outside `loader.py`.

   Mutant **M7** caches degraded results, and it is red on three tests (YAML error, non-mapping,
   unreadable).
3. **Per-entry validation results are cached.** Examples are a bad `server_version` pin, an
   invalid `servers:` entry, or a `server_env` patch for an unknown server. These are pure
   functions of the bytes in the key, not failures to read the source.
4. **The cache can never make a successful load fail (round 2, B1).** Storing is
   `_serialize(manifest)`, which wraps `pickle.dumps` and contains **any** exception. If it
   fails, the result is returned as built and not stored, and one DEBUG line names only the
   exception class: never a value, a path or a key. Reading back is `_deserialize(blob)`, the
   same for `pickle.loads`: on failure the entry is dropped and the call rebuilds. The
   second-level shipped-document cache (D7) uses the same two helpers.

   **What breaks pickle:** a user or env overlay whose YAML anchors chain values into each
   other parses with `SafeLoader`, because aliases are not parser recursion. But
   `raw_discovery_metadata` then nests as deep as the chain, and `pickle.dumps` recurses once
   per level. On 3.10.21 the largest nesting that pickles is **496** levels (bisected), and the
   C unpickler loads it back. Codex's 11 KB document (500 anchors) crosses that.

   **Measured, 500 anchors:** main takes **189.0 ms** per call; round 2 takes **34.4 ms** per
   call, uncached, re-building every time, with 0 cache entries. A normal overlay still hits
   at 0.5 ms; at 300 anchors it is cached.

   **Why not reject deep results up front** with a depth check before pickling: it would add a
   walk over every result on every miss, and duplicate a limit that depends on the Python
   version. Containing the exception is exact and free when nothing fails.

   Tests:
   - `test_a_result_that_cannot_be_pickled_is_returned_uncached[500/2000]`: both calls succeed,
     with 2 builds and 0 entries, and the DEBUG line carries no value;
   - `test_a_cached_result_that_cannot_be_read_back_is_rebuilt`.

   Mutants **M18** (raw `pickle.dumps`) and **M19** (raw `pickle.loads`) are red.

### D5. Warnings: once per transition (revised in round 2)
**Decision:** a warning is owed once each time the inputs change, whether or not the new
state is still cached. `load_manifest` remembers, under the lock, the key it served last
(`_last_served_key`).
- A call with the **same** key as the previous call, and a cache entry, is a silent hit.
- A call with a **different** key is built, even if its key is still in the LRU: a miss,
  or a *transition* into a state seen before. The build emits every warning for that state
  once, exactly the lines main would emit, and it costs **1.2 ms** with the shipped document
  cached (D7, round-2 measurement). The rebuilt entry replaces the stored one.

The lines this governs:
- merge-time warnings: overrides, unknown-server patches, refused pins;
- the project consent refusal (`log_refusal`);
- the ignored-`PMCP_MANIFEST_PATH` notice. This one is collected during key derivation and
  logged only when the call builds.

All three are functions of the key. The key includes the notice text (mutant **M3**) and the
refusal's identity (**M21**), so a change in either is a transition.

**Round 1 showed why "once per distinct state" was wrong.** The round-1 code warned once per
*cache miss*. An unapproved → approved → revoked sequence returned to a key still in the
8-slot LRU, and the revocation was silent (`[1, 0, 0]`; main `[1, 0, 1]`). A pin broken,
fixed and broken again with the same bytes was silent the second time. A symlink retargeted
from one unapproved file to another was silent, because the key lacked the resolved path.
That contradicted this plan's own acceptance criterion and CHANGELOG text. It also narrowed
SECURITY C-12 ("a refusal … reaches the operator as exactly one warning"): a refusal entered
again reached the operator as zero warnings.

**Why per transition, not on every call:**
- On main the same warnings fire up to 13 times per `catalog_search`. An operator with a bad
  pin or an unapproved project overlay gets the same line 13 times a search, which trains
  them to ignore it.
- Per transition means: told at startup, told again every time the file, the consent or the
  env changes (including back to an earlier state), and quiet while nothing changes. A
  refusal reaches the operator as exactly one warning per transition into it, which is what
  C-12's tests pin (`test_refusal_remedies.py`, `test_project_consent_gate.py`: both pass).
- While a source is broken, the warning repeats every call (D4.2), as on main, because that
  state is never cached.
- Alternating states, two callers in different cwds in one process for example, rebuild on
  every alternation. That costs about 1.2 ms each and logs each state's lines each time, as
  main does. A gateway serves one project, so it does not alternate.

**Test impact, measured:** every existing warning test passes unchanged. Each test starts with
an empty cache and no last-served key (D8). PR 327's exhaustive tests write different bytes
per case, so every case is a transition. That covers the 28 `load_manifest` files and the
full suite.

**New tests:**
- `test_warnings_are_emitted_once_per_miss`: the first load warns, the same state is silent,
  an edit warns again;
- `test_a_consent_refusal_is_logged_once_per_state`;
- `test_an_ignored_redirect_is_logged_once_per_state`;
- `test_clearing_the_cache_warns_again`;
- round 2, `test_every_consent_transition_warns_even_when_the_state_is_still_cached`.
  Unapproved, again, approve, revoke, again, approve, revoke gives `[1, 0, 0, 1, 0, 0, 1]`;
- round 2, `test_retargeting_a_project_overlay_symlink_is_a_new_refusal`. One, two, two, one
  gives `[1, 1, 0, 1]`, and each line names its own target;
- round 2, `test_breaking_fixing_and_breaking_again_warns_each_time`. Bad, bad, good, bad,
  bad with identical bytes gives `[1, 0, 0, 1, 0]`.

**Mutants:**
- **M8** logs the notices on every call.
- **M20** makes a transition into a cached state a silent hit; this is the round-1 code.
- **M21** drops the refusal identity from the key.
- **M22** never records the last-served key, so every call rebuilds and warns.

All four are red.

**Rejected:**
- Replaying the captured log records on each hit. It would keep main's 13× log volume, and it
  adds a log-capture mechanism inside the loader.
- Replaying stored records only on a transition hit. That is equivalent to rebuilding, but
  needs a second store; rebuilding costs 1.2 ms.
- Rewording the promise down to "once per distinct state while it stays cached" (the claude
  seat's option b). It narrows C-12 in practice, and an operator who revokes trust a second
  time gets nothing.

### D6. libyaml only for pmcp's own shipped manifest; overlays keep `SafeLoader`
**Decision:** `_parse_trusted_yaml(content)` is used only for the shipped manifest. It calls
`yaml.load(content, Loader=_trusted_yaml_loader())`, and the loader is `CSafeLoader` when
available. Two callers use it: `load_manifest`'s default path and `_shipped_manifest_entries`.
Overlays, and an explicit `manifest_path`, keep `yaml.safe_load`.

**Why:**
- The shipped file is pmcp's own, immutable per install, and the only large input (78 KB).
  A test pins that it parses identically under both loaders.
- Overlays come from the operator or the repository. They are tiny, and parsed once per
  distinct content (D1), so their parse speed is irrelevant.
- The two parsers do disagree on real input. Above, `a:\t1` is a `ScannerError` in
  `SafeLoader` and accepted by libyaml. A performance PR must not change which overlays are
  accepted. `test_an_overlay_is_still_parsed_by_the_pure_python_safeloader` pins it, and
  mutant **M10** is red on it.

**Both are safe loaders:** `yaml.load` is only ever called with `SafeLoader` or `CSafeLoader`,
the same `SafeConstructor`. The `# noqa: S506` comment says so.

**The fallback** is selected at call time, not import time, so a test can remove
`yaml.CSafeLoader` without reloading the module. Reloading would re-create `ServerConfig` and
break `isinstance` checks for later tests.

### D7. A second, pure level: the parsed shipped document, keyed by its own sha256
**Decision:** a single-slot `(digest, pickled raw data)` for the shipped document.

- A miss caused by an overlay, consent or env change re-uses it, so it costs 1.9–2.4 ms
  instead of 13–21 ms.
- It is a pure function of the bytes, so it is never stale and is **not** reset between
  tests. `_shipped_manifest_entries`' `lru_cache` is not reset either.
- This is what makes PR 327's fixture redundant (D11). Without it, the per-test reset made
  every test re-parse the shipped manifest with libyaml, and the two exhaustive tests took
  21.8 s.

Mutant **M12** ignores the digest, and it is red. Mutant **M16** drops the shipped digest
from the outer key, and it is red.

### D8. Where the cache lives, and how tests reset it
**Decision:** module globals in `src/pmcp/manifest/loader.py`:
- `_manifest_cache: OrderedDict[tuple, bytes]`, an LRU of **8** slots;
- `_manifest_cache_lock: threading.RLock`;
- a public `clear_manifest_cache()`, which also forgets the last-served key;
- `_last_served_key` (round 2, D5).

`tests/conftest.py`'s existing autouse `_reset_process_global_state` calls
`manifest_loader.clear_manifest_cache()` before and after every test. It sits next to
`registry.clear_in_process_cache()` and the other resets, and is documented there in the same
one-line style.

**Why 8 slots:** a gateway serves one project, so it has one key in steady state. The slots
absorb alternating states (a CLI command run in two directories, a test that flips env) without
thrashing. The bound is ~80 KB per slot, so ≤0.65 MB in total. Mutant **M14** removes the bound,
and it is red.

**Why reset per test,** when correctness does not need it: so that "once per miss" warnings are
deterministic per test, whatever ran before. `clear_manifest_cache()` has no production caller.
Correctness never depends on it, because the key covers every input. A reset is not a
provenance-erasing operation, unlike `reset_pmcp_introduced_keys`, so it may live in `src/`.

### D9. Thread and async safety
**Decision:**
- Lookup, build and store run under one `RLock`.
- The build is synchronous and in-memory: all I/O happened during key derivation, outside the
  lock. So the lock is held for 2–21 ms on a miss and ~0 on a hit, and concurrent cold
  callers build **exactly once**. `test_concurrent_cold_callers_build_once` runs 6 threads
  behind a barrier with a 0.2 s build; mutant **M9**, with no lock, builds 6 times.
- `RLock` rather than `Lock`: no re-entrant path exists today (`read_and_gate` and the trust
  store do not call `load_manifest`), but a future one would recompute instead of deadlocking.
- There is no `await` anywhere in the path, so asyncio needs nothing more. The event loop is
  blocked for ~0.65 ms per hit, against ~100–125 ms per call on main.

### D10. No invalidation hooks
pmcp never writes an overlay (see the research summary), and the key covers consent, env,
cwd, HOME and platform. So there is no `invalidate()` call to add to `pmcp trust approve`,
`pmcp refresh`, `update_server` or `auth_connect`, and none to forget in a future path.
`clear_manifest_cache()` is for tests only.

### D11. Remove PR 327's `_memoized_yaml` fixture from `tests/test_version_pin.py`
It monkeypatches the global `yaml.safe_load` to memoise by text. That no longer reaches the
shipped parse (D6), and D7 makes it redundant: measured 2.49 s without it against 2.69 s with
it. Keeping it would also mean a test seam that silently changes which parser runs. Remove the
fixture and its two parameter uses. The exhaustive tests then exercise the production cache
itself, with one miss per case, and their warning assertions pass (D5).

### D12. Call fan-out stays as it is
`catalog_search` still calls `load_manifest()` up to 13 times. Each hit costs ~0.65 ms, so
13 hits are about 8.5 ms. The in-situ measurement was 22–32 ms of the 25–37 ms search, on a
loaded host. Threading one `Manifest` through `_auth_env_options` and
`_registry_candidate_for_entry` would cut that to one call, but it changes six signatures in
`handlers.py` for a few milliseconds. That is a follow-up if a profile ever shows it.
`test_a_catalog_search_builds_the_manifest_at_most_once` pins that the fan-out costs **one
build** for two searches. Mutant **M15**, no cache, is red on it.

### D13. An explicit `manifest_path` is cached the same way, and parsed with `SafeLoader`
`load_manifest(path)` skips overlays, as today. Its key is the path, `apply_overlays=False`,
and the file's sha256. It is not pmcp's shipped file, so it keeps `SafeLoader` (D6). No `src/`
caller passes a path; tests do.

### D14. The base is read as bytes, not opened in text mode
Main does `open(manifest_path, "r")` and then `yaml.safe_load(f)`, which decodes with the
locale encoding. The spike reads bytes, which PyYAML decodes as UTF-8/16 by BOM, as YAML
specifies. That differs only for a non-UTF-8 locale reading a non-ASCII manifest, and the
bytes path is the correct one.

## Non-goals

- **P-02** (the lifecycle lock held across connect retries, `client/manager.py:1107/1272/1309`
  with sleeps from `RETRY_DELAYS` at `:358`). It is a different subsystem, and a concurrency
  change with its own failure modes (lock ordering, cancellation during a retry sleep). It
  needs its own plan and measurements. Recommendation: its own issue, or a separate plan on
  Consiliency/pmcp#233.
- **`.mcp.json` re-reads in `load_configs`.** They cost 1 ms per search with the manifest
  cached, through the C JSON parser. The project config is consent-gated per call like the
  project overlay, so a cache would need the same content key for ~1 ms. It is not worth a
  second cache.
- **The registry cache file.** It costs 6–7 ms per query-bearing search when present, but
  **no code in `src/` writes it** (`save_registry_cache` has no caller), so a default
  install falls through to `fetch_registry_servers`, which already has a 300 s in-process
  cache. If a writer is ever added, caching the read by content is a one-function follow-up.
- **dotenv reads in `_check_api_key_available`.** They are not a pure parse: they load keys
  into `os.environ` and record provenance (Consiliency/pmcp#229). Changing when they run
  changes semantics.
- **Freezing `ServerConfig` and `Manifest`** (D3).
- **Reducing the 13-call fan-out** (D12).
- **A `RecursionError` from a deeply nested user or env overlay** escapes `load_manifest` on
  main: `_parse_overlay_document` catches only `yaml.YAMLError`. This is pre-existing and
  unchanged here. An exception is never cached (D4.1). Consiliency/pmcp#297's `load_yaml`
  turns every parser exception into a `YAMLParseError`, which closes it.

## Compatibility with Consiliency/pmcp#297 (`src/pmcp/parsing.py`, plan PR 314)

Consiliency/pmcp#297 routes every structured-text parse through `pmcp.parsing` (`load_yaml(stream, *,
source)`, …). It adds a static test, `test_no_parser_call_outside_the_helpers`, that refuses
any parser reference in `src/pmcp` outside `parsing.py`. Its patch already rewrites
`loader.py`'s three `yaml.safe_load` sites, `_shipped_manifest_entries` included (its plan,
rev 8). The two changes compose as follows.

**What is orthogonal:** the cache stores the result of a build, keyed by bytes, so it does
not care which helper parsed them. Consiliency/pmcp#297's `YAMLParseError` subclasses `yaml.YAMLError`, so
`_parse_overlay_document`'s `except yaml.YAMLError` branch still fires, and that branch is
where D4's `failures.append("parse")` lives. An exception is never cached under either. Consiliency/pmcp#297
replaces the warning *text* (value-free); D5 changes only *when* it is emitted.

**If Consiliency/pmcp#297 lands first** (this PR rebases on it):
- Add one helper to `parsing.py`: `load_trusted_yaml(content: bytes, *, source: str) -> Any`.
  It has the same body and classification as `load_yaml`, but uses
  `yaml.load(content, Loader=getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader)`, and its
  docstring states D6's rule.
- `loader.py`'s `_parse_trusted_yaml(content)` becomes
  `load_trusted_yaml(content, source="shipped manifest")`, and `_trusted_yaml_loader` moves
  into `parsing.py`.
- The overlay and explicit-path parses stay `load_yaml(content, source=…)`.
- `test_no_parser_call_outside_the_helpers` stays green, because the only `CSafeLoader`
  reference is inside `parsing.py`.
- The tests here that reference `loader._trusted_yaml_loader` /
  `loader._parse_trusted_yaml` switch to `parsing._trusted_yaml_loader` /
  `parsing.load_trusted_yaml`. Mutants M10, M11 and M17 retarget to `parsing.py`.

**If this PR lands first:** Consiliency/pmcp#297's rebase meets `yaml.load(…, Loader=_trusted_yaml_loader())`
and `getattr(yaml, "CSafeLoader", …)` in `loader.py`, and its static rule flags them. That is
the intended trigger. The resolution is the one above, done by Consiliency/pmcp#297: move both into
`parsing.py` as `load_trusted_yaml`, and keep the `SafeLoader`-for-overlays rule.

**`_memoized_yaml`:** this plan deletes it (D11). If Consiliency/pmcp#297 lands first, its rev may have
rewritten that fixture to patch `parsing.load_yaml`. Either way it is deleted here.

**Pickle:** Consiliency/pmcp#297 gives `ParseError` a `__reduce__`, which is irrelevant here, because
exceptions are never pickled or cached (D4.1).

## Changes per file

### `src/pmcp/manifest/loader.py` (modify, verbatim diff below)

- imports: add `hashlib`, `pickle`, `threading`, and `collections.OrderedDict`.
- `_SHIPPED_MANIFEST_PATH` (new): replaces two inline `Path(__file__).parent / "manifest.yaml"`.
- `_trusted_yaml_loader()`, `_parse_trusted_yaml()` (new): D6.
- `_trusted_document`, `_parse_trusted_document()` (new): D7.
- `_shipped_manifest_entries`: parses through `_parse_trusted_yaml` (D6).
- `_overlay_manifest_paths(notices=None)`: when `notices` is given, the ignored-redirect text
  is appended there instead of being logged (D5). With no argument it logs as before.
- `_load_overlay_file`: **deleted**. Its read moved into `_gather_overlay_sources`, so the
  bytes read are the bytes keyed and parsed (D2).
- `_parse_overlay_document(path, content, failures=None)`: appends `"parse"` or
  `"not-a-mapping"` on those two failures (D4.2). Its return type is unchanged.
- `_manifest_cache`, `_manifest_cache_lock`, `_MANIFEST_CACHE_SLOTS`, `clear_manifest_cache()`
  (new): D8. `_last_served_key` (new, round 2): D5.
- `_serialize()`, `_deserialize()` (new, round 2): D4.4. They are used by the manifest cache
  and by `_parse_trusted_document`.
- `_source_key()` (new, round 2): one overlay's key part, with the consent identity (D1, D5).
- `_OverlaySource`, `_gather_overlay_sources()`, `_digest()` (new): D1 and D2.
- `load_manifest`: key derivation, lookup, notices on a miss, build, store if cacheable,
  return. The docstring states the cache contract.
- `_build_manifest()` (new): main's `load_manifest` body, taking bytes already read. It
  returns `(manifest, cacheable)`.

### `tests/conftest.py` (modify, +2)

Import `pmcp.manifest.loader as manifest_loader`, and call
`manifest_loader.clear_manifest_cache()` in `_reset_process_global_state._reset`; those two
lines are the embedded diff. **On top of the diff**, the implementer adds one bullet to that
fixture's docstring list: "`loader._manifest_cache` — the parsed manifest; a cached result
would hide a later test's once-per-miss warnings." It is docstring-only, and the only planned
deviation from a byte-identical apply (Verification step 10 compares before it is added).

### `tests/test_version_pin.py` (modify, −31)

Delete `_memoized_yaml` and its two parameter uses (D11).

### `tests/test_manifest_cache.py` (new, 37 tests; verbatim below)

## Documentation impact

- `CHANGELOG.md`: append one bullet to the existing `### Changed` heading under
  `## [Unreleased]` (`CHANGELOG.md:563` on `89559db`):

  > **`load_manifest()` is cached, and pmcp's own manifest is parsed with libyaml.**
  > Each call re-parsed the 78 KB shipped manifest with PyYAML's pure-Python loader, up to
  > 13 times per `gateway.catalog_search`: ~1.6 s of event-loop blocking per search, measured.
  > The result is now cached, keyed by the bytes of the shipped manifest and of every
  > overlay, each project overlay's consent decision, the `PMCP_MANIFEST_PATH` redirect and
  > its provenance, and the platform. Editing an overlay, approving or revoking one, or
  > changing the env takes effect on the next call, as before. Warnings about a manifest
  > are now logged once each time its inputs change (including a change back to an earlier
  > state), not on every load. A file that cannot be read or parsed is still reported on
  > every load until it is fixed. Overlays are still parsed with the pure-Python loader. See
  > [Consiliency/pmcp#233](https://github.com/Consiliency/pmcp/issues/233).

  Never use a closing keyword next to the number.
- `README.md` § "Private manifest overlay", after the "fail-soft" paragraph (`README.md:1125`),
  add one sentence: "pmcp re-reads overlay files on every manifest load, so edits apply
  without a restart, and logs each warning once per change rather than on every load."
- No `SECURITY.md` change. Consent semantics are unchanged: the gate runs on every call, and
  its decision, with the refusal's identity, is part of the key. C-12's "exactly one warning"
  holds per transition into a refusal (D5); its three cited tests pass. Run
  `scripts/check_security_claims.py`: OK, 129 nodes.

## Dependencies & order

1. `loader.py` and `tests/conftest.py` (the reset) together. Without the reset, warning tests
   become order-dependent.
2. `tests/test_manifest_cache.py`.
3. Delete `_memoized_yaml` (`tests/test_version_pin.py`).
4. Docs last.

Consiliency/pmcp#297 can land on either side (see "Compatibility"). Nothing else in flight
touches `load_manifest`.

## Verification (measured this session on the spike; the implementer re-runs each step)

Run everything from the worktree. A fresh worktree needs `uv sync --all-extras -p 3.10`
first, or `uv run` silently uses the system pytest. On dev0, first run
`unset npm_config_cache npm_config_store_dir pnpm_config_store_dir`, or the npm-identity
tests fail (109F/106E). Subset runs need `--cov-fail-under=0`.

```bash
# 1. The new tests (37 in round 2)
uv run pytest tests/test_manifest_cache.py -q -p no:cacheprovider --no-cov --cov-fail-under=0
#   -> 37 passed   (round 2; with test_version_pin.py: 202 passed in 5.68s)
#   -> the claude seat's 18 attack tests (scratchpad/331/seat1) against round 2: 18 passed in 2.71s

# 2. Lint, format, types
uv run ruff check src/pmcp/manifest/loader.py tests/test_manifest_cache.py tests/conftest.py tests/test_version_pin.py
uv run ruff format --check src/pmcp/manifest/loader.py tests/test_manifest_cache.py tests/conftest.py tests/test_version_pin.py
uv run mypy src/pmcp/manifest/loader.py tests/test_manifest_cache.py
#   -> All checks passed! / already formatted / Success: no issues found

# 3. Manifest/overlay/consent/pin/credential subset (15 files), main vs spike, same window
uv run pytest tests/test_version_pin.py tests/test_manifest_overlay.py tests/test_manifest.py \
  tests/test_env_overlay_provenance.py tests/test_project_source_consent_manifest.py \
  tests/test_project_source_consent_config.py tests/test_trust_boundaries_e2e.py \
  tests/test_trust_boundaries_composition.py tests/test_refusal_remedies.py \
  tests/test_baseline_constraints.py tests/test_fresh_operator_baseline.py \
  tests/test_pkgid_manifest_npx_selectors.py tests/test_pkgid_panel_fixes.py \
  tests/test_credential_gates_startup.py tests/test_credential_optionality_e2e.py \
  -p no:cacheprovider --cov-fail-under=0 --no-cov -q
#   main:  582 passed, 1 skipped in 104.79s
#   spike: 582 passed, 1 skipped in  61.75s   (first spike revision, before D7)

# 4. PR 327's exhaustive tests (432 load_manifest calls), fixture removed
uv run pytest tests/test_version_pin.py -k invalid_pin_text_is_true -q --durations=2 \
  -p no:cacheprovider --no-cov --cov-fail-under=0
#   spike, no fixture: 1.68s + 0.73s call; 2 passed in 2.49s   (main with fixture: 1.70s)

# 5. Every file that names load_manifest (28 files; indirect callers excluded, so a lower bound), WITH coverage, main vs spike,
#    same window, detached (ab.sh, appendix)
bash $S/ab2.sh   # swaps loader/conftest/test_version_pin between main and spike copies, then step 6
#   round 1: A main 252.49s / B spike 103.91s
#   round 2: A main:  2103 passed, 1 skipped, 24 deselected in 212.38s (0:03:32)
#   round 2: B spike: 2103 passed, 1 skipped, 24 deselected in 79.72s (0:01:19)

# 6. The full suite, once, WITH coverage (the CI command minus -v), detached with a counting plugin
PYTHONPATH=$S/plug LOADCOUNT_OUT=$S/loadcount.json nohup uv run pytest tests/ -q --tb=short \
  --cov --cov-report= -p loadcount -p no:cacheprovider --durations=25 > $S/full_spike.log 2>&1 & disown
#   -> Required test coverage of 60.0% reached. Total coverage: 89.75%
#   round 1 (pre-final text): 4929 passed, 3 skipped, 80 deselected in 610.19s (0:10:10)
#   round 2 (THE EMBEDDED TEXT, alone, npm vars unset): 4966 passed, 3 skipped, 80 deselected in 605.40s (0:10:05); coverage gate met
#   round 2 plugin: {"calls": 1162, "builds": 973}

# 7. catalog_search and per-call timings, main vs main+libyaml vs spike (count2.py, percall.py)
SCR=$S uv run python $S/count2.py py ; SCR=$S uv run python $S/count2.py C ; SCR=$S uv run python $S/percall.py main|mainC|spike
#   -> the tables in "Research summary"

# 8. Mutation driver (17 mutants, appendix): each applied to loader.py, test_manifest_cache.py
#    run, file restored from a saved copy and cmp-checked
uv run python $S/mutants.py "$PWD"
#   -> 22 of 22 red, every restore identical (table below)

# 9. Unchanged gates
python3 scripts/check_security_claims.py                         # -> OK
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-233-manifest-cache-*.md
#   -> blocking inconsistencies: 0

# 10. Embedding proof: extract the ````diff block after "### Production diff" and the ````python
#     block after "## Test bodies" from THIS file; on re-fetched origin/main (89559db):
git apply --check emb.diff && git apply emb.diff && cp emb_test.py tests/test_manifest_cache.py
cmp src/pmcp/manifest/loader.py spike_loader.py   # and conftest.py, test_version_pin.py, the test file
#   -> apply-check clean; all four cmp-identical; 37 passed (round 2)
```

**Targets for the implementing PR**, measured the same way and back to back with main:

| metric | main (measured) | target (spike measured) |
|---|---|---|
| worst-case `catalog_search` (query + `include_offline`, 13 loads) | 1556–1589 ms | ≤ 50 ms (spike: 25–37 ms) |
| `catalog_search`, query only | 674–768 ms | ≤ 10 ms (spike: 3 ms) |
| `load_manifest()` hit | 102–125 ms (every call is a parse) | ≤ 2 ms (spike: 0.65 ms; round 2: 0.46–0.50 ms) |
| a state transition (D5) | — (main parses every call) | ≤ 5 ms (round 2: 1.2 ms) |
| 500 chained anchors, a result that cannot be pickled | 189.0 ms per call | loads, uncached; ≤ main (round 2: 34.4 ms) |
| cold miss (process start) | same as above | ≤ 30 ms (spike: 13–21 ms) |
| `catalog_search` builds per 2 searches | 26 parses | exactly 1 build (test) |
| 28 `load_manifest` files, with coverage | 252.49 s (round 2 window: 212.38 s) | ≤ 45% of main in the same window (spike: 103.91 s, round 2: 79.72 s) |
| CI "Run tests with coverage", 3.11 | 11m05s–15m45s (two consecutive main runs) | no regression beyond that run-to-run spread; the 28-file A/B is the controlled measure |

CI wall time is reported in the PR but is not a gate: its run-to-run variance is larger than
the effect.

## Acceptance criteria

- [ ] `load_manifest()` returns a result equal to main's for every input state. The full
  suite passes with no existing test changed except the D11 fixture removal. Measured on the
  pre-final spike: 4929 passed, 3 skipped. Round 2, on the embedded text: 4966 passed, 3
  skipped (+37 new tests).
- [ ] **Stale-cache tests** pass, and each mutant that blinds the key to one input is red:
  - an overlay edit at the same size and mtime (user, env): M1;
  - a consent change (approve, revoke, re-approve, edit after approval): M2;
  - an env change (`PMCP_MANIFEST_PATH` switched or unset; provenance flipped);
  - an ignored redirect after a clean load: M3;
  - a cwd change and a HOME change: M2 (cwd);
  - a platform change: M4;
  - a shipped-bytes change: M16;
  - the bytes hashed are the bytes parsed: M13.
- [ ] **No shared mutation:** two results share no list, dict, set or dataclass (walked
  generically). Mutating a result in place never reaches the next caller. M5 and M6 are red.
- [ ] **No failure caching:**
  - an exception (a one-shot `RuntimeError`; a missing explicit path) is recomputed on the
    next call;
  - an unparseable overlay (YAML error, non-mapping) and an unreadable one are recomputed
    and re-warned on every call, and the fix applies on the next call. M7 is red.
- [ ] **Warnings once per transition:** a repeated state is silent, and an edit, a consent
  change (including back to an earlier state) or `clear_manifest_cache()` warns again. The consent refusal and the ignored-redirect notice
  are logged once per state. M8 and M15 are red.
- [ ] **Parsers:**
  - the shipped manifest parses identically under both loaders;
  - libyaml is used when available (M11), with a `SafeLoader` fallback when it is absent
    (M17);
  - overlays keep `SafeLoader`'s accept/reject behaviour (M10);
  - the shipped-document cache is keyed by its bytes (M12).
- [ ] **Concurrency and bound:** 6 concurrent cold callers build once (M9). The cache never
  exceeds 8 entries (M14).
- [ ] **Request path:** two worst-case `catalog_search`es do 1 build. M15 is red. The
  timing targets above are met and reported in the PR body, with main measured in the same
  window.
- [ ] **Round 2, B1:** an overlay whose result cannot be pickled (500 and 2000 chained
  anchors) loads as on main, uncached, with no value in any log line. A cached blob that
  cannot be read back is rebuilt. M18 and M19 are red.
- [ ] **Round 2, B2:** every consent transition warns, even into a state still in the LRU
  (`[1, 0, 0, 1, 0, 0, 1]`). A symlink retarget between unapproved files warns for each
  target. Break, fix, break with identical bytes warns at each break. M20, M21 and M22 are
  red.
- [ ] The shipped manifest's two parses are equal with exact scalar types. The fallback test
  runs both shipped parse sites.
- [ ] PR 327's `_memoized_yaml` is removed and its two tests still pass.
- [ ] ruff, ruff format and mypy are clean. `scripts/check_security_claims.py` reports OK.
  `scripts/check_plan_consistency.py` reports 0 blocking. The CHANGELOG and README text is as
  above, with no closing keyword.

## Mutation table

Each mutant is an exact-text replacement in `src/pmcp/manifest/loader.py` (M5 and M6 replace several
sites: they store the object instead of the pickle, so the failure is the sharing, not a
type error). The driver (`mutants.py`, appendix) applies it, runs `tests/test_manifest_cache.py`,
restores the file from a saved copy, never with `git checkout`, and checks the restore with `cmp`. It was run on the final spike text
(the file embedded below).

| id | mutant (the rule it breaks) | result | first failure | failing tests |
|---|---|---|---|---|
| M1 | key on (mtime_ns, size), not content | **RED** 3 failed, 34 passed in 2.47s (restored=True) | AssertionError: assert '3.25.5' == '3.25.6' | test_a_user_overlay_edit_is_seen_even_with_size_and_mtime_unchanged, test_an_env_overlay_edit_is_seen, test_breaking_fixing_and_breaking_again_warns_each_time |
| M2 | project overlay (consent) left out of the key | **RED** 4 failed, 33 passed in 2.53s (restored=True) | AssertionError: assert None == '2.0.1' | test_a_consent_change_is_seen, test_a_cwd_change_is_seen, test_every_consent_transition_warns_even_when_the_state_is_still_cached, test_retargeting_a_project_overlay_symlink_is_a_new_refusal |
| M3 | ignored-redirect notice left out of the key | **RED** 1 failed, 36 passed in 2.88s (restored=True) | assert False | test_an_ignored_redirect_after_a_clean_load_is_still_reported |
| M4 | platform left out of the key | **RED** 1 failed, 36 passed in 2.58s (restored=True) | AssertionError: assert None == '1.0.0' | test_a_platform_change_is_seen |
| M5 | the cached object itself is returned (store the object, no copy) | **RED** 7 failed, 30 passed in 1.84s (restored=True) | AssertionError: assert (Manifest(vers...y_queue.json') == Manifest(vers...y_queue.json') | test_a_hit_parses_nothing, test_no_two_callers_share_a_mutable_object, test_mutating_a_result_never_reaches_the_next_caller, test_a_result_that_cannot_be_pickled_is_returned_uncached[500], test_a_result_that_cannot_be_pickled_is_returned_uncached[2000], test_a_cached_result_that_cannot_be_read_back_is_rebuilt, test_concurrent_cold_callers_build_once |
| M6 | shallow copy: new dicts, shared ServerConfig objects | **RED** 5 failed, 32 passed in 1.98s (restored=True) | assert (not ({124531762888832, 124531762888960, 124531762889280, 124531762889344, 124531762889728, 12453176288 | test_no_two_callers_share_a_mutable_object, test_mutating_a_result_never_reaches_the_next_caller, test_a_result_that_cannot_be_pickled_is_returned_uncached[500], test_a_result_that_cannot_be_pickled_is_returned_uncached[2000], test_a_cached_result_that_cannot_be_read_back_is_rebuilt |
| M7 | a degraded result (unreadable/unparseable source) is cached | **RED** 3 failed, 34 passed in 2.57s (restored=True) | assert ([1] == [1, 1] | test_an_unparseable_overlay_is_recomputed_every_call[yaml-error], test_an_unparseable_overlay_is_recomputed_every_call[not-a-mapping], test_an_unreadable_overlay_is_recomputed_every_call |
| M8 | notices logged on every call, not once per transition | **RED** 1 failed, 36 passed in 2.20s (restored=True) | assert 2 == 1 | test_an_ignored_redirect_is_logged_once_per_state |
| M9 | no lock around lookup+build | **RED** 1 failed, 36 passed in 2.16s (restored=True) | assert [1, 1, 1, 1, 1, 1] == [1] | test_concurrent_cold_callers_build_once |
| M10 | overlays parsed with libyaml | **RED** 1 failed, 36 passed in 1.79s (restored=True) | AssertionError: assert '3.25.5' is None | test_an_overlay_is_still_parsed_by_the_pure_python_safeloader |
| M11 | shipped manifest parsed with the pure-Python SafeLoader | **RED** 1 failed, 36 passed in 2.94s (restored=True) | AssertionError: assert <class 'yaml.loader.SafeLoader'> is <class 'yaml.cyaml.CSafeLoader'> | test_the_shipped_manifest_uses_libyaml_when_available |
| M12 | parsed-shipped (L1) cache ignores the digest | **RED** 1 failed, 36 passed in 2.02s (restored=True) | AssertionError: assert 'Firecrawl MC...nd extraction' == 'edited in place' | test_the_parsed_shipped_document_is_keyed_by_its_bytes |
| M13 | overlay re-read at parse time (not the keyed bytes) | **RED** 1 failed, 36 passed in 2.20s (restored=True) | AssertionError: assert '9.9.9' == '3.25.5' | test_the_bytes_hashed_are_the_bytes_parsed |
| M14 | cache unbounded | **RED** 1 failed, 36 passed in 2.13s (restored=True) | AssertionError: assert 13 == 8 | test_the_cache_is_bounded |
| M15 | no cache at all | **RED** 9 failed, 28 passed in 3.13s (restored=True) | assert ([1, 1] == [1] | test_a_hit_parses_nothing, test_warnings_are_emitted_once_per_miss, test_a_consent_refusal_is_logged_once_per_state, test_an_ignored_redirect_is_logged_once_per_state, test_every_consent_transition_warns_even_when_the_state_is_still_cached, test_retargeting_a_project_overlay_symlink_is_a_new_refusal, test_breaking_fixing_and_breaking_again_warns_each_time, test_concurrent_cold_callers_build_once, test_a_catalog_search_builds_the_manifest_at_most_once |
| M16 | shipped bytes left out of the key | **RED** 1 failed, 36 passed in 2.21s (restored=True) | AssertionError: assert 'Firecrawl MC...nd extraction' == 'edited in place' | test_the_parsed_shipped_document_is_keyed_by_its_bytes |
| M17 | fallback missing: CSafeLoader required | **RED** 1 failed, 36 passed in 1.81s (restored=True) | AttributeError: module 'yaml' has no attribute 'CSafeLoader'. Did you mean: 'SafeLoader'? | test_without_libyaml_the_shipped_manifest_falls_back_to_safeloader |
| M18 | B1: serialization failure not contained (raw pickle.dumps) | **RED** 2 failed, 35 passed in 2.18s (restored=True) | RecursionError: maximum recursion depth exceeded while pickling an object | test_a_result_that_cannot_be_pickled_is_returned_uncached[500], test_a_result_that_cannot_be_pickled_is_returned_uncached[2000] |
| M19 | B1: read-back failure not contained (raw pickle.loads) | **RED** 1 failed, 36 passed in 2.19s (restored=True) | RecursionError: maximum recursion depth exceeded | test_a_cached_result_that_cannot_be_read_back_is_rebuilt |
| M20 | B2: a transition into a cached state is a silent hit | **RED** 3 failed, 34 passed in 2.16s (restored=True) | assert [1, 0, 0, 0, 0, 0, ...] == [1, 0, 0, 1, 0, 0, ...] | test_every_consent_transition_warns_even_when_the_state_is_still_cached, test_retargeting_a_project_overlay_symlink_is_a_new_refusal, test_breaking_fixing_and_breaking_again_warns_each_time |
| M21 | B2: the refusal's identity (resolved path, remediation) left out of the key | **RED** 1 failed, 36 passed in 2.36s (restored=True) | assert [1, 0, 0, 0] == [1, 1, 0, 1] | test_retargeting_a_project_overlay_symlink_is_a_new_refusal |
| M22 | B2: the last-served state is never recorded | **RED** 9 failed, 28 passed in 3.50s (restored=True) | assert ([1, 1] == [1] | test_a_hit_parses_nothing, test_warnings_are_emitted_once_per_miss, test_a_consent_refusal_is_logged_once_per_state, test_an_ignored_redirect_is_logged_once_per_state, test_every_consent_transition_warns_even_when_the_state_is_still_cached, test_retargeting_a_project_overlay_symlink_is_a_new_refusal, test_breaking_fixing_and_breaking_again_warns_each_time, test_concurrent_cold_callers_build_once, test_a_catalog_search_builds_the_manifest_at_most_once |

**22 of 22 mutants red; every restore byte-identical** (round 2, on the embedded text). M1–M17 are
round 1's, retargeted at the round-2 code: the key is built by `_source_key`, and the store and
return go through `_serialize`/`_deserialize`. M18–M22 are round 2's.

- execute: effort=medium. Reason: the change is small and mechanical, but it sits under every
  request path and the consent gate, and a cache fails silently, by serving a stale manifest.
  Apply the diff and the test file verbatim. Re-run every Verification step and mutant against
  the real tree, and measure main in the same window before claiming any number. If
  Consiliency/pmcp#297 has landed, follow "Compatibility" (move the trusted parse into
  `parsing.py`) and re-run M10, M11 and M17 against it.
- Before merge, run the cross-vendor panel code review and reconcile it (repository rule).

### Production diff (spike, verbatim; `git apply` on `89559db`)

````diff
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index 6e7704e..d33cfb5 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -3,10 +3,14 @@
 from __future__ import annotations
 
 import functools
+import hashlib
 import logging
 import os
+import pickle
 import re
 import tempfile
+import threading
+from collections import OrderedDict
 from collections.abc import Iterable, Mapping
 from dataclasses import dataclass, field, replace
 from pathlib import Path
@@ -24,6 +28,49 @@ from pmcp.validation import (
 logger = logging.getLogger(__name__)
 
 
+_SHIPPED_MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"
+
+
+def _trusted_yaml_loader() -> Any:
+    """libyaml's ``CSafeLoader`` when PyYAML was built with it, else ``SafeLoader``.
+
+    Both use the same ``SafeConstructor`` and ``Resolver``; only the scanner and
+    parser differ, so pmcp's own shipped manifest yields the same data either way
+    (a test pins that). Overlays are operator- and repository-supplied and keep
+    the pure-Python ``SafeLoader``: the two parsers do disagree on some input (a
+    tab after ``key:``, deep nesting), and a performance change must not change
+    which overlays are accepted (Consiliency/pmcp#233).
+    """
+    return getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
+
+
+def _parse_trusted_yaml(content: bytes) -> Any:
+    """Parse pmcp's OWN shipped manifest bytes, with libyaml when available."""
+    return yaml.load(content, Loader=_trusted_yaml_loader())  # noqa: S506 - safe loaders only
+
+
+# The parsed shipped document, by sha256 of its bytes: a pure function of the
+# bytes, so it is never stale and is not reset between tests. Pickled, so each
+# reader gets its own copy of the raw data.
+_trusted_document: tuple[bytes, bytes] | None = None
+
+
+def _parse_trusted_document(content: bytes) -> Any:
+    """``_parse_trusted_yaml(content)``, parsed once per distinct ``content``."""
+    global _trusted_document
+    digest = hashlib.sha256(content).digest()
+    cached = _trusted_document
+    if cached is not None and cached[0] == digest:
+        data = _deserialize(cached[1])
+        if data is not None:
+            return data
+    data = _parse_trusted_yaml(content)
+    blob = _serialize(data)
+    if blob is not None:
+        _trusted_document = (digest, blob)
+    return data
+
+
 @functools.lru_cache(maxsize=1)
 def _shipped_manifest_entries() -> dict[str, dict[str, Any]]:
     """pmcp's OWN shipped ``manifest.yaml``, read directly.
@@ -32,9 +79,9 @@ def _shipped_manifest_entries() -> dict[str, dict[str, Any]]:
     or ``$PMCP_MANIFEST_PATH`` overlay can add a name or a declared key here.
     The file ships with pmcp and does not change under a running process.
     """
-    path = Path(__file__).parent / "manifest.yaml"
+    path = _SHIPPED_MANIFEST_PATH
     try:
-        servers = (yaml.safe_load(path.read_bytes()) or {}).get("servers") or {}
+        servers = (_parse_trusted_yaml(path.read_bytes()) or {}).get("servers") or {}
     except (OSError, yaml.YAMLError, AttributeError):
         return {}
     return {
@@ -1063,7 +1110,9 @@ def _find_project_manifest() -> Path | None:
     return None
 
 
-def _overlay_manifest_paths() -> list[tuple[str, Path]]:
+def _overlay_manifest_paths(
+    notices: list[str] | None = None,
+) -> list[tuple[str, Path]]:
     """Return existing overlay manifest paths in precedence order (low → high).
 
     Order: user (``~/.pmcp/manifest.yaml``), then project
@@ -1108,9 +1157,11 @@ def _overlay_manifest_paths() -> list[tuple[str, Path]]:
             if env_path.exists():
                 paths.append(("env", env_path))
         else:
-            logger.warning(
-                describe_ignored_trust_env_var("PMCP_MANIFEST_PATH", env_value)
-            )
+            notice = describe_ignored_trust_env_var("PMCP_MANIFEST_PATH", env_value)
+            if notices is None:
+                logger.warning(notice)
+            else:
+                notices.append(notice)
 
     return paths
 
@@ -1123,25 +1174,9 @@ _OverlayDocument = tuple[
 ]
 
 
-def _load_overlay_file(path: Path) -> _OverlayDocument:
-    """Read and parse an overlay manifest file, fail-soft.
-
-    For the **ungated** overlay sources only -- the user's own
-    ``~/.pmcp/manifest.yaml`` and ``$PMCP_MANIFEST_PATH``. The project overlay
-    must not come through here: it is read once by ``read_and_gate``, and
-    reading it again would parse bytes the operator never approved. See
-    ``load_manifest``.
-    """
-    try:
-        content = path.read_bytes()
-    except OSError as exc:
-        logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
-        return {}, {}, {}, {}
-
-    return _parse_overlay_document(path, content)
-
-
-def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
+def _parse_overlay_document(
+    path: Path, content: bytes, failures: list[str] | None = None
+) -> _OverlayDocument:
     """Parse overlay bytes, fail-soft. ``path`` is for messages only.
 
     Returns ``(servers, cli_alternatives, server_env, server_version)``. A YAML error or a
@@ -1165,12 +1200,16 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
         data = yaml.safe_load(content)
     except yaml.YAMLError as exc:
         logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
+        if failures is not None:
+            failures.append("parse")
         return {}, {}, {}, {}
 
     if not isinstance(data, dict):
         logger.warning(
             f"Skipping manifest overlay {path}: top-level document is not a mapping"
         )
+        if failures is not None:
+            failures.append("not-a-mapping")
         return {}, {}, {}, {}
 
     servers: dict[str, ServerConfig] = {}
@@ -1233,6 +1272,141 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     return servers, cli_alternatives, server_env, server_version
 
 
+# A parsed manifest, cached by EVERYTHING it was built from (Consiliency/pmcp#233).
+# The key is the bytes of every source (sha256), not their mtimes: each call
+# re-reads every source anyway -- the consent gate must judge the bytes it
+# hands over -- so a content key costs one hash and cannot be fooled by a
+# same-size rewrite inside the filesystem's timestamp granularity or by an
+# mtime set back. Values are pickled bytes, so no caller can reach the cached
+# state, and every call gets its own deep copy (0.3 ms, against 2.4 ms for
+# copy.deepcopy). The pickles are made in this process from pmcp's own
+# Manifest objects and never leave it: nothing untrusted is ever unpickled.
+_MANIFEST_CACHE_SLOTS = 8
+_manifest_cache: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
+_manifest_cache_lock = threading.RLock()
+# The key of the state the previous call served. Warnings are owed per
+# TRANSITION, not per retained entry: a call whose key differs from this one is
+# rebuilt (about 2 ms with the shipped document cached) even when its key is
+# still in the LRU, so returning to an earlier state -- trust revoked again, a
+# pin broken again with the same bytes -- warns exactly as main does.
+_last_served_key: tuple[Any, ...] | None = None
+
+
+def clear_manifest_cache() -> None:
+    """Drop every cached manifest (tests; never needed for correctness)."""
+    global _last_served_key
+    with _manifest_cache_lock:
+        _manifest_cache.clear()
+        _last_served_key = None
+
+
+def _serialize(value: Any) -> bytes | None:
+    """``pickle.dumps(value)``, or None when it cannot be serialized.
+
+    A result that loads fine must never fail because of the cache: a deeply
+    aliased overlay (500 chained YAML anchors, 11 KB) parses with SafeLoader
+    but exceeds pickle's recursion limit on Python 3.10/3.11. Such a result is
+    returned uncached. The log line names only the exception class.
+    """
+    try:
+        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
+    except Exception as exc:  # noqa: BLE001 - any failure means "do not cache"
+        logger.debug(
+            "Manifest result not cached: it could not be serialized (%s)",
+            type(exc).__name__,
+        )
+        return None
+
+
+def _deserialize(blob: bytes) -> Any | None:
+    """``pickle.loads(blob)``, or None when it fails (the caller rebuilds)."""
+    try:
+        return pickle.loads(blob)
+    except Exception as exc:  # noqa: BLE001 - any failure means "rebuild"
+        logger.debug(
+            "Cached manifest could not be read back (%s); rebuilding",
+            type(exc).__name__,
+        )
+        return None
+
+
+@dataclass(frozen=True)
+class _OverlaySource:
+    label: str
+    path: Path
+    content: bytes | None
+    # "read" | "approved" | a consent refusal reason | "unreadable"
+    state: str
+    decision: Any = None
+    error: str | None = None
+
+
+def _gather_overlay_sources(notices: list[str]) -> list[_OverlaySource]:
+    """Read every overlay source ONCE, in precedence order.
+
+    The bytes read here are both the cache key and what gets parsed: a source is
+    never re-opened, so a rewrite between the read and the parse cannot put
+    bytes in the cache under another version's key.
+    """
+    sources: list[_OverlaySource] = []
+    for label, overlay_path in _overlay_manifest_paths(notices):
+        if label == "project":
+            # A repository-supplied overlay is gated: unapproved, it must
+            # contribute nothing at all -- not a replacement, not an
+            # insertion, not a server_env patch -- so that
+            # `get_server("<added>")`, the manifest-backed predicate in
+            # tools/handlers.py, still answers None for it. User and env
+            # scope are the operator's own files and stay ungated. The gate
+            # reads the file once; its bytes are the ones keyed and parsed.
+            content, decision = read_and_gate(overlay_path, "project_manifest")
+            sources.append(
+                _OverlaySource(
+                    label,
+                    overlay_path,
+                    content,
+                    "approved" if content is not None else str(decision.reason),
+                    decision,
+                )
+            )
+            continue
+        try:
+            content = overlay_path.read_bytes()
+        except OSError as exc:
+            sources.append(
+                _OverlaySource(label, overlay_path, None, "unreadable", error=str(exc))
+            )
+            continue
+        sources.append(_OverlaySource(label, overlay_path, content, "read"))
+    return sources
+
+
+def _digest(content: bytes | None) -> bytes | None:
+    return None if content is None else hashlib.sha256(content).digest()
+
+
+def _source_key(source: _OverlaySource) -> tuple[Any, ...]:
+    """One overlay's part of the cache key.
+
+    A project overlay's consent decision is keyed by its identity -- the
+    resolved path the gate judged, the reason and the remediation -- not only
+    its state: retargeting a symlink from one unapproved file to another is a
+    new refusal with a new remediation, and must be told (SECURITY C-12).
+    """
+    decision = source.decision
+    identity = (
+        None
+        if decision is None
+        else (str(decision.path), str(decision.reason), decision.remediation)
+    )
+    return (
+        source.label,
+        str(source.path),
+        source.state,
+        _digest(source.content),
+        identity,
+    )
+
+
 def load_manifest(manifest_path: Path | None = None) -> Manifest:
     """Load and parse the manifest.yaml file.
 
@@ -1244,16 +1418,67 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
     ``extra_env`` on an existing server without replacing it. An explicit
     ``manifest_path`` loads only that file and applies no overlays. Overlay
     parsing is fail-soft and never raises.
+
+    The result is cached by the bytes of every source, each overlay's consent
+    decision (with the refusal's identity), the ignored-redirect notice and the
+    platform; a change to any of them is a miss. Warnings are emitted once per
+    transition: whenever the inputs differ from those of the previous call,
+    even if that state is still cached. A result in which a source could not be
+    read or parsed, or that cannot be serialized, is never cached, and an
+    exception is never cached. Every call returns its own deep copy.
     """
     apply_overlays = manifest_path is None
-    if manifest_path is None:
-        # Default to manifest.yaml in the same directory as this module
-        manifest_path = Path(__file__).parent / "manifest.yaml"
-
+    base_path = _SHIPPED_MANIFEST_PATH if manifest_path is None else manifest_path
+    base = base_path.read_bytes()
+    notices: list[str] = []
+    overlays = _gather_overlay_sources(notices) if apply_overlays else []
+    key = (
+        str(base_path),
+        apply_overlays,
+        _digest(base),
+        tuple(_source_key(o) for o in overlays),
+        tuple(notices),
+        _on_windows(),
+    )
+    global _last_served_key
+    with _manifest_cache_lock:
+        steady = key == _last_served_key
+        _last_served_key = key
+        blob = _manifest_cache.get(key)
+        if blob is not None and steady:
+            _manifest_cache.move_to_end(key)
+            cached = _deserialize(blob)
+            if cached is not None:
+                return cast(Manifest, cached)
+        # A miss, or a transition into a state the LRU still holds: build, so
+        # every warning for this state is emitted once, now.
+        _manifest_cache.pop(key, None)
+        for notice in notices:
+            logger.warning(notice)
+        manifest, cacheable = _build_manifest(
+            base_path, base, overlays, trusted=manifest_path is None
+        )
+        stored = _serialize(manifest) if cacheable else None
+        if stored is not None:
+            _manifest_cache[key] = stored
+            while len(_manifest_cache) > _MANIFEST_CACHE_SLOTS:
+                _manifest_cache.popitem(last=False)
+        return manifest
+
+
+def _build_manifest(
+    manifest_path: Path,
+    base: bytes,
+    overlays: list[_OverlaySource],
+    *,
+    trusted: bool,
+) -> tuple[Manifest, bool]:
+    """Build a Manifest from bytes already read. Returns (manifest, cacheable)."""
+    cacheable = True
+    apply_overlays = trusted
     logger.info(f"Loading manifest from {manifest_path}")
 
-    with open(manifest_path, "r") as f:
-        data = yaml.safe_load(f)
+    data = _parse_trusted_document(base) if trusted else yaml.safe_load(base)
 
     # Parse CLI alternatives
     cli_alternatives: dict[str, CLIAlternative] = {}
@@ -1267,36 +1492,34 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
 
     # Merge private/custom overlays over the shipped manifest (default path only).
     if apply_overlays:
-        for label, overlay_path in _overlay_manifest_paths():
-            if label == "project":
-                # A repository-supplied overlay is gated: unapproved, it must
-                # contribute nothing at all -- not a replacement, not an
-                # insertion, not a server_env patch -- so that
-                # `get_server("<added>")`, the manifest-backed predicate in
-                # tools/handlers.py, still answers None for it. User and env
-                # scope are the operator's own files and stay ungated.
-                content, decision = read_and_gate(overlay_path, "project_manifest")
-                if content is None:
+        for source in overlays:
+            label, overlay_path = source.label, source.path
+            if source.content is None:
+                if source.label == "project":
                     # One WARNING for one refusal: returning before the parser
                     # runs keeps an unreadable overlay from also logging
                     # "Skipping unreadable manifest overlay".
-                    log_refusal(decision, logger)
-                    continue
-                # Parse the bytes the gate judged. Re-opening `overlay_path`
-                # here would apply content nobody approved.
-                (
-                    overlay_servers,
-                    overlay_clis,
-                    overlay_server_env,
-                    overlay_server_version,
-                ) = _parse_overlay_document(overlay_path, content)
-            else:
-                (
-                    overlay_servers,
-                    overlay_clis,
-                    overlay_server_env,
-                    overlay_server_version,
-                ) = _load_overlay_file(overlay_path)
+                    log_refusal(source.decision, logger)
+                else:
+                    logger.warning(
+                        f"Skipping unreadable manifest overlay {overlay_path}: "
+                        f"{source.error}"
+                    )
+                if source.state == "unreadable":
+                    cacheable = False
+                continue
+            failures: list[str] = []
+            # Parse the bytes that were read (and, for the project overlay,
+            # judged by the gate). Re-opening `overlay_path` here would apply
+            # content nobody approved.
+            (
+                overlay_servers,
+                overlay_clis,
+                overlay_server_env,
+                overlay_server_version,
+            ) = _parse_overlay_document(overlay_path, source.content, failures)
+            if failures:
+                cacheable = False
             if (
                 overlay_servers
                 or overlay_clis
@@ -1368,4 +1591,4 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
         f"{len(servers)} servers ({len(manifest.get_auto_start_servers())} auto-start)"
     )
 
-    return manifest
+    return manifest, cacheable
diff --git a/tests/conftest.py b/tests/conftest.py
index 0a0fedf..40fe4ed 100644
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -55,6 +55,7 @@ import pytest
 
 from pmcp import trust_store
 from pmcp.manifest import npm_resolver, package_identity, registry, version_checker
+from pmcp.manifest import loader as manifest_loader
 from pmcp.transport import http as transport_http
 from pmcp.env_store import reset_dotenv_keys, reset_pmcp_introduced_keys
 from pmcp.policy.policy import PolicyManager
@@ -396,6 +397,7 @@ def _reset_process_global_state() -> Iterator[None]:
         transport_http.reset_rate_limit_state()
         transport_http.reset_request_metrics()
         npm_resolver.reset_resolver_for_tests()
+        manifest_loader.clear_manifest_cache()
 
     _reset()
     yield
diff --git a/tests/test_version_pin.py b/tests/test_version_pin.py
index 5db4817..4f4eead 100644
--- a/tests/test_version_pin.py
+++ b/tests/test_version_pin.py
@@ -1188,35 +1188,6 @@ def _entry_doc(shipped: ServerConfig, kind: str | None, pin: str) -> dict[str, A
     return body
 
 
-@pytest.fixture
-def _memoized_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
-    """Parse each distinct YAML text once for the exhaustive precedence tests.
-
-    The two tests below call ``load_manifest()`` 432 times, and >99% of each
-    call is pure-Python ``yaml.safe_load`` re-parsing the same 78 KB shipped
-    manifest. Under coverage in CI that ran past the old 120 s
-    ``faulthandler_timeout`` on 3.11 and 3.12, and the faulthandler dump of the
-    still-running main thread then segfaulted the job (exit 139). Each
-    distinct YAML text is still parsed for real, once -- the cache is keyed on
-    the text, a parse error is never cached, and each caller gets a fresh copy so
-    nothing the loader mutates can leak into the next case. The copy is a
-    pickle round trip rather than ``copy.deepcopy``: deepcopy is pure Python
-    and, traced, costs a large share of the parse it replaces.
-    """
-    import pickle
-
-    real = yaml.safe_load
-    cache: dict[str | bytes, bytes] = {}
-
-    def safe_load(stream: Any) -> Any:
-        text = stream if isinstance(stream, (str, bytes)) else stream.read()
-        if text not in cache:
-            cache[text] = pickle.dumps(real(text))
-        return pickle.loads(cache[text])
-
-    monkeypatch.setattr(yaml, "safe_load", safe_load)
-
-
 def _check_precedence_case(
     case: tuple[tuple[str | None, str | None], ...],
     goods: list[tuple[str, str]],
@@ -1275,7 +1246,6 @@ def test_the_invalid_pin_text_is_true_in_every_two_source_case(
     monkeypatch: pytest.MonkeyPatch,
     tmp_path: Path,
     caplog: pytest.LogCaptureFixture,
-    _memoized_yaml: None,
 ) -> None:
     """Round 1 on Consiliency/pmcp#323 (F1, F2), same-file and later-file: two
     overlay sources (the user overlay, then `PMCP_MANIFEST_PATH`), each with any
@@ -1306,7 +1276,6 @@ def test_the_invalid_pin_text_is_true_with_sources_before_and_after_it(
     tmp_path: Path,
     approve_project_file: Any,
     caplog: pytest.LogCaptureFixture,
-    _memoized_yaml: None,
 ) -> None:
     """Round 1 on Consiliency/pmcp#323 (c7): a bad pin with an earlier source
     AND a later one -- user overlay, approved project overlay, then
````

## Test bodies (`tests/test_manifest_cache.py`, new, verbatim)

````python
"""The parsed-manifest cache (Consiliency/pmcp#233).

``load_manifest()`` is cached by the bytes of every source it reads, each
project overlay's consent decision, the ignored-redirect notice and the
platform. These tests pin the four rules a cache can break:

* never stale -- an overlay edit (even one that keeps size and mtime), a
  consent change, an env change, a cwd/HOME change or a platform change is a
  miss;
* never shared -- every caller gets its own deep copy;
* never caches a failure -- an exception, an unreadable source or an
  unparseable overlay is recomputed on the next call;
* warnings once per miss -- a hit is silent, a changed state warns again.

Plus the parser rule: libyaml only for pmcp's own shipped manifest, the
pure-Python SafeLoader for every overlay.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from pmcp import trust_store
from pmcp.env_store import record_pmcp_introduced_keys
from pmcp.manifest import loader
from pmcp.manifest.loader import load_manifest

_SHIPPED = loader._SHIPPED_MANIFEST_PATH


def _user_overlay(text: str) -> Path:
    path = Path.home() / ".pmcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _pin(version: str) -> str:
    return f'server_version:\n  firecrawl: "{version}"\n'


def _firecrawl_version() -> str | None:
    return load_manifest().servers["firecrawl"].version


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count cache misses: every miss, and only a miss, builds a manifest."""
    calls: list[int] = []
    real = loader._build_manifest

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(loader, "_build_manifest", counted)
    return calls


# ---------------------------------------------------------------------------
# A hit does no work; the shipped manifest goes through libyaml
# ---------------------------------------------------------------------------


def test_a_hit_parses_nothing(
    builds: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _user_overlay(_pin("3.25.5"))
    first = load_manifest()
    parses: list[int] = []
    monkeypatch.setattr(yaml, "load", lambda *a, **k: parses.append(1))
    monkeypatch.setattr(yaml, "safe_load", lambda *a, **k: parses.append(1))
    second = load_manifest()
    assert builds == [1] and parses == []
    assert second == first and second is not first


def _typed(value: Any) -> Any:
    """``value`` with every scalar paired with its exact type: ``==`` treats
    ``1 == True == 1.0``, so a plain comparison would miss an int/bool/float
    divergence between the two parsers."""
    if isinstance(value, dict):
        return ("dict", [(_typed(k), _typed(v)) for k, v in value.items()])
    if isinstance(value, list):
        return ("list", [_typed(v) for v in value])
    return (type(value).__name__, value)


def test_the_shipped_manifest_is_identical_under_both_loaders() -> None:
    content = _SHIPPED.read_bytes()
    assert _typed(loader._parse_trusted_yaml(content)) == _typed(
        yaml.load(content, Loader=yaml.SafeLoader)
    )


@pytest.mark.skipif(not yaml.__with_libyaml__, reason="PyYAML built without libyaml")
def test_the_shipped_manifest_uses_libyaml_when_available() -> None:
    assert loader._trusted_yaml_loader() is yaml.CSafeLoader


def test_without_libyaml_the_shipped_manifest_falls_back_to_safeloader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    monkeypatch.setattr(loader, "_trusted_document", None)
    assert loader._trusted_yaml_loader() is yaml.SafeLoader
    assert load_manifest().servers["firecrawl"].command == "npx"
    # The second shipped parse: its lru_cache is never reset, so clear it to
    # make the fallback actually run, and clear it again afterwards.
    loader._shipped_manifest_entries.cache_clear()
    try:
        assert "firecrawl" in loader._shipped_manifest_entries()
    finally:
        loader._shipped_manifest_entries.cache_clear()


def test_an_overlay_is_still_parsed_by_the_pure_python_safeloader(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """libyaml accepts a tab after `key:` that SafeLoader rejects (measured).
    An overlay must keep main's accept/reject behaviour: skipped, warned."""
    _user_overlay('server_version:\n  firecrawl:\t"3.25.5"\n')
    with caplog.at_level(logging.WARNING):
        assert _firecrawl_version() is None
    assert any("Skipping unreadable manifest overlay" in m for m in _warnings(caplog))


def test_the_parsed_shipped_document_is_keyed_by_its_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = tmp_path / "manifest.yaml"
    shipped.write_bytes(_SHIPPED.read_bytes())
    monkeypatch.setattr(loader, "_SHIPPED_MANIFEST_PATH", shipped)
    assert "firecrawl" in load_manifest().servers
    data = yaml.safe_load(shipped.read_bytes())
    data["servers"]["firecrawl"]["description"] = "edited in place"
    shipped.write_text(yaml.safe_dump(data))
    assert load_manifest().servers["firecrawl"].description == "edited in place"


# ---------------------------------------------------------------------------
# Never stale
# ---------------------------------------------------------------------------


def _rewrite_keeping_stat(path: Path, text: str) -> None:
    """Same size, same mtime: what a stat-keyed cache cannot tell apart."""
    before = path.stat()
    path.write_text(text)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def test_a_user_overlay_edit_is_seen_even_with_size_and_mtime_unchanged() -> None:
    path = _user_overlay(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"
    _rewrite_keeping_stat(path, _pin("3.25.6"))
    assert _firecrawl_version() == "3.25.6"


def test_an_env_overlay_edit_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    assert _firecrawl_version() == "1.0.1"
    _rewrite_keeping_stat(explicit, _pin("1.0.2"))
    assert _firecrawl_version() == "1.0.2"


def test_an_overlay_appearing_and_disappearing_is_seen() -> None:
    assert _firecrawl_version() is None
    path = _user_overlay(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"
    path.unlink()
    assert _firecrawl_version() is None


def test_a_consent_change_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(_pin("2.0.1"))
    monkeypatch.chdir(tmp_path / "proj")

    assert _firecrawl_version() is None  # unapproved: contributes nothing
    approve_project_file(project)
    assert _firecrawl_version() == "2.0.1"  # approval alone is a miss
    assert trust_store.revoke(project)
    assert _firecrawl_version() is None  # revocation alone is a miss
    approve_project_file(project)
    assert _firecrawl_version() == "2.0.1"
    _rewrite_keeping_stat(project, _pin("2.0.2"))
    assert _firecrawl_version() is None  # edited bytes are not the approved bytes


def test_an_env_change_is_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text(_pin("1.0.1"))
    b.write_text(_pin("1.0.2"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(a))
    assert _firecrawl_version() == "1.0.1"
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(b))
    assert _firecrawl_version() == "1.0.2"
    monkeypatch.delenv("PMCP_MANIFEST_PATH")
    assert _firecrawl_version() is None


def test_an_env_provenance_change_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Same value, but now introduced by pmcp rather than exported: ignored."""
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    assert _firecrawl_version() == "1.0.1"
    record_pmcp_introduced_keys({"PMCP_MANIFEST_PATH"})
    with caplog.at_level(logging.WARNING):
        assert _firecrawl_version() is None
    assert any("PMCP_MANIFEST_PATH" in m for m in _warnings(caplog))


def test_a_cwd_change_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    for name, version in (("one", "1.0.1"), ("two", "1.0.2")):
        project = tmp_path / name / ".pmcp" / "manifest.yaml"
        project.parent.mkdir(parents=True)
        project.write_text(_pin(version))
        approve_project_file(project)
    monkeypatch.chdir(tmp_path / "one")
    assert _firecrawl_version() == "1.0.1"
    monkeypatch.chdir(tmp_path / "two")
    assert _firecrawl_version() == "1.0.2"


def test_a_home_change_is_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _user_overlay(_pin("1.0.1"))
    assert _firecrawl_version() == "1.0.1"
    other = tmp_path / "other-home"
    monkeypatch.setenv("HOME", str(other))
    _user_overlay(_pin("1.0.2"))
    assert _firecrawl_version() == "1.0.2"


def test_a_platform_change_is_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_on_windows()` decides which launcher spellings are pinned."""
    _user_overlay(
        "servers:\n"
        "  winpin:\n"
        "    description: d\n"
        "    command: npx.cmd\n"
        "    args: ['-y', 'winpin-mcp']\n"
        "    version: '1.0.0'\n"
    )
    monkeypatch.setattr(loader, "_on_windows", lambda: False)
    assert load_manifest().servers["winpin"].version is None
    monkeypatch.setattr(loader, "_on_windows", lambda: True)
    assert load_manifest().servers["winpin"].version == "1.0.0"


def test_the_bytes_hashed_are_the_bytes_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overlay rewritten between the read and the parse must not be parsed:
    the result (and its cache entry) belongs to the bytes that were keyed."""
    path = _user_overlay(_pin("3.25.5"))
    real = loader._gather_overlay_sources

    def gather_then_rewrite(notices: list[str]) -> Any:
        sources = real(notices)
        path.write_text(_pin("9.9.9"))
        return sources

    monkeypatch.setattr(loader, "_gather_overlay_sources", gather_then_rewrite)
    assert _firecrawl_version() == "3.25.5"


# ---------------------------------------------------------------------------
# Never shared
# ---------------------------------------------------------------------------


def _mutable_ids(obj: Any, seen: set[int] | None = None) -> set[int]:
    """ids of every list/dict/set reachable from obj (dataclasses walked)."""
    seen = set() if seen is None else seen
    if isinstance(obj, (list, dict, set)):
        if id(obj) in seen:
            return seen
        seen.add(id(obj))
        items = obj.items() if isinstance(obj, dict) else enumerate(obj)
        for _k, v in items:
            _mutable_ids(v, seen)
    elif hasattr(obj, "__dataclass_fields__"):
        seen.add(id(obj))
        for name in obj.__dataclass_fields__:
            _mutable_ids(getattr(obj, name), seen)
    return seen


def test_no_two_callers_share_a_mutable_object() -> None:
    _user_overlay(_pin("3.25.5"))
    first, second = load_manifest(), load_manifest()  # miss, then hit
    third = load_manifest()  # hit
    a, b, c = _mutable_ids(first), _mutable_ids(second), _mutable_ids(third)
    assert not (a & b) and not (b & c) and not (a & c)


def test_mutating_a_result_never_reaches_the_next_caller() -> None:
    first = load_manifest()
    server = first.servers["firecrawl"]
    server.args.append("--evil")
    server.extra_env["NODE_OPTIONS"] = "--require /tmp/x.js"
    server.keywords.clear()
    for argv in server.install.values():
        argv.append("--evil")
    server.version = "0.0.1"
    first.servers.pop("github", None)
    first.cli_alternatives.clear()

    fresh = load_manifest()
    assert "--evil" not in fresh.servers["firecrawl"].args
    assert "NODE_OPTIONS" not in fresh.servers["firecrawl"].extra_env
    assert fresh.servers["firecrawl"].keywords
    assert all("--evil" not in a for a in fresh.servers["firecrawl"].install.values())
    assert fresh.servers["firecrawl"].version is None
    assert "github" in fresh.servers and fresh.cli_alternatives


# ---------------------------------------------------------------------------
# Never caches a failure
# ---------------------------------------------------------------------------


def test_an_exception_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = loader._parse_server_config
    state = {"fail": True}

    def flaky(name: str, data: dict[str, Any]) -> Any:
        if state["fail"]:
            raise RuntimeError("boom")
        return real(name, data)

    monkeypatch.setattr(loader, "_parse_server_config", flaky)
    with pytest.raises(RuntimeError):
        load_manifest()
    state["fail"] = False
    assert "firecrawl" in load_manifest().servers


def test_a_missing_explicit_manifest_is_not_cached(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    with pytest.raises(FileNotFoundError):
        load_manifest(path)
    path.write_text("servers: {}\ncli_alternatives: {}\n")
    assert load_manifest(path).servers == {}


@pytest.mark.parametrize(
    "broken",
    ["server_version: [unclosed\n", "- a list, not a mapping\n"],
    ids=["yaml-error", "not-a-mapping"],
)
def test_an_unparseable_overlay_is_recomputed_every_call(
    broken: str, builds: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    path = _user_overlay(broken)
    with caplog.at_level(logging.WARNING):
        load_manifest()
        load_manifest()
    skipped = [m for m in _warnings(caplog) if "manifest overlay" in m]
    assert builds == [1, 1] and len(skipped) == 2  # not cached: warns each call
    path.write_text(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"


def test_an_unreadable_overlay_is_recomputed_every_call(builds: list[int]) -> None:
    path = Path.home() / ".pmcp" / "manifest.yaml"
    path.mkdir(parents=True)  # exists, but read_bytes() raises IsADirectoryError
    load_manifest()
    load_manifest()
    assert builds == [1, 1]
    path.rmdir()
    _user_overlay(_pin("3.25.5"))
    assert _firecrawl_version() == "3.25.5"


# ---------------------------------------------------------------------------
# Warnings: once per miss
# ---------------------------------------------------------------------------


def test_warnings_are_emitted_once_per_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = _user_overlay('server_version:\n  firecrawl: "^3"\n')
    with caplog.at_level(logging.WARNING):
        load_manifest()
    assert len([m for m in _warnings(caplog) if m.startswith("Ignoring a '")]) == 1

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        load_manifest()  # hit: the operator has already been told
    assert _warnings(caplog) == []

    caplog.clear()
    path.write_text('server_version:\n  firecrawl: "~3"\n')
    with caplog.at_level(logging.WARNING):
        load_manifest()  # a new state is a new miss: told again
    assert len([m for m in _warnings(caplog) if m.startswith("Ignoring a '")]) == 1


def test_a_consent_refusal_is_logged_once_per_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(_pin("2.0.1"))
    monkeypatch.chdir(tmp_path / "proj")
    with caplog.at_level(logging.WARNING):
        load_manifest()
        load_manifest()
    refusals = [m for m in _warnings(caplog) if "pmcp trust approve" in m]
    assert len(refusals) == 1


def test_an_ignored_redirect_is_logged_once_per_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    record_pmcp_introduced_keys({"PMCP_MANIFEST_PATH"})
    with caplog.at_level(logging.WARNING):
        load_manifest()
        load_manifest()
    assert len([m for m in _warnings(caplog) if "PMCP_MANIFEST_PATH" in m]) == 1


def test_an_ignored_redirect_after_a_clean_load_is_still_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unset, then set by pmcp itself: the overlay list is the same (empty)
    both times, so only the notice in the key makes the second load a miss."""
    load_manifest()
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_pin("1.0.1"))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    record_pmcp_introduced_keys({"PMCP_MANIFEST_PATH"})
    with caplog.at_level(logging.WARNING):
        assert _firecrawl_version() is None
    assert any("PMCP_MANIFEST_PATH" in m for m in _warnings(caplog))


def test_clearing_the_cache_warns_again(caplog: pytest.LogCaptureFixture) -> None:
    _user_overlay('server_version:\n  firecrawl: "^3"\n')
    load_manifest()
    loader.clear_manifest_cache()
    with caplog.at_level(logging.WARNING):
        load_manifest()
    assert any(m.startswith("Ignoring a '") for m in _warnings(caplog))


def _refusals(caplog: pytest.LogCaptureFixture) -> int:
    return len([m for m in _warnings(caplog) if "pmcp trust approve" in m])


def _count_per_load(caplog: pytest.LogCaptureFixture, count: Any, step: Any) -> int:
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        step()
        load_manifest()
    return int(count(caplog))


def test_every_consent_transition_warns_even_when_the_state_is_still_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    approve_project_file: Any,
) -> None:
    """Round 1 (Consiliency/pmcp#331, codex B2 / claude F1): unapproved ->
    approve -> revoke reused the first state's LRU entry and was silent. One
    warning per transition into a refusal, none while it stays (C-12)."""
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(_pin("2.0.1"))
    monkeypatch.chdir(tmp_path / "proj")

    def nothing() -> None:
        return None

    def approve() -> None:
        approve_project_file(project)

    def revoke() -> None:
        assert trust_store.revoke(project)

    steps = [nothing, nothing, approve, revoke, nothing, approve, revoke]
    counts = [_count_per_load(caplog, _refusals, step) for step in steps]
    assert counts == [1, 0, 0, 1, 0, 0, 1]


def test_retargeting_a_project_overlay_symlink_is_a_new_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1 (codex B2): one unapproved target, then another. The refusal
    names the resolved file, so its identity must be part of the key."""
    pmcp_dir = tmp_path / "proj" / ".pmcp"
    pmcp_dir.mkdir(parents=True)
    (pmcp_dir / "one.yaml").write_text(_pin("2.0.1"))
    (pmcp_dir / "two.yaml").write_text(_pin("2.0.1"))
    link = pmcp_dir / "manifest.yaml"
    link.symlink_to(pmcp_dir / "one.yaml")
    monkeypatch.chdir(tmp_path / "proj")

    def nothing() -> None:
        return None

    def retarget(name: str) -> Any:
        def step() -> None:
            link.unlink()
            link.symlink_to(pmcp_dir / name)

        return step

    named: list[str] = []

    def refusals_naming(caplog: pytest.LogCaptureFixture) -> int:
        lines = [m for m in _warnings(caplog) if "pmcp trust approve" in m]
        named.extend(lines)
        return len(lines)

    steps = [nothing, retarget("two.yaml"), nothing, retarget("one.yaml")]
    counts = [_count_per_load(caplog, refusals_naming, step) for step in steps]
    assert counts == [1, 1, 0, 1]
    assert "one.yaml" in named[0] and "two.yaml" in named[1]


def test_breaking_fixing_and_breaking_again_warns_each_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 1 (claude F1): the same broken bytes twice, with a fix between."""
    bad, good = 'server_version:\n  firecrawl: "^3"\n', _pin("3.25.5")
    path = _user_overlay(bad)

    def bad_pins(caplog: pytest.LogCaptureFixture) -> int:
        return len([m for m in _warnings(caplog) if m.startswith("Ignoring a '")])

    def write(text: str) -> Any:
        return lambda: path.write_text(text)

    steps = [write(bad), write(bad), write(good), write(bad), write(bad)]
    assert [_count_per_load(caplog, bad_pins, step) for step in steps] == [
        1,
        0,
        0,
        1,
        0,
    ]


def _deep_alias_overlay(depth: int) -> str:
    """Round 1 (codex B1): chained anchors. 11 KB at depth 500; parses with
    SafeLoader, but the result nests deeper than pickle can recurse."""
    text = "d0: &d0 {v: 0}\n" + "".join(
        f"d{i}: &d{i} {{v: *d{i - 1}}}\n" for i in range(1, depth + 1)
    )
    return text + (
        "servers:\n"
        "  custom:\n"
        "    description: deep\n"
        "    command: echo\n"
        f"    discovery_metadata: *d{depth}\n"
    )


@pytest.mark.parametrize("depth", [500, 2000])
def test_a_result_that_cannot_be_pickled_is_returned_uncached(
    depth: int, builds: list[int], caplog: pytest.LogCaptureFixture
) -> None:
    _user_overlay(_deep_alias_overlay(depth))
    with caplog.at_level(logging.DEBUG, logger="pmcp.manifest.loader"):
        first, second = load_manifest(), load_manifest()
    assert "custom" in first.servers and "custom" in second.servers
    assert len(first.servers) == len(second.servers)
    assert builds == [1, 1] and len(loader._manifest_cache) == 0
    debug = [r.getMessage() for r in caplog.records if "not cached" in r.getMessage()]
    assert debug and all("'v'" not in m and "d0" not in m for m in debug)


def test_a_cached_result_that_cannot_be_read_back_is_rebuilt(
    monkeypatch: pytest.MonkeyPatch, builds: list[int]
) -> None:
    _user_overlay(_pin("3.25.5"))
    load_manifest()

    def broken(_blob: bytes) -> Any:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(loader.pickle, "loads", broken)
    assert _firecrawl_version() == "3.25.5"
    assert builds == [1, 1]


# ---------------------------------------------------------------------------
# Threads, bound, and the request path
# ---------------------------------------------------------------------------


def test_concurrent_cold_callers_build_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = loader._build_manifest
    calls: list[int] = []

    def slow(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        time.sleep(0.2)  # widen the window a missing lock would race in
        return real(*args, **kwargs)

    monkeypatch.setattr(loader, "_build_manifest", slow)
    barrier = threading.Barrier(6)
    results: list[Any] = []

    def worker() -> None:
        barrier.wait()
        results.append(load_manifest())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]
    assert len(results) == 6 and all(r == results[0] for r in results)
    assert len({id(r) for r in results}) == 6


def test_the_cache_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit.yaml"
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    for i in range(loader._MANIFEST_CACHE_SLOTS + 5):
        explicit.write_text(_pin(f"1.0.{i}"))
        load_manifest()
    assert len(loader._manifest_cache) == loader._MANIFEST_CACHE_SLOTS


@pytest.mark.asyncio
async def test_a_catalog_search_builds_the_manifest_at_most_once(
    builds: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's shape: one search used to parse the manifest up to 13 times
    (2 direct, 1 via load_configs, 1 per manifest and registry candidate)."""
    from unittest.mock import MagicMock

    from pmcp.manifest.registry import RegistryCache, RegistryServerEntry
    from pmcp.tools.handlers import GatewayTools

    entries = [
        RegistryServerEntry(name=f"io.example/{w}", description=f"{w} search database")
        for w in ("alpha", "beta", "gamma", "delta", "eps")
    ]

    async def registry(self: Any) -> RegistryCache:
        return RegistryCache(
            schema_version="v1", source_endpoint="x", fetched_at="now", servers=entries
        )

    monkeypatch.setattr(GatewayTools, "_load_registry_candidates", registry)
    calls: list[int] = []
    real_load = loader.load_manifest

    def counted() -> Any:
        calls.append(1)
        return real_load()

    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", counted)
    monkeypatch.setattr(loader, "load_manifest", counted)
    cm = MagicMock()
    cm.get_all_tools.return_value = []
    cm.is_server_online.return_value = False
    cm.get_all_server_statuses.return_value = []
    pm = MagicMock()
    pm.is_tool_allowed.return_value = True
    pm.is_server_allowed.return_value = True
    pm.scoped_advisor_active = False
    tools = GatewayTools(client_manager=cm, policy_manager=pm)
    query = {"query": "search database browser web git", "include_offline": True}
    await tools.catalog_search(query)
    await tools.catalog_search(query)
    assert len(calls) >= 12  # still called per site (8 per search here) ...
    assert builds == [1]  # ... but built once
````

## Appendix: call counter (`count2.py`)

````python
"""Spike harness (not shipped): per-catalog_search parse counts on main."""
import asyncio, collections, json, os, sys, tempfile, time, traceback
from pathlib import Path
from unittest.mock import MagicMock
mode = sys.argv[1] if len(sys.argv) > 1 else "py"
tmp = Path(tempfile.mkdtemp(dir=os.environ["SCR"]))
home = tmp / "home"; (home / ".pmcp").mkdir(parents=True)
proj = home / "proj"; (proj / ".git").mkdir(parents=True)
(home/".mcp.json").write_text(json.dumps({"mcpServers": {"github": {"command":"npx","args":["-y","@modelcontextprotocol/server-github"]}, "custom": {"command":"node","args":["x.js"]}}}))
(home/".pmcp"/"manifest.yaml").write_text("server_env:\n  firecrawl:\n    FIRECRAWL_API_URL: http://x\n")
os.environ["HOME"] = str(home); os.chdir(proj)
import yaml
if mode == "C":
    _orig = yaml.safe_load
    yaml.safe_load = lambda s: yaml.load(s, Loader=yaml.CSafeLoader)
import pmcp.manifest.loader as L, pmcp.tools.handlers as H, pmcp.config.loader as CL
import pmcp.manifest.registry as R, pmcp.project_consent as PC
from pmcp.manifest.registry import RegistryServerEntry, RegistryCache, RegistryPackage
counts = collections.Counter(); sites = collections.Counter(); tm = collections.Counter()
SELF = __file__
def wrap(mod, name, key, also=()):
    orig = getattr(mod, name)
    def w(*a, **k):
        counts[key] += 1
        for fr in reversed(traceback.extract_stack()[:-1]):
            if fr.filename != SELF:
                sites[(key, f"{Path(fr.filename).name}:{fr.lineno} {fr.name}")] += 1; break
        t0 = time.perf_counter()
        try: return orig(*a, **k)
        finally: tm[key] += time.perf_counter() - t0
    setattr(mod, name, w)
    for m in also: setattr(m, name, w)
wrap(L, "load_manifest", "load_manifest", also=[H])
wrap(yaml, "safe_load", "yaml.safe_load")
wrap(H, "load_configs", "load_configs")
wrap(PC, "read_and_gate", "read_and_gate", also=[L, CL])
wrap(H, "load_dotenv", "load_dotenv")
wrap(R, "load_registry_cache", "load_registry_cache", also=[H] if hasattr(H, "load_registry_cache") else [])
# registry cache with 5 entries matching the query, written where load_registry_cache looks
entries = [RegistryServerEntry(name=f"io.example/{w}-server", description=f"{w} search database browser", packages=[RegistryPackage(registry_type="npm", identifier=f"@ex/{w}")] if "registry_type" in RegistryPackage.__dataclass_fields__ else []) for w in ["alpha","beta","gamma","delta","eps"]]
async def reg(self): return RegistryCache(schema_version="v1", source_endpoint="x", fetched_at="now", servers=entries)
H.GatewayTools._load_registry_candidates = reg
cm = MagicMock(); cm.get_all_tools.return_value = []; cm.is_server_online.return_value = False; cm.get_all_server_statuses.return_value = []
pm = MagicMock(); pm.is_tool_allowed.return_value = True; pm.is_server_allowed.return_value = True; pm.scoped_advisor_active = False
gt = H.GatewayTools(client_manager=cm, policy_manager=pm)
async def main():
    for label, inp in [("worst: query+offline", {"query": "search database browser web git", "include_offline": True}),
                       ("query only", {"query": "search database browser web git"}), ("no query", {})]:
        await gt.catalog_search(inp)  # warm (imports, probe caches)
        counts.clear(); sites.clear(); tm.clear()
        N = 3; t0 = time.perf_counter()
        for _ in range(N): out = await gt.catalog_search(inp)
        el = (time.perf_counter() - t0) / N
        print(f"== {label}: {el*1000:.0f} ms/search; per search: " + ", ".join(f"{k}={v/N:g}" for k, v in counts.items()) + "; ms/search: " + ", ".join(f"{k}={v*1000/N:.0f}" for k, v in tm.items()))
        print(f"   candidates={len(getattr(out,'candidates',[]) or [])}")
        for (k, s), n in sorted(sites.items()): print(f"   {k:18} {n/N:4g}/search  {s}")
asyncio.run(main())
````

## Appendix: per-call timer (`percall.py`)

````python
"""Per-call load_manifest cost (spike harness). Usage: percall.py main|mainC|spike"""
import os, sys, statistics, tempfile, time
from pathlib import Path
mode = sys.argv[1]
tmp = Path(tempfile.mkdtemp(dir=os.environ["SCR"])); home = tmp / "home"; (home / ".pmcp").mkdir(parents=True)
proj = home / "proj"; (proj / ".git").mkdir(parents=True)
(home / ".pmcp" / "manifest.yaml").write_text("server_env:\n  firecrawl:\n    FIRECRAWL_API_URL: http://x\n")
os.environ["HOME"] = str(home); os.chdir(proj)
import yaml
if mode == "mainC":
    yaml.safe_load = lambda s: yaml.load(s, Loader=yaml.CSafeLoader)
import pmcp.manifest.loader as L
L.load_manifest()
def t(fn, n=25):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); xs.append(time.perf_counter() - t0)
    return f"median {statistics.median(xs)*1000:7.2f} ms  min {min(xs)*1000:7.2f} ms"
if mode.startswith("main"):
    print(f"{mode:6} every call (no cache):        ", t(L.load_manifest))
else:
    def cold():
        L.clear_manifest_cache(); L._trusted_document = None; L.load_manifest()
    def l1warm():
        L.clear_manifest_cache(); L.load_manifest()
    print("spike  miss, cold (parse shipped):     ", t(cold))
    print("spike  miss, shipped parse cached (L1):", t(l1warm))
    print("spike  hit:                            ", t(L.load_manifest, 200))
````

## Appendix: suite counting plugin (`plug/loadcount.py`)

````python
"""Spike-only pytest plugin: count load_manifest calls / misses across the suite."""
import time, json, os
_stats = {"calls": 0, "misses": 0, "miss_s": 0.0, "call_s": 0.0}

def pytest_configure(config):
    import pmcp.manifest.loader as L
    og, ob = L._gather_overlay_sources, L._build_manifest
    def g(*a, **k):
        _stats["calls"] += 1
        return og(*a, **k)
    def b(*a, **k):
        _stats["misses"] += 1; t0 = time.perf_counter()
        try: return ob(*a, **k)
        finally: _stats["miss_s"] += time.perf_counter() - t0
    L._gather_overlay_sources, L._build_manifest = g, b

def pytest_unconfigure(config):
    with open(os.environ["LOADCOUNT_OUT"], "w") as f: json.dump(_stats, f)
````

## Appendix: mutation driver (`mutants.py`)

````python
"""Mutation driver for the Consiliency/pmcp#233 plan (spike only; not shipped).

Each mutant is an exact-text replacement in src/pmcp/manifest/loader.py. The
driver applies it, runs tests/test_manifest_cache.py, restores the file from a
saved copy (never git checkout) and cmp-checks the restore.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TARGET = ROOT / "src/pmcp/manifest/loader.py"
PRISTINE = TARGET.read_bytes()

MUTANTS: list[tuple] = [
    ("M1", "key on (mtime_ns, size), not content",
     "        _digest(source.content),\n        identity,",
     "        (source.path.stat().st_mtime_ns, source.path.stat().st_size) if source.content is not None else None,\n        identity,"),
    ("M2", "project overlay (consent) left out of the key",
     "        tuple(_source_key(o) for o in overlays),",
     "        tuple(_source_key(o) for o in overlays if o.label != 'project'),"),
    ("M3", "ignored-redirect notice left out of the key",
     "        tuple(notices),\n        _on_windows(),",
     "        _on_windows(),"),
    ("M4", "platform left out of the key",
     "        tuple(notices),\n        _on_windows(),",
     "        tuple(notices),"),
    ("M5", "the cached object itself is returned (store the object, no copy)",
     [("                return cast(Manifest, cached)", "                return cast(Manifest, blob)"),
      ("            cached = _deserialize(blob)", "            cached = blob"),
      ("        stored = _serialize(manifest) if cacheable else None", "        stored = manifest if cacheable else None")], None),
    ("M6", "shallow copy: new dicts, shared ServerConfig objects",
     [("            cached = _deserialize(blob)", "            cached = replace(blob, servers=dict(blob.servers), cli_alternatives=dict(blob.cli_alternatives))"),
      ("        stored = _serialize(manifest) if cacheable else None", "        stored = manifest if cacheable else None")], None),
    ("M7", "a degraded result (unreadable/unparseable source) is cached",
     "        stored = _serialize(manifest) if cacheable else None",
     "        stored = _serialize(manifest)"),
    ("M8", "notices logged on every call, not once per transition",
     "    global _last_served_key\n    with _manifest_cache_lock:\n        steady",
     "    global _last_served_key\n    for notice in notices:\n        logger.warning(notice)\n    notices = []\n    with _manifest_cache_lock:\n        steady"),
    ("M9", "no lock around lookup+build",
     "    global _last_served_key\n    with _manifest_cache_lock:\n        steady",
     "    global _last_served_key\n    if True:\n        steady"),
    ("M10", "overlays parsed with libyaml",
     "        data = yaml.safe_load(content)\n    except yaml.YAMLError as exc:",
     "        data = _parse_trusted_yaml(content)\n    except yaml.YAMLError as exc:"),
    ("M11", "shipped manifest parsed with the pure-Python SafeLoader",
     "    return getattr(yaml, \"CSafeLoader\", None) or yaml.SafeLoader",
     "    return yaml.SafeLoader"),
    ("M12", "parsed-shipped (L1) cache ignores the digest",
     "if cached is not None and cached[0] == digest:",
     "if cached is not None:"),
    ("M13", "overlay re-read at parse time (not the keyed bytes)",
     ") = _parse_overlay_document(overlay_path, source.content, failures)",
     ") = _parse_overlay_document(overlay_path, overlay_path.read_bytes(), failures)"),
    ("M14", "cache unbounded",
     "            while len(_manifest_cache) > _MANIFEST_CACHE_SLOTS:\n                _manifest_cache.popitem(last=False)\n",
     ""),
    ("M15", "no cache at all",
     "        blob = _manifest_cache.get(key)\n",
     "        blob = None\n"),
    ("M16", "shipped bytes left out of the key",
     "        _digest(base),\n",
     ""),
    ("M17", "fallback missing: CSafeLoader required",
     "    return getattr(yaml, \"CSafeLoader\", None) or yaml.SafeLoader",
     "    return yaml.CSafeLoader"),
    ("M18", "B1: serialization failure not contained (raw pickle.dumps)",
     "    try:\n        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)\n    except Exception as exc:  # noqa: BLE001 - any failure means \"do not cache\"",
     "    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)\n    try:\n        pass\n    except Exception as exc:  # noqa: BLE001 - any failure means \"do not cache\""),
    ("M19", "B1: read-back failure not contained (raw pickle.loads)",
     "    try:\n        return pickle.loads(blob)\n    except Exception as exc:  # noqa: BLE001 - any failure means \"rebuild\"",
     "    return pickle.loads(blob)\n    try:\n        pass\n    except Exception as exc:  # noqa: BLE001 - any failure means \"rebuild\""),
    ("M20", "B2: a transition into a cached state is a silent hit",
     "        if blob is not None and steady:",
     "        if blob is not None:"),
    ("M21", "B2: the refusal's identity (resolved path, remediation) left out of the key",
     "        identity,\n    )",
     "        None,\n    )"),
    ("M22", "B2: the last-served state is never recorded",
     "        _last_served_key = key\n        blob",
     "        blob"),
]

only = set(sys.argv[2:])
env = {k: v for k, v in os.environ.items() if k not in (
    "npm_config_cache", "npm_config_store_dir", "pnpm_config_store_dir")}
red = 0
for mid, desc, old, new in MUTANTS:
    if only and mid not in only:
        continue
    text = PRISTINE.decode()
    pairs = old if isinstance(old, list) else [(old, new)]
    for o, n in pairs:
        assert text.count(o) == 1, (mid, text.count(o))
        text = text.replace(o, n, 1)
    TARGET.write_text(text)
    try:
        r = subprocess.run(
            ["uv", "run", "pytest", "tests/test_manifest_cache.py", "-q", "-p",
             "no:cacheprovider", "--no-cov", "--cov-fail-under=0", "--tb=line",
             "-rf"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
        )
    finally:
        TARGET.write_bytes(PRISTINE)
    restored = TARGET.read_bytes() == PRISTINE
    out = r.stdout + r.stderr
    summary = (re.findall(r"^(?:=+ )?(\d+ failed.*|\d+ passed.*)$", out, re.M) or ["?"])[-1]
    failed = re.findall(r"^FAILED (\S+)", out, re.M)
    first = (re.findall(r"^E\s+(.*)$", out, re.M) or re.findall(r"^\S+:\d+: (.*)$", out, re.M) or [""])[0][:110]
    is_red = r.returncode != 0
    red += is_red
    print(f"| {mid} | {desc} | {'**RED**' if is_red else 'green'} {summary.strip('= ')} (restored={restored}) | {first} | {', '.join(f.split('::')[-1] for f in failed)} |", flush=True)
print(f"{red} of {len(only or MUTANTS)} red")
````

## Appendix: 28-file A/B, then the full suite (`ab2.sh`, round 2)

````bash
#!/bin/bash
set -u
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir
cd /home/viperjuice/workspace/worktrees/pmcp-233
mv tests/test_manifest_cache.py /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/test_manifest_cache.held.py
# A: main
cp /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/loader.orig.py src/pmcp/manifest/loader.py; cp /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/conftest.orig.py tests/conftest.py; cp /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/test_version_pin.orig.py tests/test_version_pin.py
echo "A main start $(date +%T)"; /usr/bin/time -f "A wall %e s maxrss %M KB" uv run pytest tests/test_baseline_constraints.py tests/test_cli.py tests/test_credential_gates_handlers.py tests/test_credential_gates_startup.py tests/test_credential_optionality_e2e.py tests/test_env_overlay_provenance.py tests/test_fresh_operator_baseline.py tests/test_install_child_env_project_root.py tests/test_integration.py tests/test_lazy_start.py tests/test_manifest_overlay.py tests/test_manifest_provision.py tests/test_manifest.py tests/test_package_identity_gate.py tests/test_phase4_e2e.py tests/test_phase6_tenant_code_mode.py tests/test_pkgid_manifest_npx_selectors.py tests/test_pkgid_panel_fixes.py tests/test_project_source_consent_config.py tests/test_project_source_consent_manifest.py tests/test_provision_validation.py tests/test_refresher.py tests/test_refusal_remedies.py tests/test_server_lifecycle.py tests/test_tools.py tests/test_trust_boundaries_composition.py tests/test_trust_boundaries_e2e.py tests/test_version_pin.py  -q -p no:cacheprovider --cov --cov-report= --cov-fail-under=0 2>&1 | tail -3
# B: spike
cp /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/loader.r2.py src/pmcp/manifest/loader.py; cp /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/conftest.spike.py tests/conftest.py; cp /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/test_version_pin.spike.py tests/test_version_pin.py
echo "B spike start $(date +%T)"; /usr/bin/time -f "B wall %e s maxrss %M KB" uv run pytest tests/test_baseline_constraints.py tests/test_cli.py tests/test_credential_gates_handlers.py tests/test_credential_gates_startup.py tests/test_credential_optionality_e2e.py tests/test_env_overlay_provenance.py tests/test_fresh_operator_baseline.py tests/test_install_child_env_project_root.py tests/test_integration.py tests/test_lazy_start.py tests/test_manifest_overlay.py tests/test_manifest_provision.py tests/test_manifest.py tests/test_package_identity_gate.py tests/test_phase4_e2e.py tests/test_phase6_tenant_code_mode.py tests/test_pkgid_manifest_npx_selectors.py tests/test_pkgid_panel_fixes.py tests/test_project_source_consent_config.py tests/test_project_source_consent_manifest.py tests/test_provision_validation.py tests/test_refresher.py tests/test_refusal_remedies.py tests/test_server_lifecycle.py tests/test_tools.py tests/test_trust_boundaries_composition.py tests/test_trust_boundaries_e2e.py tests/test_version_pin.py  -q -p no:cacheprovider --cov --cov-report= --cov-fail-under=0 2>&1 | tail -3
mv /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/test_manifest_cache.held.py tests/test_manifest_cache.py
echo AB-DONE
# then the full suite, alone, on the round-2 spike
free -h | sed -n 2p
echo "FULL start $(date +%T)"
cd /home/viperjuice/workspace/worktrees/pmcp-233 && PYTHONPATH=/tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/plug LOADCOUNT_OUT=/tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/loadcount.r2.json uv run pytest tests/ -q --tb=short --cov --cov-report= -p loadcount -p no:cacheprovider --durations=10 > /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/full_r2.log 2>&1
echo "FULL exit $?"; tail -3 /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/full_r2.log; cat /tmp/claude-1000/-home-viperjuice-code-pmcp/ade04d67-62c9-4ed6-b91f-982cd0fee7a1/scratchpad/233/loadcount.r2.json; echo ALL-DONE
````
