# Detailed plan: version pinning and drift detection for self-hosted-backed MCP clients (Consiliency/pmcp#294, proposals 1 and 2)

> **Bounded-plan verdict: within threshold.** Four source files change
> (`src/pmcp/manifest/loader.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/types.py`,
> `src/pmcp/cli.py`), plus one new test file, a one-stub edit to
> `tests/test_pkgid_panel_fixes.py` (keeps an existing test offline), and three docs
> (README, CHANGELOG, CONTRIBUTING). There are two
> conceptually distinct changes: (1) a first-class `version:` / `server_version:` pin that
> `gateway.update_server` reports on, and (2) an advisory warning for an unpinned client
> in front of a self-hosted backend. Proposals 3 (post-update smoke probe) and 4 (client
> version in error hints) are **named follow-up slices** with a design note each. They are
> not planned here.
>
> Every number below was **measured this session** on a throwaway spike on
> `plan/294-version-pin` at `9ca081e` (= `origin/main`). The spike was then reverted, so
> `src/` and `tests/` are byte-identical to HEAD in the commit that carries this plan
> (Verification step 9 re-checks that). The spike diff and the new test file are embedded
> verbatim below as the reference patch.

## Revision 3 (2026-09-26): board round 2 on `95968c8`

The claude seat returned DISAGREE with one blocker (B1) and four non-blocking items. It
confirmed every other round-1 resolution and reproduced the round-1 numbers. Each
item below was **reproduced first**, then fixed, and has a test that is red on the
revision-2 spike and green on revision 3, plus a mutant (M25-M33). All numbers were
re-measured on the revision-3 spike, which was then reverted.

| # | finding | resolution | evidence |
|---|---|---|---|
| **B1** (blocking) | Tarball specs slipped through the P1 allowlist. npm treats `name@corp.tgz`, `name@x.tar` and `name@x.tar.gz` (any case), and a bare **unscoped** `corp.tgz` slot, as local tarball **files**. The rev-2 tag regex read them as dist-tags, so `firecrawl-mcp@corp-mcp.TGZ` plus a pin became the public `firecrawl-mcp@3.25.5`: dependency confusion. | npm's own rule, verbatim from npm-package-arg: `isFileType = /[.](?:tgz\|tar\.gz\|tar)$/i`. It is applied to the selector **and** to an unscoped name, exactly where npa applies it; a scoped name is exempt, as in npa. The allowlist was then **re-derived class by class** against npa's classifier (the class table below, in D3), which also closed N4. The rule is pure grammar in the new public `split_plain_registry_spec`. It never consults the resolver, so pins keep working while identity is disabled. | **Reproduced** end to end with the seat's overlay (temp HOME): main `9ca081e` gives `['-y','firecrawl-mcp@corp-mcp.TGZ']`; rev 2 (per the seat, and per the `grammar_conformance` probe below) gives `['-y','firecrawl-mcp@3.25.5']`, silently; **rev 3 gives `version: None args: ['-y','firecrawl-mcp@corp-mcp.TGZ']` plus the WARNING** `Ignoring version pin '3.25.5' for server 'corp-mcp': its args name no plain registry package ...`. **Grammar conformance against the host's real npa** (461 crafted slots): rev-2 rule, **136 violations** (accepted slots that npa classifies as `file`, as `range`, or as invalid); **rev-3 rule, 0 violations**. The P1 test now has one case per npa class (25 ids): **12 red on rev 2** (every tarball form, both bare tarballs, `x`, `X`, `v1.2.x`, and the trailing newline), green on rev 3. A new positive-control test covers 8 accepted classes. Mutant **M26** (drop the name clause) is red on **`bare-tarball-tgz`, `bare-tarball-TAR` and `tarball-name-with-version`**. M25 (drop the selector clause) is red on all 5 tarball-selector ids. |
| **N1** | Two cache-key components had no test: the project overlay and the trust store. | Added `test_fingerprint_changes_when_a_project_overlay_appears` and `test_fingerprint_changes_when_the_project_overlay_is_approved`. The latter uses `approve_project_file`, which changes only the trust store, and asserts that the approved `server_version` then loads. | Mutant **M32** (`parts.append(None)` for the project line) is red on the first test. **M33** (the same for the trust-store line) is red on the second. Before revision 3, both mutants survived (the seat measured 70 passed). |
| **N2** | The warning and `pmcp update` went silent when npm identity was disabled, which is dev0's default (`npm_config_cache`). | **I chose the structural fallback, and it fails loud when that can't decide.** For an npx launch whose identity is refused, the warning reads the slot with the provision gate's `_package_slot` and the same `split_plain_registry_spec` grammar, and says so (`(read from the argv: npm package identity is unavailable)`). An exact pin stays silent. When even the grammar can't name a plain registry package, it warns `pmcp cannot verify that its client is pinned: npm package identity is unavailable (see gateway_diagnostics.npm_identity) ...`. `pmcp update` prints the same warning under its result through the update wrapper. The update result itself is the pre-existing "Could not determine a registry package", because moving a package needs identity (#195) and that stays refused. | **Reproduced** with `npm_config_cache=/tmp/...` set: identity is `('unknown', None)`. rev 2 gives `warnings: []`. **rev 3 gives `'firecrawl' ... npm:firecrawl-mcp is unpinned (read from the argv: npm package identity is unavailable) ...`.** There are 3 tests (unpinned, exact pin, unreadable slot), using the `npm_identity_disabled` fixture. **2 are red on rev 2** (the exact-pin control is green on both). Mutants M29 and M30. |
| **N3** | Build metadata was refused only for manifest pins. A `.pmcp.json` argv `firecrawl-mcp@3.25.5+evil` was labelled `[PINNED] 3.25.5+evil`. | **The label says what npm runs.** A configured argv is the operator's own, so pmcp does not refuse it. `_is_exact_pin` keeps treating it as exact, correctly: npm runs exactly 3.25.5, so the warning stays suppressed. `update_server` now reports `pinned_version="3.25.5"`, compares 3.25.5 against latest, and its message says `(build metadata '+evil' is ignored by npm)`. The same applies to cargo. The manifest path still refuses `+` outright (round-1 N2). | `test_update_server_labels_build_metadata_with_what_npm_runs`: **red on rev 2** (`('3.25.5+evil', 'newer') == ('3.25.5', 'newer')`), green on rev 3. Mutant M31. |
| **N4** (nit) | `latest\n` was accepted (`match` rather than `fullmatch`), and `x`/`X` were accepted as "tags" although npm treats them as ranges. | The tag word is now `fullmatch`. Letter-led version or range words (`x`, `X`, `x.x`, `v1`, `v1.2.x`, `x-beta`) are **refused** by a partial-version-word clause, consistent with refusing every range. The clause is deliberately broad: a real tag it also catches only loses its pin, with a warning. | The P1 test ids `range-x`, `range-X`, `range-v-partial` and `tag-trailing-newline` are red on rev 2. Mutants M27 (range words) and M28 (`match`; red on `tag-trailing-newline` and 7 others). |
| **N5** | This was already documented. | Unchanged, as instructed. | None needed. |

**Scope growth.** There is still no new source file. `loader.py` gains the
`split_plain_registry_spec` grammar (three module regexes), and `handlers.py` gains the
identity-disabled fallback and the build-metadata label.

## Revision 2 (2026-09-26): board round on Consiliency/pmcp#295 @ `9d08184`

The board reached quorum: gemini AGREE, claude PARTIALLY AGREE, codex DISAGREE with 3
blocking findings, and grok timed out. Every finding is resolved below. Each blocking
one was **reproduced first**, then fixed, and is proved by a new test that is red on the
revision-1 spike and green on revision 2, plus a mutant (mutation table, M15-M24). All
numbers below were re-measured on the revision-2 spike, which was then reverted.

| # | finding | resolution | evidence |
|---|---|---|---|
| codex P1 (blocking) + claude N1 | The materialiser rewrote an alias `myalias@npm:firecrawl-mcp@3.25.5` to `myalias@3.25.5`, which is a different registry package. | `_pin_npx_args` now pins only a **plain registry spec**, defined by grammar as an allowlist: `name`, or `name@<selector>` where the selector is one exact SemVer version (`is_valid_package_version`) or a dist-tag (`package_identity._DIST_TAG_RE`, a letter-led `[A-Za-z0-9._-]` word). Everything else npm accepts after `name@` is refused with a WARNING and the entry stays unpinned: aliases, URLs, git/`github:`, `file:`/tarball, and also ranges (a range selects a set). See D3 step 3. | Reproduced with the real npm resolver: `detect_package_type("npx", ["-y","myalias@npm:firecrawl-mcp@3.25.5"])` gives `('unknown', None)`, and `["-y","myalias@3.25.5"]` gives `('npm', 'myalias')`. Test `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec` covers 7 cases, all **7 red on rev 1** (`assert '3.25.5' is None`) and green on rev 2. `test_version_replaces_a_dist_tag_slot` is the positive control. Mutants M15 (7 red) and M16 (2 red). |
| codex P2 (blocking) + claude F1 | A range or dist-tag (`^3.25.5`, `~`, `next`) silenced the self-hosted warning, and `pmcp update` labelled a range `[PINNED] ... pinned at ^3.25.0`. | New `_is_exact_pin(package_type, pin)`: exact means `is_valid_package_version` in every ecosystem, plus a docker `sha256:` digest and a PEP 440 `==X` with no wildcard. The warning suppresses only on an **exact** pin, and names a non-exact one: `floats on '^3.25.5' (a range or tag, not one exact version)`. `update_server`'s refusal is **unchanged**: it still does not move what the operator chose. It now reports a non-exact selector as `floating_selector` (with `pinned_version=None`, `latest_comparison=None`) and a message that starts `'fc' is held at '^3.25.0'`. `pmcp update` prints **`[FLOATING] fc: held at ^3.25.0, a range or tag that re-resolves at every spawn (latest 3.26.0)`**. See D6 and D7. | `test_health_warns_on_a_range_or_dist_tag` (4 specs), `test_update_server_reports_a_range_as_floating_not_pinned` and `test_pmcp_update_renders_a_range_as_floating`: **6 red on rev 1** (`assert 0 == 1` ×4, `AttributeError: ... 'floating_selector'`, `['[FAILED] fc: long message']`), all green on rev 2. Mutants M17 (5 red), M18 (1) and M24 (1). |
| codex P3 (blocking) | One malformed entry aborted the whole manifest load: `args: ["-y", 123]` plus `version:` raised `AttributeError`. | Materialisation is contained **per entry** (`_materialize_version_pin_soft`). Any exception costs that entry its pin, with a WARNING, and everything else loads. | Reproduced with the same overlay (user `~/.pmcp/manifest.yaml`, entry `malformed`, `args: ["-y", 123]`, `version: "1.2.3"`): **main `9ca081e`: `entries: 108`**; **rev 1: `LOAD FAILED: AttributeError 'int' object has no attribute 'startswith'`**; **rev 2: `entries: 108 malformed.version: None`**. Test `test_a_pin_on_a_malformed_entry_costs_only_that_entry` covers int in args, int in an install argv, and int `command`, asserting `len(servers) == shipped + 1`: **3 red on rev 1** (`AttributeError` ×2, `TypeError`), green on rev 2. Mutant M19 (3 red). |
| claude F2 | A relaxer that the child inherits from the gateway's own environment never triggered the warning. | The **advisory** warning now judges the relaxer on `sanitized_subprocess_env(resolved.config.env, project_root)`, the exact environment `client/manager.py:2409` spawns with (the gateway's env minus managed secrets, plus the entry's env). The credential gate is **unchanged**: `credential_requirement`'s gate callers still pass `config.env`. For a gate, ignoring the ambient value is the safe direction (#124). For a warning, including it is. `tests/test_credential_predicate_guard.py` (no `os.environ` passed as `child_env`) stays green. | `test_health_warns_when_the_relaxer_comes_from_the_gateway_environment` (`monkeypatch.setenv("SELFHOST_API_URL", ...)`, empty `extra_env`) is **red on rev 1** (`assert 0 == 1`) and green on rev 2. In the same test, `credential_requirement(server).required is True` proves the gate did not move. Mutant M20 (1 red). |
| claude F3 | `gateway.health` loaded the manifest once or twice per call (~92 ms each), and logged a WARNING per load while an unapproved project overlay was present. | Health no longer reads config files at all. (1) The relaxer-declaring manifest entries are cached by `manifest_sources_fingerprint()`, a new `stat`-only key over the shipped manifest, the user overlay, the project overlay found by the cwd walk, the `$PMCP_MANIFEST_PATH` value and target, and the **trust store** (an approval changes what loads without touching the overlay). The manifest is re-loaded only when that key changes. (2) The config judged is the one the gateway actually **connected** with (`ClientManager.get_connected_configs()`, which already exists), so there is no `load_configs()`. A server that is not connected is not judged by health; `update_server` still warns for it. | Measured with 21 `health()` calls, firecrawl connected, a user `server_env` URL and an **unapproved project overlay** in the cwd. **main: 0.0 ms mean, 0 WARNING lines. rev 1: 223.6 ms mean (220.7 ms steady), 42 WARNING lines (2 per call). rev 2: 9.5 ms mean, first call 174.1 ms (one load plus resolver spawn), then 1.3 ms steady; 1 WARNING line in total.** Test `test_health_loads_the_manifest_once_until_a_source_changes` expects 3 calls to give 1 load, and a user-overlay write to give a 2nd load. It is **red on rev 1** (`assert 3 == 1`) and green on rev 2. Mutants M21 (no cache) and M22 (fingerprint misses the user overlay), 1 red each. |
| claude N2 | Build metadata was accepted (`3.25.5+evil`). | **Refused.** npm ignores build metadata when resolving, so the argv would run 3.25.5 while every report echoed a label that names nothing. The rule is `is_valid_package_version(raw) and "+" not in raw`, because SemVer's build segment is the `+...` suffix. | The parametrized refusal set gains `"3.25.5+evil"`: **red on rev 1** (`assert '3.25.5+evil' is None`), green on rev 2. Mutant M23 (1 red). |
| gemini note 1 | PyPI `pkg==1.2.3`: the pinned report's latest lookup uses the name `pkg==1.2.3` and so comes back unknown. | **Tabled, explicitly.** It is pre-existing: `detect_package_type` keeps `==X` in a uvx "name" on purpose, so the existing pinned-refusal fires. This plan only adds the report, which honestly says "latest version could not be determined". Fixing it means a PEP 440 lookup name for uvx pins, and it lands with any future uvx `version:` support (D5), not here. `_is_exact_pin` already treats `==1.2.3` as exact, so no false FLOATING and no false warning. | D5 (unchanged text), plus this row. |
| gemini note 2 | The descriptions cache labels a pinned server's tools with the registry's latest. | **Tabled, explicitly**, as Non-goal plus R2. It is pre-existing for `.mcp.json` pins, causes regeneration churn only, and nothing reads the label as the running version since #150. | Non-goals and R2 (unchanged). |

**Other notes from the claude seat.** (a) A lazily registered server that has never
connected is not judged by health. That is now explicit, and `update_server` still warns
for it. (b) After adding a pin, run `gateway.refresh`, because a child spawned before the
pin keeps its version until it is respawned. The README subsection states this (see
Documentation impact).

**Scope growth.** Still within threshold. There is no new source file: `handlers.py`
gains `_is_exact_pin`, `_relaxable_manifest_servers` and the cache attribute; `loader.py`
gains `_materialize_version_pin_soft` and `manifest_sources_fingerprint`; `types.py`
gains `floating_selector`; `cli.py` gains the `[FLOATING]` branch.

## Task

Consiliency/pmcp#294. Built-in manifest entries launch npm MCP clients unversioned
(`firecrawl`: `npx -y firecrawl-mcp`), and `pmcp update` moves them to the latest
release. That is fine for a vendor-hosted backend. With a **self-hosted** backend
(`api_key_optional_when: ["FIRECRAWL_API_URL"]`, Consiliency/pmcp#114), the client can
move ahead of the server without anyone noticing. On 2026-09-25, `firecrawl-mcp` 3.25.5
started sending fields that the self-hosted API rejected with a 400.

The maintainer's 2026-09-26 measurements (issue comment) found two things:

- The 400 **no longer reproduces**: the server was fixed in Consiliency/firecrawl#9. The
  unpinned-client hazard is still real (`~/.pmcp.json` still says
  `firecrawl-mcp@latest`, and ViperJuice/dotfiles#325 is still open).
- The self-hosted search returns an empty `success:true` in **1 of 6** identical
  requests. That is a server-side problem, and it constrains follow-up slice 3 (below).

Scope of this plan:

- **Proposal 1.** A `version:` field on a manifest entry, which a user overlay can set on
  a built-in entry without restating its install matrix. `pmcp update` then reports
  "pinned at X, newer available: Y".
- **Proposal 2.** A warning in `pmcp update` and `gateway.health` when an entry's
  `api_key_optional_when` relaxer is active and its client is unpinned.

## Research summary (current tree, `9ca081e`)

**How an npm spec reaches the spawned command.** A manifest `ServerConfig`
(`src/pmcp/manifest/loader.py:43-90`) carries `command`, `args` and a per-platform
`install` argv. Two paths spawn from it:

- `config/loader.py` `_manifest_server_to_config` (`:1364`) builds a `LocalMcpServerConfig`
  from `server.command` + `server.args`, and `client/manager.py` spawns that on every
  connect, restart and lazy reconnect.
- `manifest/installer.py` (`:182`, `:686`) runs `server_config.install[platform]` for
  provisioning.

`provision_gate._config_runs_exactly` (`provision_gate.py:166-184`) already codifies
"both spawn": an argv pin counts only if `args` AND every `install` argv run exactly
`name@version` under npx. `_package_slot` (`:136`) defines the slot: the first argument
that is not in `_NPX_LEADING_FLAGS = {-y, --yes, -q, --quiet}`.

**How `.pmcp.json`/`.mcp.json` interacts.** `_merge_manifest_defaults`
(`config/loader.py:1022`) copies the manifest's `command` and prepends its `args`
**only when the configured entry has no `command`**. An entry with explicit
`command`/`args` (the ViperJuice/dotfiles#325 shape,
`"args": ["-y", "firecrawl-mcp@3.25.5"]`) is taken verbatim. Only the manifest's
`extra_env` is merged in, for keys the entry does not set.

**How update resolution reads a pin.** `gateway.update_server`
(`tools/handlers.py:5292`) resolves the *effective* config through
`_resolve_lifecycle_target`, where configured wins over manifest. It classifies the
config with `detect_package_type` and then calls `_detect_effective_version_pin`
(`handlers.py:347-450`). When that returns a pin, the tool **already refuses**, with
`"'X' is pinned to 'V' in <source> (...). gateway.update_server will not move a pinned
server..."`. `tests/test_pkgid_panel_fixes.py:900` asserts that substring. The npm branch
of the pin check reads the package token through `_npm_package_arg`, npm's own parser
(#195), and treats `@latest` as unpinned. uvx `==`, cargo `--version` and docker
`:tag`/`@sha256:` are detected too. What is missing is *what is available*:
`get_package_version` (`version_checker.py:1736`) strips the tag in
`detect_package_type`, so for a pinned argv it returns the registry's `dist-tags.latest`.
`compare_versions` (`:1906`) is the three-way classifier (#164). So the report needs no
new resolution code.

**`pmcp update` is a thin client.** `cli.py:943 run_update` calls `gateway.update_server`
per target and prints `[OK]`/`[FAILED] server: message` (`:1005-1010`). `--json` dumps the
results. It **never exits nonzero** on a per-server failure. A pinned server therefore
prints today as `[FAILED]` with a long refusal message.

**Relaxer judgement.** `credential_requirement(server, child_env=...)`
(`loader.py:164-231`) returns `relaxed_by`, the relaxer variable name, when the entry
declares `api_key_optional_when` and the *child's* environment carries a usable value for
it. It never reads `os.environ` (#124). `config/loader.py:1437 _local_env` and `:1453
_eager_requires_credential` show the required call shape: the resolved config's `env` is
passed as `child_env`.

**npm resolver cost.** `NpmResolver` (`npm_resolver.py:234`) is a persistent node child:
43 ms to start, then ~0.5 ms per query, with deliberately no memoisation (`:449-462`).
Measured this session: with the three `npm_config_*` variables unset, the real resolver
(`active (npm 11.19.0)`) resolves `firecrawl-mcp@3.25.5` to the token
`firecrawl-mcp@3.25.5` → `npm`/`firecrawl-mcp`, pin `3.25.5`, and `@playwright/mcp@1.2.3`
→ pin `1.2.3`. `firecrawl-mcp@latest` → pin `None`. A version-qualified spec is ordinary
IDENTITY input and weakens nothing. With `npm_config_cache` set, the resolver is DISABLED
and every npm server reads as `unknown`. That is by design, and it is why every test
command below unsets those variables.

**Live registry check (read only).** `get_package_version("npx", ["-y",
"firecrawl-mcp@3.25.4"], None, None)` → `3.25.5`, and `compare_versions("3.25.4",
"3.25.5", "npm")` → `newer`. That is the exact report proposal 1 asks for.

**Overlay precedent.** `server_env:` (Consiliency/pmcp#108/#109) is a top-level overlay
map that patches `extra_env` on an **existing** server, so an operator does not have to
restate its command, args or install block. It cannot create a server (unknown name →
warning, skipped). It is applied per source after that source's whole-entry replaces
(`loader.py:894-907`). A project overlay reaches the parser only through
`read_and_gate(..., "project_manifest")` (`:862`), so an unapproved project file
contributes nothing at all (tests in `tests/test_project_source_consent_manifest.py:125`).

**Which shipped entries a pin can apply to (measured).** The spike's materialiser was run
over all 107 shipped entries with `version="1.0.0"`. **77 pinnable, 30 refused**: 19 uvx,
9 remote with an empty command, `cloudflare` (npx argv but a `url`, so remote), and
`context7`. The `context7` refusal is correct: its windows install is
`["cmd", "/c", "npx", ...]`, which `_config_runs_exactly` would not accept as pinned
either. `firecrawl` and every other `api_key_optional_when` user are pinnable.

**No output-schema snapshots.** Nothing under `tests/` compares a `gateway.health` or
`update_server` payload by dict equality (`grep -rln "ServerHealthInfo\|UpdateServerOutput"
tests/` → only `tests/test_auth.py`, field reads). New optional output fields break no
existing test. The full-suite run below confirms it.

## Design decisions

### D1. Version grammar: one exact SemVer version, nothing else

A pin is accepted only if `pmcp.validation.is_valid_package_version(raw)` accepts it. That
is SemVer 2.0.0 with ASCII classes and no leading zeros, prerelease and build allowed,
≤256 characters, and the same predicate the provision gate already requires of an argv
pin. The following are refused, fail-soft with a WARNING, and the entry stays unpinned:

| refused | why |
|---|---|
| ranges: `^3.25.5`, `~3.25.5`, `3.x`, `*`, `>=3.25.0` | `npx -y pkg@^3` re-resolves the newest match at every spawn, which is the drift this issue is about. A range "pin" is a pin in name only, and `package_identity._resolve_version` refuses ranges for the same reason. |
| dist-tags: `latest`, `next` | The registry moves them. `@latest` is exactly today's unpinned behaviour, and `_detect_effective_version_pin` already reads it as unpinned. |
| `v3.25.5`, `3.25`, YAML float `3.25`, `true`, `""` | Not one exact SemVer. |
| build metadata: `3.25.5+evil` (rev 2, board N2) | npm ignores `+...` when resolving, so the argv would run `3.25.5` while every report echoed a label that names nothing. |
| anything with a name, space or flag: `evil-pkg@1.0.0`, `npm:evil-pkg@1.0.0`, `3.25.5 --registry=http://evil.test`, `../../tmp/x` | The value is a version and only a version. See D3. |

Validation is **syntactic and offline**. `load_manifest` never touches the network.
Whether `3.25.5` exists is proven later: by npx at spawn (a nonexistent version fails
the spawn loudly, which is the right failure), and by the registry read in
`update_server`.

### D2. Overlay semantics: `version:` on any entry, `server_version:` to patch one

- **`version:`** is an ordinary key on any `servers:` entry, whether shipped or a
  whole-entry overlay replace. It is parsed by `_parse_version_pin(name, raw, "version")`.
- **`server_version:`** is a new top-level overlay map, `{server-name: "X.Y.Z"}`. It is
  the version counterpart of `server_env:`, with the same rules:
  - It patches `version` on a server the manifest **already defines**. An unknown name
    gets a WARNING and is skipped, so the map can never create a server.
  - It is applied per source, **after** that source's whole-entry replaces, so a patch
    can refine an entry the same file just replaced.
  - Precedence is that of the sources: user < project < `$PMCP_MANIFEST_PATH`. A later
    source's whole-entry replace **drops** an earlier source's pin, because whole-entry
    replace means whole entry. This is the same as `server_env` today.
  - It travels in the project overlay's gated bytes. An **unapproved** project overlay's
    `server_version` contributes nothing (test
    `test_unapproved_project_server_version_contributes_nothing`, mutant M5), and an
    approved one applies (`test_approved_project_server_version_applies`).

  This is how an operator pins the built-in firecrawl:

  ```yaml
  # ~/.pmcp/manifest.yaml
  server_env:
    firecrawl:
      FIRECRAWL_API_URL: "http://ai:3002"
  server_version:
    firecrawl: "3.25.5"
  ```

- **Why a new map instead of a partial `servers: firecrawl: {version: ...}`.**
  `servers:` is whole-entry replace. README (`:1096-1099`) already warns that a partial
  `firecrawl:` entry erases the install block and resets `requires_api_key`. Making
  `servers:` merge would change what every existing overlay means. That is a separate,
  larger decision, and it is listed as an open question.

### D3. Materialisation: one post-overlay pass that rewrites the npx package slot everywhere it spawns

`load_manifest` ends with a single pass,
`servers = {name: _materialize_version_pin(entry) ...}`. It runs **after all overlays**
and also for an explicit `manifest_path`. For an entry with `version` set:

1. A remote entry (`url`) is refused, because there is no local client to pin.
2. The command must be npx under `provision_gate._is_npx`, the strict spelling set
   `npx`/`npx.cmd`/`npx.exe` that the pin check itself uses. Otherwise the pin is refused
   with a message that names the escape hatch (D5).
3. `_pin_npx_args(args, version)` finds the slot with the gate's rule (the first argument
   not in `_NPX_LEADING_FLAGS`) and parses it with `validation.parse_package_spec`. It
   keeps the **name** and replaces only the version suffix, so `firecrawl-mcp` becomes
   `firecrawl-mcp@3.25.5` and `@playwright/mcp@latest` becomes `@playwright/mcp@1.2.3`.
   If the slot is missing or not a package spec (`-p x`, `github:x/y`), the pin is
   refused. **Revision 2 (codex P1):** the slot must also be a **plain registry spec**,
   `name` or `name@<exact-version | dist-tag>`, decided by grammar
   (`is_valid_package_version` or `package_identity._DIST_TAG_RE`). An alias
   (`myalias@npm:other@1`), URL, git/`github:`, `file:`/tarball, or range selector is
   refused, because replacing it with `@<version>` would change **which** package runs.
   `myalias@npm:firecrawl-mcp@3.25.5` → `myalias@3.25.5` is the registry package
   `myalias` (measured with the real resolver).
4. The same rewrite is applied to **every** `install[platform]` argv. Each must be npx
   and must name the **same package** as `args`. Otherwise the whole pin is refused, all
   or nothing, because pinning `args` and not `install` "approves X and runs latest".
5. A refusal logs a WARNING, returns the entry unchanged with `version=None`, and so
   leaves the entry **honestly unpinned**. Proposal 2's warning then still fires for it.
   "`version` is set" always means "every spawning argv is pinned". The firecrawl test
   pins that invariant through the gate's own predicate:
   `_config_runs_exactly(pinned, "firecrawl-mcp@3.25.5")`.
   **Revision 3 (board round 2, B1/N4): the allowlist, derived class by class from
   npm-package-arg.** The source is npa as shipped with the host's npm
   (`node_modules/npm-package-arg/lib/npa.js`). npa classifies a spec in a fixed order,
   and only the last branch, `fromRegistry`, fetches `name` from the registry.
   `split_plain_registry_spec(arg)` accepts a slot only if it reaches that branch as a
   `version` or `tag` (or as a bare name, which npa types as the range `*`):

   | npa class (branch, in npa's order) | npa trigger | how `split_plain_registry_spec` treats it |
   |---|---|---|
   | url / git url (`isURL`, whole arg) | `^(?:git[+])?[a-z]+:` | refused: the name half contains `:`, so `parse_package_spec` rejects it |
   | git ssh (`isGit`, whole arg) | `^[^@]+@[^:.]+\.[^:]+:.+$` | refused: the selector contains `:` and is not a tag word |
   | file / directory, unscoped name part (whole arg) | name part has `/` **or `isFileType`** | refused: `/` fails `is_valid_package_name`; **`isFileType` on an unscoped name is refused explicitly (B1)**. A scoped name is exempt, as in npa. |
   | file (`isFileSpec`, selector) | `file:` prefix, or starts with `.`, `~/`, `/` or `X:` | refused: not a version and not a tag word (`:`, `/`, `.`-led, `~`) |
   | alias (`isAliasSpec`, selector) | `npm:` prefix | refused: `:` |
   | hosted git (`HostedGit.fromUrl`, selector) | `github:`, `gitlab:`, `user/repo`, ... | refused: `:` or `/` |
   | remote (`isURL`, selector) | `https:` and similar | refused: `:` |
   | file (`hasSlashes \|\| isFileType`, selector) | any `/`, **or `.tgz`/`.tar`/`.tar.gz`, any case** | refused: `/` is not a tag-word char; **`isFileType` on the selector is refused explicitly (B1)** |
   | registry `version` (`semver.valid`, loose) | e.g. `3.25.5` | **accepted** when `is_valid_package_version` (strict SemVer). Loose forms such as `v3.25.5` and `=3.25.5` are refused, which is conservative. |
   | registry `range` (`semver.validRange`, loose) | `^`, `~`, `>=`, `*`, `x`, `X`, `v1`, `1.x`, ... | **refused**. Symbol forms fail the tag word. **Letter-led forms (`x`, `X`, `v1.2.x`) are refused by the partial-version-word clause (N4).** A bare name (npa range `*`) is accepted: that is the entry running `name` at latest. |
   | registry `tag` (`encodeURIComponent(spec) === spec`) | anything URI-safe that is not a version or range | **accepted** when it fullmatches the letter-led `[A-Za-z][A-Za-z0-9._-]*` (a subset of npa's tags), and is neither a tarball name nor a partial-version word |
   | invalid (`EINVALIDTAGNAME` / `EINVALIDPACKAGENAME`) | e.g. `latest\n`, `tag!` | refused: `fullmatch` (N4), and `!` is outside the tag word |

   **Measured against the real npa** (`grammar_conformance.py`, Verification step 10,
   461 crafted slots across 9 names × 50 selectors plus bare forms): **0 accepted slots
   that npa does not fetch as the same name from the registry** (72 accepted: 18 `version`,
   48 `tag`, 6 bare-name `range *`). The revision-2 rule, on the same corpus, had **136
   violations**. The tests use one case per class (`_NON_PLAIN_SLOTS`, 25 ids) plus 8
   accepted-class controls, because round 2 showed that example lists which only
   use `:`-prefixed forms can't see a letter-led class.
6. **Revision 2 (codex P3): per entry.** The pass calls `_materialize_version_pin_soft`,
   so an exception while reading one entry (a non-string argv element or `command`,
   which overlays can carry because only parsed fields are shape-checked) costs that
   entry its pin, with a WARNING. It never costs the manifest its other entries.

**Why no redirect is possible.** The pin value passes D1's exact-version grammar (no `@`,
`/`, `:` or whitespace), and the package name always comes from the entry's own argv. A
`server_version` patch can therefore only pick a version of the package the entry
already runs. For honesty: a user-scope `servers:` **whole-entry replace** can still
point a built-in at any command. That is pre-existing, documented (README "Security"
note) and ungated by design, because it is the operator's own file. The invariant here is
about the version path, which adds no new redirect capability.

**Why rewriting argv beats a spawn-time override.** Every consumer already reads
`args`/`install`: `_manifest_server_to_config`, the installer,
`_detect_effective_version_pin`, `_config_runs_exactly`, `_manifest_package_names`
(policy denylist) and `_refresh_config_unchanged`. Rewriting once at load makes all of
them see the pinned spec with **zero** changes to spawn code. Two side effects are
desirable:

- A `.pmcp.json` entry with no `command` inherits the pinned args through
  `_merge_manifest_defaults` (test `test_a_config_entry_without_a_command_inherits_the_pin`).
- Changing a pin changes `args`, so `_refresh_config_unchanged` sees a new config and
  `gateway.refresh` respawns the server onto the new version.

### D4. Security invariants, checked one by one

- **#195 (npm identity refuses what it can't prove).** Nothing in the resolver changes.
  A pinned token is ordinary input to `_npm_package_arg`, measured to resolve IDENTITY
  (research). The materialiser's slot rule is the provision gate's, which is *stricter*
  than npm's parser (it rejects `-p`/`--package` before the slot), so a pin can only be
  applied where the gate would also call the argv pinned.
- **Overlay cannot redirect.** Covered in D3. Tests: the parametrized refusal cases
  `evil-pkg@1.0.0`, `npm:evil-pkg@1.0.0`, `3.25.5 --registry=...`, plus mutants M1 and M3.
- **Credential gates (#124).** Untouched. The warning *reads* `credential_requirement`
  with the resolved config's env as `child_env`, which is the same contract the seven
  gate consumers use (never `os.environ`). `extra_env`, `server_env` and the gates are
  not modified.
- **Project overlay approval.** `server_version` is parsed from the gated bytes only
  (`_parse_overlay_document(overlay_path, content)` after `read_and_gate`). There is no
  new file read. Test and mutant M5.
- **Provision gate.** A manifest entry is `manifest_backed` (rule 2) whether pinned or
  not. `_manifest_package_names` reads the same name from a pinned argv, so a
  `packages.denylist` still binds.

### D5. Non-npm installers: rejected with a clear error, and the existing escape hatch named

| installer | behaviour of `version:`/`server_version:` |
|---|---|
| npx (`npx`, `npx.cmd`, `npx.exe`) | supported (D3) |
| `npm exec`, `cmd /c npx` wrappers | refused: "install command is not npx" / "pins npx-launched servers only" |
| uvx / pip | refused: `'version'/'server_version' pins npx-launched servers only and this one runs 'uvx'; pin a uvx/pip/cargo/docker server with explicit command and args in .mcp.json or .pmcp.json instead` |
| cargo / docker | refused, same message |
| remote (`url`) | refused: "it is a remote server, so there is no local client to pin" |

The escape hatch already works, because `_detect_effective_version_pin` honours uvx
`pkg==X`, cargo `--version X` and docker `:tag`/`@sha256:` in a configured entry's
explicit args. `update_server`'s new "pinned at X, newer available: Y" report (D6) applies
to those pins too, with one limit: for uvx, `detect_package_type` keeps `pkg==X` as the
"name", so the PyPI lookup fails, and the report reads "latest version could not be
determined". That is honest and not wrong. Proper uvx support means PEP 440 grammar plus
`--from` slot handling, and is left to a follow-up if anyone asks.

### D6. How `pmcp update` reports

**`gateway.update_server` (the pinned branch, `handlers.py:5411-5428`).** The existing
refusal stays: `ok=False`, no probe, no restart, and the substring
`is pinned to 'V' in <source>` that `tests/test_pkgid_panel_fixes.py:900` asserts. The
change adds a **registry read**. `get_package_version(command, args, env, cwd,
timeout=5.0)` strips the pin and returns `dist-tags.latest`. Then
`compare_versions(pinned, latest, package_type)` classifies it three ways, never
negated (#164). Three new output fields are set:
`pinned_version`, `latest_available`, and `latest_comparison ∈ {"newer","not_newer","incomparable"} | None`
(`None` = the lookup failed). The message gains one sentence:
`Pinned at 3.25.5, newer available: 3.26.0.` / `Pinned at 3.25.5, up to date.` /
`...; the latest (X) cannot be ordered against it.` / `...; the latest version could not be determined.`

**`pmcp update` (the CLI renderer).** The per-result print moves into a pure
`_format_update_result(item) -> list[str]`. A result with `pinned_version` renders as
`[PINNED] firecrawl: pinned at 3.25.5, newer available: 3.26.0` (or `up to date` /
`latest X cannot be compared` / `latest unknown`) instead of `[FAILED] firecrawl: <long
refusal>`. Other results render exactly as today. Every string in `warnings` prints under
its result as `  warning: <text>`. `--json` gets the new fields for free. The exit code is
unchanged (0), because `run_update` has never exited nonzero on a per-server result.

**Revision 2 (codex P2 / claude F1): a range or tag is FLOATING, not PINNED.**
`_detect_effective_version_pin` answers "does the argv carry any version selector". That
is the right question for the refusal: do not move what the operator chose, and the
refusal is unchanged. It is the wrong question for the label. `_is_exact_pin` decides
the label. An exact pin reports as above. A range or dist-tag (`^3.25.0`, `~3.25.5`,
`next`) returns `floating_selector="^3.25.0"`, `pinned_version=None` and
`latest_comparison=None`, with a message that starts `'fc' is held at '^3.25.0' in ...`
and says `'^3.25.0' is a range or tag, not one exact version: it re-resolves at every
spawn, so it does not hold the client still (latest: 3.26.0).` `pmcp update` prints
`[FLOATING] fc: held at ^3.25.0, a range or tag that re-resolves at every spawn (latest 3.26.0)`.
`[FLOATING]` is not a failure, and the exit code stays 0.

**Revision 3 (board round 2, N3): build metadata in a configured argv.** npm and cargo
ignore `+...` when resolving, so for an exact pin the label is **what runs**:
`firecrawl-mcp@3.25.5+evil` reports `pinned_version="3.25.5"`, compares `3.25.5` with
latest, and says `(build metadata '+evil' is ignored by npm)`. A manifest `version:`
with `+` is still refused at load.

### D7. The unpinned-self-hosted warning (proposal 2)

`_unpinned_self_hosted_warning(server_name, manifest_server, resolved)` in `handlers.py`
is a pure function. It fires only when **both** of these hold:

1. **The relaxer is active on the child env.** That is
   `credential_requirement(manifest_server, child_env=resolved.config.env).relaxed_by`.
   For a manifest-only server, `resolved` is `manifest_server_to_config(...)`, whose env
   carries `extra_env` (including `server_env` patches). For a configured entry it is
   the merged config, so an entry that blanks `FIRECRAWL_API_URL` is judged on the blank
   value.
2. **The spawning argv is unpinned** by the *same* `_detect_effective_version_pin` that
   `update_server` uses, so the two can never disagree. `@latest` counts as unpinned.

It judges the **effective** config (configured over manifest), so the
ViperJuice/dotfiles#325 shape (`.pmcp.json` with explicit `firecrawl-mcp@3.25.5`) does
**not** warn, and `~/.pmcp.json`'s current `firecrawl-mcp@latest` **does**. The remedy
text depends on the source. A manifest-sourced npm entry is told
`pin it with \`server_version: {firecrawl: <version>}\` in ~/.pmcp/manifest.yaml`. Anything
else is told to pin it in the args of the config that launches it.

**Revision 2 changes to the predicate:**

- **Exactness (codex P2 / claude F1).** The argv counts as pinned only if
  `_is_exact_pin(package_type, pin)`: `is_valid_package_version(pin)` in every
  ecosystem, a docker `sha256:` digest (immutable), or a PyPI `==X` with no wildcard.
  A bare spec, `@latest`, a range or another dist-tag all warn. A non-exact selector is
  named in the text: `floats on '^3.25.5' (a range or tag, not one exact version)`.
- **Inherited environment (claude F2).** The relaxer is judged on
  `sanitized_subprocess_env(resolved.config.env, project_root)`, which is exactly what
  `client/manager.py:2409` spawns the child with. So a `FIRECRAWL_API_URL` exported in the
  shell that started pmcp counts. This is advisory only: every credential **gate** keeps
  `child_env=config.env` and is not touched (for a gate, ignoring the ambient value is
  the safe direction, #124), and `tests/test_credential_predicate_guard.py` stays green.
- **Health cost (claude F3).** Health judges the config the gateway **connected** with
  (`ClientManager.get_connected_configs()`, an existing public method), so there is no
  config-file I/O. The relaxer-declaring manifest entries are cached per `GatewayTools`
  and keyed by `manifest_sources_fingerprint()` (`stat` only), so the manifest is
  re-loaded only when a source or the trust store changes. A server that is not
  connected is not judged by health; `update_server` still judges it, on a fresh
  resolution.

**Revision 3 change (board round 2, N2): npm identity disabled.** When
`detect_package_type` refuses an **npx** launch (identity disabled or refused, #195),
the warning does not go silent. It reads the slot with `provision_gate._package_slot`
and `split_plain_registry_spec`, the grammar the materialiser uses. An exact pin is
silent. `@latest`, a bare spec, a range or a tag warns, with `(read from the argv: npm
package identity is unavailable)`. A slot the grammar can't name at all warns
`pmcp cannot verify that its client is pinned ... (see gateway_diagnostics.npm_identity)`.
Any other command with unknown identity stays silent, as before.

The warning appears in two places:

- **`gateway.health`.** `ServerHealthInfo.warnings: list[str]` (default `[]`) is filled by
  `_attach_version_pin_warnings(servers)` just before diagnostics. The cost, **as of
  revision 2 and measured** (21 calls, an unapproved project overlay present): one
  `load_manifest()` per change of `manifest_sources_fingerprint()`, not per call, and
  then per connected relaxer-declaring server one `sanitized_subprocess_env` plus one
  resolver query. That is 174 ms on the first call and 1.3 ms steady, with 1 WARNING
  line in total. Revision 1 measured 220.7 ms steady and 42 WARNING lines. It is
  wrapped in `try/except`, logged at DEBUG, and never costs health its answer.
- **`gateway.update_server`.** The public method becomes a thin wrapper around the
  unchanged body (renamed `_update_server_unwarned`). It appends the warning computed on
  the configuration **after** the attempt. The warning never changes `ok` and never
  blocks the update. An unpinned self-hosted client is still updated, and still warned
  about, because it is still unpinned. `pmcp update` prints it under the result (D6).

## Changes

### `src/pmcp/manifest/loader.py` (modify)

| entity | action | reason |
|---|---|---|
| imports | add `from pmcp.validation import is_valid_package_version, parse_package_spec` | D1 grammar, D3 slot parse (validation imports nothing from pmcp, so there is no cycle; measured by mypy + the suite) |
| `ServerConfig.version: str \| None = None` | add, with a 5-line comment | the pin, `None` = unpinned, including a refused pin |
| `_parse_version_pin(name, raw, field_label)` | add | D1, fail-soft WARNING that names the field (`version` / `server_version`) |
| `_pin_npx_args(args, version)` | add | D3 step 3. Local import of `provision_gate._NPX_LEADING_FLAGS` (provision_gate imports `ServerConfig` under `TYPE_CHECKING` only, but a local import keeps the module graph as it is) |
| `_materialize_version_pin(server)` | add | D3 steps 1-5, all or nothing |
| `split_plain_registry_spec(arg)` + `_NPM_FILE_TYPE_RE`, `_TAG_WORD_RE`, `_PARTIAL_VERSION_WORD_RE` (rev 3; public) | add; `_pin_npx_args` calls it for the slot (it replaces the rev-2 `_DIST_TAG_RE` check) | D3 class table: codex P1, board round 2 B1/N4 |
| `_materialize_version_pin_soft(server)` (rev 2) | add; the load pass calls it | D3 step 6, codex P3 |
| `manifest_sources_fingerprint()` (rev 2) | add, public, `stat` only | D7 health cache, claude F3 |
| `_parse_version_pin` (rev 2) | also refuse `+` build metadata | D1, claude N2 |
| `_parse_server_config` | add `version=_parse_version_pin(name, data.get("version"), "version")` | D2 `version:` key |
| `_OverlayDocument` | widen to a 4-tuple `(servers, clis, server_env, server_version)` | D2. The only callers are `_load_overlay_file` and `load_manifest` (grep: no test imports it) |
| `_load_overlay_file`, `_parse_overlay_document` | return 4-tuples; parse `server_version:` (a mapping of non-empty str → `_parse_version_pin`; a non-mapping gets a WARNING); docstring paragraph | D2 |
| `load_manifest` | unpack 4-tuples in both branches; include the server_version count in the "Applying manifest overlay" INFO line; apply `server_version` per source after `server_env` (unknown name → WARNING, skip); **after the overlay loop, outside `if apply_overlays`**, the materialisation pass | D2/D3 |

### `src/pmcp/types.py` (modify)

| entity | action | reason |
|---|---|---|
| `ServerHealthInfo.warnings: list[str] = Field(default_factory=list)` | add | D7. The same `default_factory` shape as `missing_env_vars` |
| `UpdateServerOutput.pinned_version`, `.latest_available: str \| None = None`, `.latest_comparison: Literal["newer","not_newer","incomparable"] \| None = None`, `.warnings: list[str]` | add | D6/D7 |
| `UpdateServerOutput.floating_selector: str \| None = None` (rev 2) | add | D6 FLOATING, codex P2 |

### `src/pmcp/tools/handlers.py` (modify)

| entity | action | reason |
|---|---|---|
| imports | add `compare_versions` (version_checker) and `credential_requirement` (manifest.loader) | D6/D7 |
| `_unpinned_self_hosted_warning(server_name, manifest_server, resolved, project_root)` (module level, after `_detect_effective_version_pin`) | add | D7 (rev 2: judges the relaxer on `sanitized_subprocess_env`, and suppresses only on `_is_exact_pin`) |
| `_is_exact_pin(package_type, pin)` (rev 2) | add | D6/D7, codex P2 |
| identity-disabled fallback in `_unpinned_self_hosted_warning` (rev 3) | add: npx only, via `provision_gate._is_npx`/`_package_slot` + `split_plain_registry_spec` (imports added) | D7, board round 2 N2 |
| pinned branch: `build_note` (rev 3) | add: strip `+...` from an exact npm/cargo pin before comparing and labelling | D6, board round 2 N3 |
| imports (rev 2) | add `_parse_version` (version_checker) and `manifest_sources_fingerprint` (manifest.loader) | `_is_exact_pin`, the health cache |
| `GatewayTools._relaxable_cache`, `_relaxable_manifest_servers()` (rev 2) | add | D7 health cache, claude F3 |
| `health` | call `self._attach_version_pin_warnings(servers)` before the diagnostics block | D7 |
| `_version_pin_warning(server_name)`, `_attach_version_pin_warnings(servers)` (methods, before `_config_source_paths_by_server`) | add | D7. Rev 2: health judges `get_connected_configs()`, not `load_configs()` |
| `update_server` | becomes a wrapper that **keeps the full contract docstring** (plus one #294 paragraph); the body moves to `_update_server_unwarned` with a one-line pointer docstring and is otherwise unchanged except for the pinned branch | D7. `tests/test_tools.py::test_update_server_docstring_states_both_probe_window_env_contracts` reads `GatewayTools.update_server.__doc__` (measured: moving the docstring turns it red) |
| pinned branch of the body (`if pinned_to is not None:`) | add the registry read + `compare_versions`, set the three fields, and add the availability sentence to the message. Rev 2: `exact = _is_exact_pin(...)`; a non-exact selector sets `floating_selector`, and the message says `is held at` | D6 |

### `src/pmcp/cli.py` (modify)

| entity | action | reason |
|---|---|---|
| `run_update` print loop | `for line in _format_update_result(item): print(line)` | D6 |
| `_format_update_result(item)` (after `run_update`) | add | D6. Pure, so it is unit-tested without a gateway. Rev 2: the `[FLOATING]` branch |

### `tests/test_pkgid_panel_fixes.py` (modify): keep the pinned-refusal test offline

| entity | action | reason |
|---|---|---|
| `test_update_server_still_refuses_a_pinned_manifest_server_as_before` | add a `monkeypatch.setattr(handlers_module, "get_package_version", no_registry)` stub returning `(None, "npm")` before the call | The pinned branch now makes a **registry read** (D6). Without the stub, this pre-existing test makes a real HTTP request to registry.npmjs.org for `@shipped/server`. That can take up to the 5 s timeout, and it is swallowed to `None`, so it would never go red: a silent hermeticity regression (the Consiliency/pmcp#235 class). The refusal path was offline before this diff and must stay offline. |

**Measured** with a no-network plugin (Verification step 5b) over every test file that
drives `update_server` or `health` (19 files):

- HEAD: 0 registry lookups.
- The spike without this stub: exactly **1** offender, this test (`1 failed, 857 passed`).
- The spike with the stub: 0. Revision 2 re-measured this over the same 19 files: `878 passed, 1 deselected`; revision 3: `910 passed, 1 deselected`.

The pinned-refusal tests in `tests/test_tools.py` (`test_update_server_refuses_pinned_configured_override`,
`..._pinned_docker_tag`) already stub `get_package_version` and need no change. The stub
is included in the production diff above.

### `tests/test_version_pin.py` (create)

89 tests (rev 3; 57 in rev 2, 37 in rev 1). The parametrized sets are: 17 version refusals, **25 npa-class non-plain slots**, 8 accepted-class controls, 3 malformed shapes and 4 floating specs. It also has 2 fingerprint tests, 3 identity-disabled tests and 1 build-metadata label test. Body verbatim below.

### Production diff (spike, verbatim; apply as-is)

The diff is against `9ca081e`, already `ruff format`-clean.

```diff
diff --git a/src/pmcp/cli.py b/src/pmcp/cli.py
index 74ced0d..8760f54 100644
--- a/src/pmcp/cli.py
+++ b/src/pmcp/cli.py
@@ -1005,15 +1005,51 @@ async def run_update(args: argparse.Namespace) -> None:
             return
 
         for item in results:
-            ok = bool(item.get("ok"))
-            status = "OK" if ok else "FAILED"
-            server = item.get("server", "unknown")
-            message = item.get("message", "")
-            print(f"[{status}] {server}: {message}")
+            for line in _format_update_result(item):
+                print(line)
     finally:
         await client_manager.disconnect_all()
 
 
+def _format_update_result(item: dict[str, object]) -> list[str]:
+    """Render one gateway.update_server result for `pmcp update`.
+
+    A pinned server was deliberately not moved, so it is ``[PINNED]`` with
+    its availability line, not ``[FAILED]`` (Consiliency/pmcp#294). A server
+    held at a range or dist-tag was not moved either, but it is not pinned --
+    it re-resolves at every spawn -- so it is ``[FLOATING]`` (#295 board).
+    Warnings are advisory and printed under the result whatever its status.
+    """
+    server = item.get("server", "unknown")
+    pinned = item.get("pinned_version")
+    floating = item.get("floating_selector")
+    if floating:
+        latest = item.get("latest_available") or "unknown"
+        lines = [
+            f"[FLOATING] {server}: held at {floating}, a range or tag that "
+            f"re-resolves at every spawn (latest {latest})"
+        ]
+    elif pinned:
+        latest = item.get("latest_available")
+        comparison = item.get("latest_comparison")
+        if comparison == "newer":
+            detail = f"pinned at {pinned}, newer available: {latest}"
+        elif comparison == "not_newer":
+            detail = f"pinned at {pinned}, up to date"
+        elif comparison == "incomparable":
+            detail = f"pinned at {pinned}, latest {latest} cannot be compared"
+        else:
+            detail = f"pinned at {pinned}, latest unknown"
+        lines = [f"[PINNED] {server}: {detail}"]
+    else:
+        status = "OK" if bool(item.get("ok")) else "FAILED"
+        lines = [f"[{status}] {server}: {item.get('message', '')}"]
+    warnings = item.get("warnings")
+    if isinstance(warnings, list):
+        lines.extend(f"  warning: {warning}" for warning in warnings)
+    return lines
+
+
 def _get_gateway_url() -> str:
     """Return the PMCP gateway MCP endpoint URL."""
     return os.environ.get(
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index 9837e82..695c712 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -14,6 +14,7 @@ from typing import Any, Literal, cast
 import yaml
 
 from pmcp.project_consent import log_refusal, read_and_gate
+from pmcp.validation import is_valid_package_version, parse_package_spec
 
 logger = logging.getLogger(__name__)
 
@@ -88,6 +89,12 @@ class ServerConfig:
     status: str | None = None
     source: str | None = None
     replacement: str | None = None
+    # The exact client version this entry runs (Consiliency/pmcp#294). Set by
+    # `version:` on an entry or by an overlay's `server_version:` patch, and
+    # materialised by `load_manifest` into the npx package slot of `args` and
+    # of every `install` argv. ``None`` means unpinned -- including a pin that
+    # was refused, so "version is set" always means "the argv is pinned".
+    version: str | None = None
 
 
 def credential_storage_key(server: Any) -> str | None:
@@ -553,6 +560,229 @@ def _parse_api_key_optional_when(
     return parsed
 
 
+def _parse_version_pin(name: str, raw: Any, field_label: str) -> str | None:
+    """Parse a client version pin, fail-soft and fail-closed.
+
+    One exact SemVer version (``is_valid_package_version``, the grammar the
+    provision gate already requires of an argv pin) or nothing. Refused, with
+    a warning: a range (``^3.25.5``, ``3.x``) and a dist-tag (``latest``) --
+    both re-resolve at every ``npx -y`` spawn, which is the drift a pin exists
+    to stop -- a ``v`` prefix, a YAML number, and anything carrying a package
+    name, whitespace or a flag. The value never names a package: the name is
+    always taken from the entry's own argv (see `_materialize_version_pin`).
+    """
+    if raw is None:
+        return None
+    # SemVer build metadata (the `+...` segment) is refused too: npm ignores it
+    # when resolving, so `3.25.5+x` would run 3.25.5 while every report echoed
+    # a label that names nothing (Consiliency/pmcp#295 board, N2).
+    if isinstance(raw, str) and is_valid_package_version(raw) and "+" not in raw:
+        return raw
+    logger.warning(
+        f"Ignoring '{field_label}' {raw!r} for server '{name}': a version pin "
+        'must be one exact version such as "3.25.5" -- not a range, a '
+        'dist-tag such as "latest", build metadata (+...), or a package '
+        "spec; the server stays unpinned"
+    )
+    return None
+
+
+# npm-package-arg's classification, restated as the ALLOWLIST a pin may
+# rewrite (Consiliency/pmcp#295 board rounds 1-2). npa decides a spec's class
+# in a fixed order, and only its final branch, `fromRegistry`, fetches `name`
+# from the registry; every earlier branch (URL, git, alias, file, directory,
+# hosted git) names something else. The plan's class table maps each branch to
+# the clause below that refuses it.
+#
+# npa `isFileType`, verbatim: a selector -- or an UNSCOPED bare name -- ending
+# in .tgz/.tar/.tar.gz (any case) is a local tarball FILE, checked before the
+# registry branch. Letter-led, so a tag-shaped regex alone admits it.
+_NPM_FILE_TYPE_RE = re.compile(r"[.](?:tgz|tar\.gz|tar)$", re.IGNORECASE)
+# A dist-tag: npa's registry branch accepts any encodeURIComponent-safe word
+# that is neither a version nor a range; this is the letter-led subset of it
+# (fullmatch, so no trailing newline).
+_TAG_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
+# A letter-led word npm reads as a VERSION or RANGE, not a tag: an optional
+# v/V, then a partial version whose parts are numbers or x/X/* wildcards
+# (`x`, `X.x`, `v1`, `v1.2.x`, `x-beta`). Refused like every range: rewriting
+# it would be harmless to package identity (still `fromRegistry`), but a range
+# is not the version-or-tag class a pin replaces. Deliberately broad: a real
+# tag this also matches only loses its pin, with a warning.
+_PARTIAL_VERSION_WORD_RE = re.compile(
+    r"[vV]?(?:[0-9]+|[xX*])(?:\.(?:[0-9]+|[xX*])){0,2}(?:[-+].*)?"
+)
+
+
+def split_plain_registry_spec(arg: str) -> tuple[str, str | None] | None:
+    """``(name, selector)`` if npm would fetch *arg* from the registry as
+    ``name`` at an exact version or a dist-tag; else ``None``.
+
+    Accepted: ``name`` (npa: registry range ``*``), ``name@<exact SemVer>``
+    (npa: ``version``) and ``name@<dist-tag>`` (npa: ``tag``). Refused, by
+    class: anything ``parse_package_spec`` rejects (URLs, aliases, git, paths,
+    flags: their name half is not a package name, or they have no name); an
+    unscoped name ending in a tarball suffix (npa: ``file``); a selector that
+    is not an exact version or a tag word (``:`` / ``/`` / ``~`` / ``.``-led
+    forms, i.e. alias, git, URL, file, directory, and every range); a tag word
+    ending in a tarball suffix (npa: ``file``); and a tag word that npm reads as
+    a version or range (``x``, ``v1``). Pure grammar: it never asks the
+    resolver, so it works while npm package identity is disabled.
+    """
+    try:
+        name, selector = parse_package_spec(arg)
+    except (ValueError, TypeError, AttributeError):
+        return None
+    if not name.startswith("@") and _NPM_FILE_TYPE_RE.search(name):
+        return None
+    if selector is None or is_valid_package_version(selector):
+        return name, selector
+    if (
+        _TAG_WORD_RE.fullmatch(selector)
+        and not _NPM_FILE_TYPE_RE.search(selector)
+        and not _PARTIAL_VERSION_WORD_RE.fullmatch(selector)
+    ):
+        return name, selector
+    return None
+
+
+def _pin_npx_args(args: list[str], version: str) -> tuple[list[str], str] | None:
+    """*args* with the npx package slot pinned to *version*, and the name.
+
+    The slot is the provision gate's (`provision_gate._package_slot`): the
+    first argument that is not an allowlisted leading flag. It must be a plain
+    registry spec (`split_plain_registry_spec`); its NAME is kept and only its
+    version suffix is replaced, so a pin can select a version of the package
+    the entry already runs and never a different one. ``None`` otherwise:
+    ``myalias@npm:firecrawl-mcp@3.25.5`` -> ``myalias@3.25.5`` would be the
+    registry package ``myalias`` (codex P1), and
+    ``firecrawl-mcp@corp-mcp.TGZ`` -> ``firecrawl-mcp@3.25.5`` would turn a
+    local tarball into a public-registry fetch (round-2 B1).
+    """
+    # Local import: provision_gate is a consumer of this module's ServerConfig.
+    from pmcp.provision_gate import _NPX_LEADING_FLAGS
+
+    for index, arg in enumerate(args):
+        if arg in _NPX_LEADING_FLAGS:
+            continue
+        plain = split_plain_registry_spec(arg)
+        if plain is None:
+            return None
+        name, _selector = plain
+        return [*args[:index], f"{name}@{version}", *args[index + 1 :]], name
+    return None
+
+
+def _materialize_version_pin(server: ServerConfig) -> ServerConfig:
+    """Write ``server.version`` into every argv that spawns the server.
+
+    Both ``args`` (what ``client/manager.py`` spawns) and every ``install``
+    argv (what ``start_install`` runs): pinning one and not the other approves
+    X and runs latest (`provision_gate._config_runs_exactly`). All or nothing:
+    if any argv cannot be pinned to the same package, the pin is dropped with
+    a warning and the entry is returned unpinned with ``version=None``, so
+    gateway.health's unpinned-self-hosted warning still sees it.
+    """
+    version = server.version
+    if version is None:
+        return server
+    from pmcp.provision_gate import _is_npx
+
+    def refuse(reason: str) -> ServerConfig:
+        logger.warning(
+            f"Ignoring version pin {version!r} for server '{server.name}': "
+            f"{reason}; the server stays unpinned"
+        )
+        return replace(server, version=None)
+
+    if server.url:
+        return refuse("it is a remote server, so there is no local client to pin")
+    if not _is_npx(server.command):
+        return refuse(
+            f"'version'/'server_version' pins npx-launched servers only and this "
+            f"one runs {server.command!r}; pin a uvx/pip/cargo/docker server with "
+            "explicit command and args in .mcp.json or .pmcp.json instead"
+        )
+    pinned = _pin_npx_args(list(server.args), version)
+    if pinned is None:
+        return refuse(
+            "its args name no plain registry package (name or name@version/tag) "
+            "to pin -- an alias, URL, git, file or range spec could change which "
+            "package runs"
+        )
+    args, package = pinned
+    install: dict[Platform, list[str]] = {}
+    for platform, argv in server.install.items():
+        if not argv:
+            install[platform] = argv
+            continue
+        if not _is_npx(argv[0]):
+            return refuse(f"its {platform} install command is not npx")
+        pinned_install = _pin_npx_args(list(argv[1:]), version)
+        if pinned_install is None or pinned_install[1] != package:
+            return refuse(
+                f"its {platform} install command does not run the plain "
+                f"registry package {package!r} its args run"
+            )
+        install[platform] = [argv[0], *pinned_install[0]]
+    return replace(server, args=args, install=install)
+
+
+def _materialize_version_pin_soft(server: ServerConfig) -> ServerConfig:
+    """`_materialize_version_pin`, contained to one entry.
+
+    Overlay entries are only shape-checked where a field is parsed, so an argv
+    can still carry a non-string (``args: ["-y", 123]``) or a non-string
+    ``command``. HEAD loads such an entry untouched; a pin on it must cost that
+    entry its pin, never the whole manifest (Consiliency/pmcp#295 board,
+    codex P3).
+    """
+    try:
+        return _materialize_version_pin(server)
+    except Exception as exc:
+        logger.warning(
+            f"Ignoring version pin {server.version!r} for server '{server.name}': "
+            f"its command/args/install could not be read ({type(exc).__name__}: "
+            f"{exc}); the server stays unpinned"
+        )
+        return replace(server, version=None)
+
+
+def manifest_sources_fingerprint() -> tuple[object, ...]:
+    """A cheap identity for everything ``load_manifest()`` reads.
+
+    ``stat`` only -- no parse and no log line -- over the shipped manifest,
+    the user overlay, the project overlay the cwd walk finds, the raw
+    ``$PMCP_MANIFEST_PATH`` value and its target, and the trust store (a
+    project overlay's approval changes what loads without touching the
+    overlay file). A caller that only needs a few manifest facts on a hot
+    path (gateway.health) re-loads when this changes instead of on every
+    call (Consiliency/pmcp#295 board, claude F3).
+    """
+
+    def stat(path: Path) -> tuple[str, int | None, int | None]:
+        try:
+            st = path.stat()
+        except OSError:
+            return (str(path), None, None)
+        return (str(path), st.st_mtime_ns, st.st_size)
+
+    parts: list[object] = [
+        stat(Path(__file__).parent / "manifest.yaml"),
+        stat(Path.home() / ".pmcp" / "manifest.yaml"),
+    ]
+    project = _find_project_manifest()
+    parts.append(stat(project) if project is not None else None)
+    env_value = os.environ.get("PMCP_MANIFEST_PATH")
+    parts.append((env_value, stat(Path(env_value).expanduser())) if env_value else None)
+    try:
+        from pmcp.trust_store import trust_store_path
+
+        parts.append(stat(trust_store_path()))
+    except Exception:
+        parts.append(None)
+    return tuple(parts)
+
+
 def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
     """Parse a server config from raw YAML data."""
     install_data = data.get("install", {})
@@ -613,6 +843,7 @@ def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
         status=data.get("status"),
         source=data.get("source"),
         replacement=data.get("replacement"),
+        version=_parse_version_pin(name, data.get("version"), "version"),
     )
 
 
@@ -722,7 +953,10 @@ def _overlay_manifest_paths() -> list[tuple[str, Path]]:
 
 
 _OverlayDocument = tuple[
-    dict[str, ServerConfig], dict[str, CLIAlternative], dict[str, dict[str, str]]
+    dict[str, ServerConfig],
+    dict[str, CLIAlternative],
+    dict[str, dict[str, str]],
+    dict[str, str],
 ]
 
 
@@ -739,7 +973,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
         content = path.read_bytes()
     except OSError as exc:
         logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     return _parse_overlay_document(path, content)
 
@@ -747,7 +981,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
 def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     """Parse overlay bytes, fail-soft. ``path`` is for messages only.
 
-    Returns ``(servers, cli_alternatives, server_env)``. A YAML error or a
+    Returns ``(servers, cli_alternatives, server_env, server_version)``. A YAML error or a
     non-mapping top-level document logs a warning naming the file and returns
     empty dicts. Each entry is parsed in its own try/except so one malformed
     entry is skipped without dropping siblings.
@@ -759,18 +993,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     operator can point a shipped server at a self-hosted endpoint without
     restating its command, args, and install block. It deliberately cannot
     create a server: ``servers:`` remains whole-entry replace.
+
+    ``server_version`` is the same kind of patch for ``version``: it pins an
+    existing server's client without restating its install matrix, and it
+    cannot create a server either (Consiliency/pmcp#294).
     """
     try:
         data = yaml.safe_load(content)
     except yaml.YAMLError as exc:
         logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     if not isinstance(data, dict):
         logger.warning(
             f"Skipping manifest overlay {path}: top-level document is not a mapping"
         )
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     servers: dict[str, ServerConfig] = {}
     raw_servers = data.get("servers", {})
@@ -814,7 +1052,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     elif raw_server_env:
         logger.warning(f"Skipping 'server_env' in overlay {path}: not a mapping")
 
-    return servers, cli_alternatives, server_env
+    server_version: dict[str, str] = {}
+    raw_server_version = data.get("server_version", {})
+    if isinstance(raw_server_version, dict):
+        for name, raw_version in raw_server_version.items():
+            if not isinstance(name, str) or not name:
+                logger.warning(
+                    f"Skipping non-string 'server_version' key in overlay {path}"
+                )
+                continue
+            version = _parse_version_pin(name, raw_version, "server_version")
+            if version is not None:
+                server_version[name] = version
+    elif raw_server_version:
+        logger.warning(f"Skipping 'server_version' in overlay {path}: not a mapping")
+
+    return servers, cli_alternatives, server_env, server_version
 
 
 def load_manifest(manifest_path: Path | None = None) -> Manifest:
@@ -868,19 +1121,31 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
                     continue
                 # Parse the bytes the gate judged. Re-opening `overlay_path`
                 # here would apply content nobody approved.
-                overlay_servers, overlay_clis, overlay_server_env = (
-                    _parse_overlay_document(overlay_path, content)
-                )
+                (
+                    overlay_servers,
+                    overlay_clis,
+                    overlay_server_env,
+                    overlay_server_version,
+                ) = _parse_overlay_document(overlay_path, content)
             else:
-                overlay_servers, overlay_clis, overlay_server_env = _load_overlay_file(
-                    overlay_path
-                )
-            if overlay_servers or overlay_clis or overlay_server_env:
+                (
+                    overlay_servers,
+                    overlay_clis,
+                    overlay_server_env,
+                    overlay_server_version,
+                ) = _load_overlay_file(overlay_path)
+            if (
+                overlay_servers
+                or overlay_clis
+                or overlay_server_env
+                or overlay_server_version
+            ):
                 logger.info(
                     f"Applying manifest overlay ({label}) from {overlay_path}: "
                     f"{len(overlay_servers)} servers, "
                     f"{len(overlay_clis)} CLI alternatives, "
-                    f"{len(overlay_server_env)} server_env patches"
+                    f"{len(overlay_server_env)} server_env patches, "
+                    f"{len(overlay_server_version)} server_version pins"
                 )
             for name in overlay_servers:
                 if name in servers:
@@ -906,6 +1171,25 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
                     existing, extra_env={**existing.extra_env, **patch}
                 )
 
+            # Same rules as server_env: after this source's replaces, and never
+            # for a server the manifest does not already define.
+            for name, version in overlay_server_version.items():
+                existing = servers.get(name)
+                if existing is None:
+                    logger.warning(
+                        f"Manifest overlay ({label}) from {overlay_path} has a "
+                        f"'server_version' pin for unknown server '{name}': skipped"
+                    )
+                    continue
+                servers[name] = replace(existing, version=version)
+
+    # Materialise every pin once, after all overlays: a later source's
+    # whole-entry replace or server_version patch must be what gets written
+    # into argv, not an earlier one's.
+    servers = {
+        name: _materialize_version_pin_soft(entry) for name, entry in servers.items()
+    }
+
     manifest = Manifest(
         version=data.get("version", "1.0"),
         cli_alternatives=cli_alternatives,
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 956c8c8..66b5c4f 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -101,13 +101,17 @@ from pmcp.manifest.version_checker import (
     _docker_image_tag,
     _npm_package_arg,
     _npm_tag,
+    _parse_version,
     _uvx_package_arg,
+    compare_versions,
     detect_package_type,
     get_package_version,
 )
 from pmcp.policy.policy import PolicyManager
 from pmcp.provision_gate import (
     ProvisionDecision,
+    _is_npx,
+    _package_slot,
     ProvisionSource,
     evaluate_provision,
     operator_safe,
@@ -194,8 +198,11 @@ from pmcp.manifest.loader import (
     Manifest,
     ServerConfig,
     credential_lookup_keys,
+    credential_requirement,
     credential_storage_key,
     is_usable_credential_value,
+    manifest_sources_fingerprint,
+    split_plain_registry_spec,
     requires_credential,
 )
 
@@ -444,6 +451,108 @@ def _detect_effective_version_pin(
     return None
 
 
+def _is_exact_pin(package_type: str, pin: str) -> bool:
+    """Does *pin* hold the client at ONE version, or only name a selector?
+
+    ``_detect_effective_version_pin`` answers "does the argv carry any
+    version selector", which is the right question for update_server's
+    refusal (do not move what the operator chose) and the wrong one for
+    drift: ``^3.25.5``, ``~3.25.5`` and ``next`` re-resolve at every spawn
+    (Consiliency/pmcp#295 board, codex P2 / claude F1). Exact means one
+    SemVer version (``is_valid_package_version``) in every ecosystem, plus a
+    docker ``sha256:`` digest (immutable) and a PEP 440 ``==X`` without a
+    wildcard for PyPI (``==1.2`` is exact there, and ``==1.*`` is not).
+    """
+    if is_valid_package_version(pin):
+        return True
+    if package_type == "docker":
+        return pin.startswith("sha256:")
+    if package_type == "pypi":
+        return "*" not in pin and _parse_version(pin) is not None
+    return False
+
+
+def _unpinned_self_hosted_warning(
+    server_name: str,
+    manifest_server: ServerConfig | None,
+    resolved: ResolvedServerConfig,
+    project_root: Path | None,
+) -> str | None:
+    """Warn when a self-hosted backend is served by an unpinned client.
+
+    Consiliency/pmcp#294, proposal 2. Fires only when BOTH hold:
+
+    * the manifest entry's ``api_key_optional_when`` relaxer is active on the
+      environment the child ACTUALLY receives: ``sanitized_subprocess_env``
+      of the resolved config's env, which is exactly what ``client/manager.py``
+      spawns with -- the gateway's own environment minus managed secrets, plus
+      the entry's env. So a relaxer exported in the shell that started pmcp
+      counts (Consiliency/pmcp#295 board, claude F2). This is an ADVISORY
+      read: the credential gates keep ``child_env=config.env`` and are not
+      touched (#124: for a gate, ignoring the ambient value is the safe
+      direction; for a warning, including it is); and
+    * the argv that actually spawns is not held at one exact version
+      (``_is_exact_pin`` over ``_detect_effective_version_pin``): a bare
+      spec, ``@latest``, a range or another dist-tag all warn.
+
+    A vendor-hosted server (relaxer inactive) never warns: following the
+    vendor's latest client is the intended default there.
+    """
+    if manifest_server is None or not isinstance(resolved.config, LocalMcpServerConfig):
+        return None
+    child_env = sanitized_subprocess_env(resolved.config.env, project_root)
+    relaxed_by = credential_requirement(manifest_server, child_env=child_env).relaxed_by
+    if relaxed_by is None:
+        return None
+    command, args = resolved.config.command, list(resolved.config.args)
+    env, cwd = resolved.config.env, resolved.config.cwd
+    package_type, package_name = detect_package_type(command, args, env, cwd)
+    note = ""
+    if package_type == "unknown" or not package_name:
+        # npm package identity is refused or disabled (#195; e.g. whenever
+        # `npm_config_cache` is set). Do not go silent (Consiliency/pmcp#295
+        # board round 2, N2): for an npx launch, read the slot STRUCTURALLY
+        # with the materialiser's own grammar, and say so; if even that cannot
+        # name a plain registry package, warn that the pin cannot be verified.
+        if not _is_npx(command):
+            return None
+        slot = _package_slot(args)
+        plain = split_plain_registry_spec(slot) if slot is not None else None
+        if plain is None:
+            return (
+                f"'{server_name}' talks to a self-hosted backend ({relaxed_by} is "
+                "set), but pmcp cannot verify that its client is pinned: npm "
+                "package identity is unavailable (see gateway_diagnostics."
+                "npm_identity) and its npx package slot is not a plain registry "
+                "spec. Pin an exact version in the args of the config that "
+                "launches it."
+            )
+        package_type, package_name = "npm", plain[0]
+        pin = plain[1] if plain[1] and plain[1] != "latest" else None
+        note = " (read from the argv: npm package identity is unavailable)"
+    else:
+        pin = _detect_effective_version_pin(package_type, command, args, env, cwd)
+    if pin and _is_exact_pin(package_type, pin):
+        return None
+    if package_type == "npm" and resolved.source == "manifest":
+        remedy = (
+            f"pin it with `server_version: {{{server_name}: <version>}}` in "
+            "~/.pmcp/manifest.yaml"
+        )
+    else:
+        remedy = "pin an exact version in the args of the config that launches it"
+    state = (
+        f"floats on '{pin}' (a range or tag, not one exact version)"
+        if pin
+        else "is unpinned"
+    )
+    return (
+        f"'{server_name}' talks to a self-hosted backend ({relaxed_by} is set) "
+        f"but its client {package_type}:{package_name} {state}{note}, so a spawn "
+        "or `pmcp update` can move it ahead of the server; " + remedy + "."
+    )
+
+
 # Human-readable label for a ResolvedServerConfig.source, used in messages
 # that need to point an operator at the file a pin (or other override) came
 # from.
@@ -2675,6 +2784,8 @@ class GatewayTools:
                 )
             )
 
+        self._attach_version_pin_warnings(servers)
+
         diagnostics = self._transport_diagnostics.model_copy()
         diagnostics.audit_buffer_size = self._audit_events.maxlen or len(
             self._audit_events
@@ -2697,6 +2808,69 @@ class GatewayTools:
             audit_events=list(self._audit_events) or None,
         )
 
+    def _version_pin_warning(self, server_name: str) -> str | None:
+        """`_unpinned_self_hosted_warning` for *server_name*'s effective config."""
+        manifest_server = load_manifest().get_server(server_name)
+        if manifest_server is None or not manifest_server.api_key_optional_when:
+            return None
+        resolved = self._load_all_configured_servers().get(
+            server_name
+        ) or manifest_server_to_config(manifest_server)
+        return _unpinned_self_hosted_warning(
+            server_name, manifest_server, resolved, self._project_root
+        )
+
+    #: (manifest_sources_fingerprint(), relaxer-declaring manifest entries).
+    _relaxable_cache: tuple[tuple[object, ...], dict[str, ServerConfig]] | None = None
+
+    def _relaxable_manifest_servers(self) -> dict[str, ServerConfig]:
+        """Manifest entries that declare ``api_key_optional_when``, cached.
+
+        gateway.health is polled; ``load_manifest()`` costs ~90 ms and logs a
+        WARNING per call while an unapproved project overlay is present. Re-load
+        only when a manifest source (or the trust store) changes on disk
+        (Consiliency/pmcp#295 board, claude F3).
+        """
+        key = manifest_sources_fingerprint()
+        cached = self._relaxable_cache
+        if cached is not None and cached[0] == key:
+            return cached[1]
+        relaxable = {
+            name: server
+            for name, server in load_manifest().servers.items()
+            if server.api_key_optional_when
+        }
+        self._relaxable_cache = (key, relaxable)
+        return relaxable
+
+    def _attach_version_pin_warnings(self, servers: list[ServerHealthInfo]) -> None:
+        """Add the unpinned-self-hosted warning to each health entry it applies to.
+
+        Judged on the config the gateway actually CONNECTED the server with
+        (``get_connected_configs``), not on a fresh config read: that is the
+        argv and env that are running, and it costs no file I/O. A server that
+        is not connected is not judged here (update_server still warns for
+        it). Advisory only: any failure is logged at DEBUG and the entries are
+        left as they are.
+        """
+        try:
+            relaxable = self._relaxable_manifest_servers()
+            wanted = [info for info in servers if info.name in relaxable]
+            if not wanted:
+                return
+            connected = self._client_manager.get_connected_configs()
+            for info in wanted:
+                resolved = connected.get(info.name)
+                if resolved is None:
+                    continue
+                warning = _unpinned_self_hosted_warning(
+                    info.name, relaxable[info.name], resolved, self._project_root
+                )
+                if warning:
+                    info.warnings.append(warning)
+        except Exception as exc:  # advisory; never fail health over it
+            logger.debug(f"version-pin warnings skipped: {exc}")
+
     def _config_source_paths_by_server(self) -> dict[str, tuple[str, str]]:
         paths: dict[str, tuple[str, str]] = {}
         for source in load_config_sources(
@@ -5319,7 +5493,26 @@ class GatewayTools:
         Freezing the ambient environment across the update is deliberately NOT
         done here; it would mean threading a frozen env through ClientManager,
         which is a separate concern from this TOCTOU.
+
+        Wrapped (Consiliency/pmcp#294): the unpinned-self-hosted warning is
+        computed on the configuration as it stands AFTER the update attempt and
+        never changes ``ok`` -- an unpinned self-hosted client is still updated,
+        and still warned about, because it is still unpinned.
         """
+        result = await self._update_server_unwarned(input_data)
+        try:
+            warning = self._version_pin_warning(result.server)
+        except Exception as exc:  # advisory; never fail the update over it
+            logger.debug(f"version-pin warning skipped: {exc}")
+            warning = None
+        if warning:
+            result.warnings.append(warning)
+        return result
+
+    async def _update_server_unwarned(
+        self, input_data: dict[str, Any]
+    ) -> UpdateServerOutput:
+        """The body of ``update_server``; its docstring states the contracts."""
         parsed = UpdateServerInput.model_validate(input_data)
         server_name = parsed.server_name
 
@@ -5415,14 +5608,70 @@ class GatewayTools:
             source_desc = _CONFIG_SOURCE_LABELS.get(
                 resolved_config.source, f"the {resolved_config.source} config"
             )
+            # "Pinned at X, newer available: Y" (Consiliency/pmcp#294). A
+            # registry READ only -- nothing is probed, fetched or restarted.
+            # `get_package_version` strips the pin off the spec, so this is the
+            # registry's latest, compared three-way (never negated, #164).
+            latest_available, _ = await get_package_version(
+                command, args, server_env, server_cwd, timeout=5.0
+            )
+            # A range or dist-tag still stops this tool (the operator chose
+            # it), but it is not a pin: npx re-resolves it at every spawn, so
+            # it is reported as FLOATING, never as "pinned at ^3.25.0"
+            # (Consiliency/pmcp#295 board, codex P2 / claude F1).
+            exact = _is_exact_pin(package_type, pinned_to)
+            # SemVer build metadata selects nothing: `pkg@3.25.5+x` runs
+            # 3.25.5. Report what runs, and say the suffix was ignored -- the
+            # manifest path refuses such a pin outright; a configured argv is
+            # the operator's own and is labelled honestly instead (#295 board
+            # round 2, N3).
+            build_note = ""
+            if exact and package_type in ("npm", "cargo") and "+" in pinned_to:
+                pinned_to, _, build = pinned_to.partition("+")
+                build_note = (
+                    f" (build metadata '+{build}' is ignored by {package_type})"
+                )
+            comparison = (
+                compare_versions(pinned_to, latest_available, package_type)
+                if latest_available and exact
+                else None
+            )
+            if not exact:
+                availability = (
+                    f"'{pinned_to}' is a range or tag, not one exact version: it "
+                    "re-resolves at every spawn, so it does not hold the client "
+                    f"still (latest: {latest_available or 'unknown'})."
+                )
+            elif comparison == "newer":
+                availability = (
+                    f"Pinned at {pinned_to}, newer available: {latest_available}."
+                )
+            elif comparison == "not_newer":
+                availability = f"Pinned at {pinned_to}, up to date."
+            elif comparison == "incomparable":
+                availability = (
+                    f"Pinned at {pinned_to}; the latest ({latest_available}) "
+                    "cannot be ordered against it."
+                )
+            else:
+                availability = (
+                    f"Pinned at {pinned_to}; the latest version could not be "
+                    "determined."
+                )
             return UpdateServerOutput(
                 ok=False,
                 server=server_name,
                 package_type=package_type,
                 package_name=package_name,
+                pinned_version=pinned_to if exact else None,
+                floating_selector=None if exact else pinned_to,
+                latest_available=latest_available,
+                latest_comparison=comparison,
                 message=(
-                    f"'{server_name}' is pinned to '{pinned_to}' in {source_desc} "
-                    f"({command} {' '.join(args)}). gateway.update_server will not "
+                    f"'{server_name}' is {'pinned to' if exact else 'held at'} "
+                    f"'{pinned_to}'{build_note} in {source_desc} "
+                    f"({command} {' '.join(args)}). {availability} "
+                    "gateway.update_server will not "
                     "move a pinned server to the latest version -- edit or remove "
                     "the pin in that config to allow updates."
                 ),
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 215088d..3772008 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -898,6 +898,9 @@ class ServerHealthInfo(BaseModel):
     auth_metadata: AuthMetadataInfo | None = None
     auth_challenge: AuthChallengeInfo | None = None
     url_elicitations: list[UrlElicitationInfo] | None = None
+    # Advisory, never a status change: e.g. an unpinned client talking to a
+    # self-hosted backend (Consiliency/pmcp#294).
+    warnings: list[str] = Field(default_factory=list)
 
 
 class HealthOutput(BaseModel):
@@ -1371,6 +1374,20 @@ class UpdateServerOutput(BaseModel):
     cancelled_request_count: int = 0
     cancelled_task_count: int = 0
     message: str
+    # Set only when the server is pinned and therefore was not moved
+    # (Consiliency/pmcp#294): the pin, the registry's latest, and
+    # `compare_versions(pinned_version, latest_available)` -- None when the
+    # latest could not be fetched. Three-way on purpose (#164).
+    pinned_version: str | None = None
+    # Set instead of pinned_version when the config holds the server at a
+    # range or dist-tag (`^3.25.0`, `next`): the tool still does not move it,
+    # but it is not a pin -- it re-resolves at every spawn (#295 board).
+    floating_selector: str | None = None
+    latest_available: str | None = None
+    latest_comparison: Literal["newer", "not_newer", "incomparable"] | None = None
+    # Advisory; never changes `ok`. E.g. an unpinned client talking to a
+    # self-hosted backend.
+    warnings: list[str] = Field(default_factory=list)
 
 
 class AuthConnectInput(BaseModel):
diff --git a/tests/test_pkgid_panel_fixes.py b/tests/test_pkgid_panel_fixes.py
index 41cadd8..aa7cf6a 100644
--- a/tests/test_pkgid_panel_fixes.py
+++ b/tests/test_pkgid_panel_fixes.py
@@ -894,6 +894,14 @@ async def test_update_server_still_refuses_a_pinned_manifest_server_as_before(
         manifest_servers={"shipped": server},
     )
 
+    # The pinned branch now reads the registry's latest to report "newer
+    # available" (Consiliency/pmcp#294). Stub it: this refusal path was offline
+    # before that change and must stay offline.
+    async def no_registry(*_args: Any, **_kwargs: Any) -> tuple[None, str]:
+        return (None, "npm")
+
+    monkeypatch.setattr(handlers_module, "get_package_version", no_registry)
+
     result = await gateway.update_server({"server_name": "shipped"})
 
     assert result.ok is False
```

## Test bodies

`tests/test_version_pin.py`, verbatim from the spike (`ruff format`-clean):

```python
"""First-class client version pins (Consiliency/pmcp#294, proposals 1 and 2).

Proposal 1: a manifest entry may carry ``version:``, and an overlay may set it
on an existing (e.g. shipped) entry with ``server_version:`` -- without
restating the install matrix. The pin is materialised into the npx package
slot of ``args`` AND every ``install`` argv, keeping the package NAME from the
entry, so an overlay can choose a version of the same package and nothing
else. ``gateway.update_server`` then reports "pinned at X, newer available: Y".

Proposal 2: an entry whose ``api_key_optional_when`` relaxer is active (a
self-hosted backend is configured) and whose client is unpinned carries a
warning in ``gateway.health`` and ``gateway.update_server``.

Offline: npm argv is read with the node-less tables (``npm_tables``), and the
registry lookup is a monkeypatched ``get_package_version``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from pmcp.config.loader import _merge_manifest_defaults, manifest_server_to_config
from pmcp.manifest.loader import (
    Manifest,
    ServerConfig,
    credential_requirement,
    load_manifest,
    manifest_sources_fingerprint,
    split_plain_registry_spec,
)
from pmcp.policy.policy import PolicyManager
from pmcp.provision_gate import _config_runs_exactly
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools
from pmcp.types import (
    LocalMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)

PLATFORMS = ("mac", "linux", "wsl", "windows")


@pytest.fixture(autouse=True)
def _isolate_overlays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ambient overlay: HOME is already isolated by conftest; add a clean cwd."""
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)


@pytest.fixture
def npm_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read npm argv with the node-less tables, whatever npm the host has."""
    from pmcp.manifest import version_checker

    def tables(
        args: list[str], command: str, env: Any = None, cwd: Any = None
    ) -> str | None:
        return version_checker._npm_package_arg_from_tables(args, command)

    monkeypatch.setattr(version_checker, "_npm_package_arg", tables)
    monkeypatch.setattr(handlers_module, "_npm_package_arg", tables)


def _user_overlay(text: str) -> None:
    path = Path.home() / ".pmcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# Proposal 1 -- the manifest side
# ---------------------------------------------------------------------------


def test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns() -> None:
    """One overlay line pins args AND every install argv; nothing else changes."""
    shipped = load_manifest().servers["firecrawl"]
    assert shipped.version is None
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')

    pinned = load_manifest().servers["firecrawl"]

    assert pinned.version == "3.25.5"
    assert pinned.args == ["-y", "firecrawl-mcp@3.25.5"]
    assert pinned.install == {
        p: ["npx", "-y", "firecrawl-mcp@3.25.5"] for p in PLATFORMS
    }
    # The provision gate's own definition of "runs exactly this spec".
    assert _config_runs_exactly(pinned, "firecrawl-mcp@3.25.5")
    for field_name in ("command", "env_var", "api_key_optional_when", "extra_env"):
        assert getattr(pinned, field_name) == getattr(shipped, field_name)


def test_version_replaces_an_existing_tag_on_a_scoped_package() -> None:
    """``@playwright/mcp@latest`` becomes ``@playwright/mcp@1.2.3``, scope kept."""
    _user_overlay('server_version:\n  playwright: "1.2.3"\n')

    pinned = load_manifest().servers["playwright"]

    assert pinned.args == ["-y", "@playwright/mcp@1.2.3"]
    assert _config_runs_exactly(pinned, "@playwright/mcp@1.2.3")


def test_version_key_on_a_whole_servers_entry_is_materialised() -> None:
    _user_overlay(
        """
servers:
  pinned-custom:
    description: "custom"
    keywords: [c]
    install:
      linux: ["npx", "-y", "custom-mcp"]
    command: "npx"
    args: ["-y", "custom-mcp", "--port", "3000"]
    version: "2.0.0-rc.1"
"""
    )

    entry = load_manifest().servers["pinned-custom"]

    assert entry.args == ["-y", "custom-mcp@2.0.0-rc.1", "--port", "3000"]
    assert entry.install == {"linux": ["npx", "-y", "custom-mcp@2.0.0-rc.1"]}


@pytest.mark.parametrize(
    "raw",
    [
        '"^3.25.5"',  # range: re-resolves at every spawn
        '"~3.25.5"',
        '"3.x"',
        '"*"',
        '">=3.25.0"',
        '"latest"',  # dist-tag: the registry moves it
        '"next"',
        '"v3.25.5"',  # not SemVer
        "3.25",  # YAML float
        '"3.25"',
        '"3.25.5 --registry=http://evil.test"',
        '"evil-pkg@1.0.0"',  # an attempt to name a different package
        '"npm:evil-pkg@1.0.0"',
        '"../../tmp/x"',
        '""',
        "true",
        '"3.25.5+evil"',  # build metadata: npm ignores it, reports would echo it
    ],
)
def test_server_version_refuses_anything_but_one_exact_version(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    shipped = load_manifest().servers["firecrawl"]
    _user_overlay(f"server_version:\n  firecrawl: {raw}\n")

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["firecrawl"]

    assert entry.version is None
    assert entry.args == shipped.args
    assert entry.install == shipped.install
    assert any("firecrawl" in m and "server_version" in m for m in _warnings(caplog))


def test_server_version_cannot_create_a_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _user_overlay('server_version:\n  no-such-server: "1.0.0"\n')

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()

    assert "no-such-server" not in manifest.servers
    assert any("no-such-server" in m for m in _warnings(caplog))


def test_unapproved_project_server_version_contributes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    monkeypatch.chdir(tmp_path / "proj")

    entry = load_manifest().servers["firecrawl"]

    assert entry.version is None
    assert entry.args == ["-y", "firecrawl-mcp"]


def test_approved_project_server_version_applies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")

    assert load_manifest().servers["firecrawl"].args == ["-y", "firecrawl-mcp@3.25.5"]


def test_version_on_a_uvx_server_is_refused_with_the_escape_hatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shipped = load_manifest().servers["fetch"]
    assert shipped.command == "uvx"
    _user_overlay('server_version:\n  fetch: "1.0.0"\n')

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["fetch"]

    assert entry.version is None
    assert entry.args == shipped.args
    assert any("npx" in m and ".mcp.json" in m for m in _warnings(caplog))


def test_version_is_refused_when_an_install_argv_names_another_package(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pinning args but not install would approve X and run latest."""
    _user_overlay(
        """
servers:
  split-brain:
    description: "args and install disagree"
    keywords: [s]
    install:
      linux: ["npx", "-y", "other-mcp"]
    command: "npx"
    args: ["-y", "split-mcp"]
server_version:
  split-brain: "1.0.0"
"""
    )

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["split-brain"]

    assert entry.version is None
    assert entry.args == ["-y", "split-mcp"]
    assert entry.install == {"linux": ["npx", "-y", "other-mcp"]}


# One case per npm-package-arg class that is NOT a registry version/tag
# (the plan's class table). Round 2 (B1) found the tarball class missing: every
# earlier file/URL case carried a `:` prefix, which the old regex excluded
# anyway, so no test exercised a letter-led tarball.
_NON_PLAIN_SLOTS = {
    "alias": "myalias@npm:firecrawl-mcp@3.25.5",
    "url": "t@https://evil.test/t.tgz",
    "git-url": "t@git+https://evil.test/t.git",
    "hosted-shortcut": "t@github:evil/x",
    "hosted-path": "t@evil/x",
    "git-ssh": "t@git@github.com:evil/x",
    "file-prefix": "t@file:../evil",
    "relative-dir": "t@../evil",
    "home-dir": "t@~/evil",
    "absolute-dir": "t@/abs/evil",
    "tarball-tgz": "t@corp.tgz",
    "tarball-TGZ-mixed": "firecrawl-mcp@corp-mcp.TGZ",
    "tarball-tar": "t@x.tar",
    "tarball-tar.gz": "t@x.tar.gz",
    "tarball-Tar.Gz": "@s/p@X.Tar.Gz",
    "bare-tarball-tgz": "corp.tgz",
    "bare-tarball-TAR": "x.TAR",
    "tarball-name-with-version": "corp.tgz@3.25.5",
    "range-caret": "t@^3.25.0",
    "range-gte": "t@>=1.0.0",
    "range-x": "t@x",
    "range-X": "t@X",
    "range-v-partial": "t@v1.2.x",
    "tag-trailing-newline": "t@latest\n",
    "not-uri-safe-tag": "t@tag!",
}


def _odd_slot_overlay(slot: str, version: str = "3.25.5") -> None:
    _user_overlay(
        yaml.safe_dump(
            {
                "servers": {
                    "odd-slot": {
                        "description": "odd slot",
                        "keywords": ["o"],
                        "install": {"linux": ["npx", "-y", slot]},
                        "command": "npx",
                        "args": ["-y", slot],
                        "version": version,
                    }
                }
            }
        )
    )


@pytest.mark.parametrize(
    "slot", list(_NON_PLAIN_SLOTS.values()), ids=list(_NON_PLAIN_SLOTS)
)
def test_version_refuses_a_slot_that_is_not_a_plain_registry_spec(
    slot: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Only ``name`` or ``name@<version-or-tag>`` may have its version replaced."""
    _odd_slot_overlay(slot)

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["odd-slot"]

    assert split_plain_registry_spec(slot) is None
    assert entry.version is None
    assert entry.args == ["-y", slot]
    assert entry.install == {"linux": ["npx", "-y", slot]}
    assert any("plain registry" in m for m in _warnings(caplog))


@pytest.mark.parametrize(
    ("slot", "pinned"),
    [
        ("t", "t@3.25.5"),  # npa: registry range `*`
        ("t@1.0.0", "t@3.25.5"),  # npa: version
        ("t@1.0.0-rc.1", "t@3.25.5"),
        ("t@next", "t@3.25.5"),  # npa: tag
        ("t@beta-2.tgzx", "t@3.25.5"),  # tag: `.tgzx` is not a tarball suffix
        ("@s/p", "@s/p@3.25.5"),
        ("@s/p.tgz", "@s/p.tgz@3.25.5"),  # a SCOPED name is never a file to npa
        ("@s/p@latest", "@s/p@3.25.5"),
    ],
)
def test_version_pins_every_plain_registry_class(slot: str, pinned: str) -> None:
    _odd_slot_overlay(slot)

    entry = load_manifest().servers["odd-slot"]

    assert entry.args == ["-y", pinned]
    assert entry.version == "3.25.5"


def test_version_replaces_a_dist_tag_slot() -> None:
    """A dist-tag is part of the plain-registry grammar and is replaced."""
    _user_overlay(
        """
servers:
  tagged:
    description: "tagged"
    keywords: [t]
    install:
      linux: ["npx", "-y", "tagged-mcp@next"]
    command: "npx"
    args: ["-y", "tagged-mcp@next"]
    version: "1.0.0"
"""
    )

    assert load_manifest().servers["tagged"].args == ["-y", "tagged-mcp@1.0.0"]


@pytest.mark.parametrize(
    "shape",
    [
        'command: "npx"\n    args: ["-y", 123]',
        'command: "npx"\n    args: ["-y", "ok-mcp"]\n    install:\n      linux: ["npx", "-y", 123]',
        'command: 123\n    args: ["-y", "ok-mcp"]',
    ],
)
def test_a_pin_on_a_malformed_entry_costs_only_that_entry(
    shape: str, caplog: pytest.LogCaptureFixture
) -> None:
    """HEAD loads every entry of this overlay; the pin must not change that."""
    shipped_count = len(load_manifest().servers)
    _user_overlay(
        f"""
servers:
  malformed:
    description: "non-string argv"
    keywords: [m]
    {shape}
    version: "1.2.3"
"""
    )

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()  # must not raise

    assert len(manifest.servers) == shipped_count + 1
    assert manifest.servers["malformed"].version is None
    assert manifest.servers["firecrawl"].args == ["-y", "firecrawl-mcp"]
    assert any("malformed" in m for m in _warnings(caplog))


def test_a_config_entry_without_a_command_inherits_the_pin() -> None:
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest_servers = load_manifest().servers

    merged = _merge_manifest_defaults(
        "firecrawl", LocalMcpServerConfig(command="", args=[]), manifest_servers
    )

    assert merged is not None
    assert merged.args == ["-y", "firecrawl-mcp@3.25.5"]


def test_explicit_config_args_win_over_the_manifest_pin() -> None:
    """``.pmcp.json`` with its own command/args is the operator's override."""
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest_servers = load_manifest().servers

    merged = _merge_manifest_defaults(
        "firecrawl",
        LocalMcpServerConfig(command="npx", args=["-y", "firecrawl-mcp@3.20.0"]),
        manifest_servers,
    )

    assert merged is not None
    assert merged.args == ["-y", "firecrawl-mcp@3.20.0"]


# ---------------------------------------------------------------------------
# Harness for the handler tests
# ---------------------------------------------------------------------------


class _ClientManager:
    def __init__(self, statuses: list[ServerStatus] | None = None) -> None:
        self._statuses = statuses or []

    def get_all_tools(self) -> list[Any]:
        return []

    def get_server_status(self, name: str) -> None:
        return None

    def get_all_server_statuses(self) -> list[ServerStatus]:
        return list(self._statuses)

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    def get_connected_configs(self) -> dict[str, ResolvedServerConfig]:
        return dict(self.connected)

    connected: dict[str, ResolvedServerConfig] = {}


def _server(
    name: str,
    args: list[str],
    *,
    relaxer_value: str | None = "http://self-hosted.internal:3002",
    **extra: Any,
) -> ServerConfig:
    return ServerConfig(
        name=name,
        description=name,
        keywords=[name],
        install={p: ["npx", *args] for p in PLATFORMS},
        command="npx",
        args=list(args),
        requires_api_key=True,
        env_var="SELFHOST_API_KEY",
        api_key_optional_when=["SELFHOST_API_URL"],
        extra_env={"SELFHOST_API_URL": relaxer_value} if relaxer_value else {},
        **extra,
    )


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    servers: dict[str, ServerConfig],
    *,
    configured: list[ResolvedServerConfig] | None = None,
    online: list[str] | None = None,
) -> GatewayTools:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers=dict(servers),
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: configured or [])
    policy_path = tmp_path / "gateway-policy.yaml"
    policy_path.write_text("servers: {}\n")
    statuses = [
        ServerStatus(name=n, status=ServerStatusEnum.ONLINE, tool_count=0)
        for n in (online or [])
    ]
    manager = _ClientManager(statuses)
    # What the gateway connected each online server with: the configured entry
    # when there is one, else the manifest's -- as startup resolution picks.
    by_name = {c.name: c for c in configured or []}
    manager.connected = {
        n: by_name.get(n) or manifest_server_to_config(servers[n])
        for n in (online or [])
    }
    gateway = GatewayTools(
        client_manager=cast(Any, manager),
        policy_manager=PolicyManager(policy_path=policy_path),
    )
    cast(Any, gateway)._platform = "linux"
    return gateway


def _latest(monkeypatch: pytest.MonkeyPatch, version: str | None) -> list[list[str]]:
    """Stub the registry lookup; record the args it was asked about."""
    asked: list[list[str]] = []

    async def fake(
        command: str, args: list[str], env: Any, cwd: Any, timeout: float = 10.0
    ) -> tuple[str | None, str]:
        asked.append(list(args))
        return (version, "npm")

    monkeypatch.setattr(handlers_module, "get_package_version", fake)
    return asked


def _no_probe(monkeypatch: pytest.MonkeyPatch, gateway: GatewayTools) -> list[Any]:
    probes: list[Any] = []

    async def probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        probes.append(list(command))
        return (False, "probe output")

    monkeypatch.setattr(gateway, "_run_update_probe_command", probe)
    return probes


# ---------------------------------------------------------------------------
# Proposal 1 -- update_server reports "pinned at X, newer available: Y"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_server_reports_a_newer_version_for_a_pinned_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])}
    )
    asked = _latest(monkeypatch, "3.26.0")
    probes = _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert result.ok is False
    assert probes == []  # a pinned server is never probed or moved
    assert asked == [["-y", "fc-mcp@3.25.5"]]
    assert (result.pinned_version, result.latest_available) == ("3.25.5", "3.26.0")
    assert result.latest_comparison == "newer"
    assert "is pinned to '3.25.5' in the manifest entry" in result.message
    assert "newer available: 3.26.0" in result.message


@pytest.mark.asyncio
async def test_update_server_says_up_to_date_when_the_pin_is_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])}
    )
    _latest(monkeypatch, "3.25.5")

    result = await gateway.update_server({"server_name": "fc"})

    assert result.latest_comparison == "not_newer"
    assert "up to date" in result.message
    assert "newer available" not in result.message


@pytest.mark.asyncio
async def test_update_server_says_so_when_latest_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])}
    )
    _latest(monkeypatch, None)

    result = await gateway.update_server({"server_name": "fc"})

    assert (result.pinned_version, result.latest_available) == ("3.25.5", None)
    assert result.latest_comparison is None
    assert "latest version could not be determined" in result.message


# ---------------------------------------------------------------------------
# Proposal 2 -- unpinned client against a self-hosted backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_warns_when_a_self_hosted_backend_client_is_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])}, online=["fc"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "SELFHOST_API_URL" in info.warnings[0]
    assert "unpinned" in info.warnings[0]
    assert "server_version" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_treats_latest_as_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@latest"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1


@pytest.mark.asyncio
async def test_health_is_silent_when_the_relaxer_is_not_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Vendor-hosted (no self-hosted URL): unpinned is the intended default."""
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp"], relaxer_value=None)},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_health_is_silent_when_the_client_is_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"], version="3.25.5")},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_health_judges_the_configured_entry_not_the_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """The ViperJuice/dotfiles#325 shape: ``.pmcp.json`` pins with explicit args."""
    configured = ResolvedServerConfig(
        name="fc",
        source="user",
        config=LocalMcpServerConfig(
            command="npx",
            args=["-y", "fc-mcp@3.25.5"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp"])},
        configured=[configured],
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.parametrize(
    "spec", ["fc-mcp@^3.25.5", "fc-mcp@~3.25.5", "fc-mcp@3.x", "fc-mcp@next"]
)
@pytest.mark.asyncio
async def test_health_warns_on_a_range_or_dist_tag(
    spec: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """A range or tag re-resolves at every spawn: it is not a pin."""
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", spec])}, online=["fc"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "floats on" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_warns_when_the_relaxer_comes_from_the_gateway_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """The child inherits the gateway's env, so an exported URL is self-hosting.

    Advisory only: the credential gate still ignores the ambient value.
    """
    server = _server("fc", ["-y", "fc-mcp"], relaxer_value=None)
    monkeypatch.setenv("SELFHOST_API_URL", "http://self-hosted.internal:3002")
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "SELFHOST_API_URL" in info.warnings[0]
    # The gate is unchanged: it never reads os.environ (#124).
    assert credential_requirement(server).required is True


@pytest.mark.asyncio
async def test_health_loads_the_manifest_once_until_a_source_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])}, online=["fc"]
    )
    loaded = handlers_module.load_manifest
    loads: list[int] = []

    def counting() -> Manifest:
        loads.append(1)
        return loaded()

    monkeypatch.setattr(handlers_module, "load_manifest", counting)

    for _ in range(3):
        health = await gateway.health()
    assert len(loads) == 1
    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1  # the cached answer is still an answer

    _user_overlay("server_env: {}\n")  # a manifest source changed on disk
    await gateway.health()
    assert len(loads) == 2


def test_fingerprint_changes_when_a_project_overlay_appears(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    (project / ".pmcp").mkdir(parents=True)
    monkeypatch.chdir(project)
    before = manifest_sources_fingerprint()

    (project / ".pmcp" / "manifest.yaml").write_text(
        'server_version:\n  firecrawl: "3.25.5"\n'
    )

    assert manifest_sources_fingerprint() != before


def test_fingerprint_changes_when_the_project_overlay_is_approved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """Approval changes what loads without touching the overlay file."""
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    monkeypatch.chdir(tmp_path / "proj")
    before = manifest_sources_fingerprint()
    assert load_manifest().servers["firecrawl"].version is None

    approve_project_file(overlay)

    assert manifest_sources_fingerprint() != before
    assert load_manifest().servers["firecrawl"].version == "3.25.5"


@pytest.fixture
def npm_identity_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """What `npm_config_cache` in the gateway env produces: every npm argv refused."""
    from pmcp.manifest import version_checker

    def refused(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(version_checker, "_npm_package_arg", refused)
    monkeypatch.setattr(handlers_module, "_npm_package_arg", refused)


@pytest.mark.asyncio
async def test_health_still_warns_when_npm_identity_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])}, online=["fc"]
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "is unpinned (read from the argv" in info.warnings[0]


@pytest.mark.asyncio
async def test_health_reads_an_exact_pin_structurally_when_identity_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.asyncio
async def test_health_says_it_cannot_verify_an_unreadable_slot_without_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "-p", "other", "fc-mcp@3.25.5"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]


@pytest.mark.asyncio
async def test_update_server_labels_build_metadata_with_what_npm_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """A configured `pkg@3.25.5+evil` runs 3.25.5; the label must say that."""
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@3.25.5+evil"])}
    )
    _latest(monkeypatch, "3.26.0")

    result = await gateway.update_server({"server_name": "fc"})

    assert (result.pinned_version, result.latest_comparison) == ("3.25.5", "newer")
    assert "build metadata '+evil' is ignored by npm" in result.message
    assert "3.25.5+evil" not in (result.pinned_version or "")


@pytest.mark.asyncio
async def test_update_server_reports_a_range_as_floating_not_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(
        monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp@^3.25.0"])}
    )
    _latest(monkeypatch, "3.26.0")
    probes = _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert result.ok is False
    assert probes == []  # still not moved: the operator chose the selector
    assert (result.pinned_version, result.floating_selector) == (None, "^3.25.0")
    assert result.latest_comparison is None
    assert "range or tag" in result.message


@pytest.mark.asyncio
async def test_update_server_carries_the_unpinned_self_hosted_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    gateway = _gateway(monkeypatch, tmp_path, {"fc": _server("fc", ["-y", "fc-mcp"])})
    probes = _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert probes == [["npx", "-y", "fc-mcp@latest", "--help"]]  # not blocked
    assert len(result.warnings) == 1
    assert "SELFHOST_API_URL" in result.warnings[0]


# ---------------------------------------------------------------------------
# pmcp update rendering
# ---------------------------------------------------------------------------


def test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed() -> None:
    from pmcp.cli import _format_update_result

    lines = _format_update_result(
        {
            "ok": False,
            "server": "firecrawl",
            "pinned_version": "3.25.5",
            "latest_available": "3.26.0",
            "latest_comparison": "newer",
            "message": "long message",
            "warnings": [],
        }
    )

    assert lines == ["[PINNED] firecrawl: pinned at 3.25.5, newer available: 3.26.0"]


def test_pmcp_update_renders_a_range_as_floating() -> None:
    from pmcp.cli import _format_update_result

    lines = _format_update_result(
        {
            "ok": False,
            "server": "fc",
            "floating_selector": "^3.25.0",
            "latest_available": "3.26.0",
            "message": "long message",
            "warnings": [],
        }
    )

    assert lines == [
        "[FLOATING] fc: held at ^3.25.0, a range or tag that re-resolves at "
        "every spawn (latest 3.26.0)"
    ]


def test_pmcp_update_prints_warnings_under_the_result() -> None:
    from pmcp.cli import _format_update_result

    lines = _format_update_result(
        {
            "ok": True,
            "server": "firecrawl",
            "message": "Updated.",
            "warnings": ["unpinned against a self-hosted backend"],
        }
    )

    assert lines == [
        "[OK] firecrawl: Updated.",
        "  warning: unpinned against a self-hosted backend",
    ]
```

## Documentation impact

- `README.md`: **modify**. Add a subsection, "Pinning a client version", right after
  the `server_env` / `api_key_optional_when` paragraph (search for
  `FIRECRAWL_API_URL: "http://localhost:3002"`). It covers:
  - the `server_version:` YAML shown in D2;
  - the exact-version-only rule and why ranges and dist-tags are refused;
  - npx only, and the `.mcp.json`/`.pmcp.json` explicit-args escape hatch for
    uvx/cargo/docker;
  - "a whole-entry `servers:` replace in a higher-precedence overlay drops a
    lower-precedence pin";
  - what `pmcp update` prints for a pinned server (`[PINNED] ...`), and for a range or tag (`[FLOATING] ...`);
  - that a new pin reaches a running server only on respawn, so run `gateway.refresh` after adding one;
  - that aliases, URLs, git/file specs and ranges in an entry's slot are refused (the pin could change the package);
  - the `gateway.health` warning for an unpinned self-hosted client.

  In the gateway-tools table row for `gateway.update_server`, mention that a pinned
  server is reported with the newer version that is available. The existing README
  sentence that `.mcp.json` pinning is "the supported override channel" stays true.
  Add "or `server_version` for a manifest server" to it.
- `CHANGELOG.md`: **modify**. Under `## [Unreleased]`, add an `### Added` entry in the
  house style (bold lead sentence, then detail, then
  `See [#294](https://github.com/Consiliency/pmcp/issues/294).`). Suggested text:

  > - **Pin a built-in server's client version with one overlay line, and see when
  >   a newer one exists.** A manifest entry takes `version: "X.Y.Z"`, and an overlay's
  >   new `server_version:` map sets it on a shipped entry without restating its install
  >   matrix. The pin is written into the npx package slot of `args` and of every
  >   `install` argv, keeping the entry's own package name, so an overlay can choose a
  >   version and never a different package. Only one exact SemVer version is accepted:
  >   ranges and dist-tags such as `latest` re-resolve at every spawn and are refused.
  >   uvx/pip/cargo/docker and remote entries are refused with a message pointing at
  >   `.mcp.json` explicit args. `pmcp update` prints a pinned server as `[PINNED]
  >   firecrawl: pinned at 3.25.5, newer available: 3.26.0` instead of `[FAILED]`, and
  >   `gateway.update_server` returns `pinned_version`, `latest_available` and
  >   `latest_comparison`. `gateway.health` and `gateway.update_server` now also carry a
  >   `warnings` list, which warns when an entry's `api_key_optional_when` relaxer is
  >   active (a self-hosted backend is configured) and its client is unpinned. See
  >   [#294](https://github.com/Consiliency/pmcp/issues/294).
- `CONTRIBUTING.md`: **modify**. In the manifest-entry example (the
  `api_key_optional_when` comment block around line 70), add one commented line:
  `# Optional: version: "1.2.3"   # exact client pin; npx entries only (see README)`.
- `src/pmcp/manifest/manifest.yaml`: **no change**. No shipped entry is pinned by this
  plan. Shipping a pin for firecrawl would pin every vendor-hosted user too. See open
  question Q1.
- No `docs/**`, `llms*.txt`, `SECURITY.md` or openapi footprint: the new fields are
  optional output fields, and no input schema changes.

## Dependencies & order

1. `types.py` fields first. `handlers.py` constructs them, and without them `mypy src/`
   fails.
2. `loader.py`: grammar, materialiser and overlay map. This is independent of handlers.
3. `handlers.py`: the warning helper, health, the update wrapper and the pinned branch.
4. `cli.py`: the renderer.
5. `tests/test_version_pin.py`, then the docs.

External blockers: none. There is no migration, and the new fields are additive and
optional.

## Follow-up slices (sequenced after this plan; design notes only, not planned)

### Slice 3: optional post-update smoke probe (proposal 3)

- **Manifest shape.** An optional
  `smoke_probe: {tool: "firecrawl_search", arguments: {query: "example domain", limit: 1}}`
  on an entry, which an overlay can set via a `server_smoke_probe:` patch map. It follows
  the same "patch an existing server, cannot create" rule as `server_env`/`server_version`.
- **When it runs.** Only after `gateway.update_server` has actually restarted the server
  onto a new version (`restart_result.ok`), and never on a pinned no-op.
- **What counts as a failure (from the 2026-09-26 measurement).** The self-hosted search
  returned an empty `success:true` in **1 of 6** identical requests with no client
  involved. So the probe must assert only "the call did not return a JSON-RPC error, an
  `isError: true` tool result, or an HTTP 4xx/5xx in its text". It must **never** assert
  that results are non-empty. On failure it retries once, and only a second failure is
  attributed to the version change.
- **Report.** The message names the previous and the new client versions. The previous
  version comes from what the update resolved before the probe. For a manifest npm
  entry, a failure message can suggest the exact
  `server_version: {name: <previous>}` line, because this plan makes that a one-line
  rollback. Automatic rollback is out of scope: it would rewrite the operator's overlay
  file.

### Slice 4: client version in error hints (proposal 4)

- **What exists, measured.** There is none. `grep -rn "installed_version\|resolve_spawned_version" src/pmcp` finds nothing. The
  spawn-time-provenance plan
  (`.consiliency/plans/detailed-spawn-time-version-provenance-20260817-1800.md`) was
  not implemented: automatic update notices were removed instead (Consiliency/pmcp#150,
  quoted at `handlers.py` in `update_server`: "pmcp cannot observe which artifact a
  running server executes").
- **What this plan makes possible.** For a **pinned** npx argv, the argv itself names
  the version that ran. No inference is needed, because `_detect_effective_version_pin`
  on the spawned config is the answer. Slice 4 can therefore record, at connect time on
  `ManagedClient`, the `(package, pinned_version | "unpinned")` pair read from the argv
  it spawned. When a downstream tool result is a 4xx/schema-validation error (the
  `400 Invalid request body` class), it adds `client firecrawl-mcp@3.25.5 (pinned)` or
  `client firecrawl-mcp (unpinned: whatever npx resolved at spawn)` to
  `gateway.invoke`'s `errors`/`feedback_hint`. Naming an *unpinned* client's exact
  version is the #150 problem again and stays out of scope. The hint says so and names
  the `server_version` remedy.

## Verification

**Prerequisites** (memory: `worktree-needs-uv-sync-p310`):

- Run `uv sync -p 3.10 --all-extras` in the worktree. A bare `uv sync` leaves out
  ruff, mypy and pytest-timeout, and resolves to the system Python.
- Run **`unset npm_config_cache npm_config_store_dir pnpm_config_store_dir` in the same
  shell command** as every pytest invocation. With them set, the npm resolver is
  DISABLED by design, and every npm server reads as `unknown`. The pin tests would then
  go red for the wrong reason.

All steps below were run this session against the spike (the diff and test file above),
on Python 3.10.12 via `uv run`. The expected results are the measured ones.

```bash
cd "$WORKTREE"   # a fresh worktree off origin/main, with the diff + test file applied

# 0. The new tests exist.
uv run pytest tests/test_version_pin.py --collect-only -q --cov-fail-under=0 | tail -1
#   -> 89 tests collected

# 1. Red on HEAD (before the diff; the test file alone).
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py -q --cov-fail-under=0 --tb=line | tail -1
#   -> ImportError: cannot import name 'manifest_sources_fingerprint' from 'pmcp.manifest.loader' -- 1 error during collection
#      (rev 3: the file imports manifest_sources_fingerprint and split_plain_registry_spec,
#      which HEAD lacks, so collection itself is red; the per-test red evidence is the
#      rev-1/rev-2 spike runs below and in the Revision tables)
#   rev 2 was: 56 failed, 1 passed   (the 1 is test_explicit_config_args_win_over_the_manifest_pin,
#      an inertness guard that is green on HEAD by design)
#   Against the REVISION-1 spike: 19 failed, 38 passed; exactly the 19 new rev-2 cases
#   (Revision 2 table).
#   Against the REVISION-2 spike: 15 failed, 74 passed (Revision 3 table; measured with a
#   one-line import shim, because rev 2 has no public split_plain_registry_spec).

# 2. Green with the diff.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py -q --cov-fail-under=0 | tail -1
#   -> 89 passed

# 3. CI gates (all three are in .github/workflows).
uv run ruff check src/ tests/                 # -> All checks passed!
uv run ruff format --check src/ tests/        # -> 162 files already formatted
uv run mypy src/                              # -> Success: no issues found in 49 source files

# 4. The neighbouring suites that own the touched contracts.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py tests/test_manifest_overlay.py \
    tests/test_project_source_consent_manifest.py tests/test_pkgid_panel_fixes.py \
    tests/test_package_identity_gate.py tests/test_credential_gates_handlers.py \
    tests/test_credential_gates_startup.py tests/test_credential_optionality_e2e.py \
    tests/test_manifest_provision.py tests/test_version_checker.py \
    tests/test_credential_predicate_guard.py -q --cov-fail-under=0 | tail -1
#   -> 1620 passed, 19 deselected   (rev 3; rev 2: 1588, adding the credential-predicate guard for F2)

# 5. Whole suite: Bash run_in_background, wait for the notification. Do not detach with
#    nohup/disown. Use a lane-unique log path.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/ -q --cov-fail-under=0 -p no:cacheprovider > "$LOGDIR/294-suite.log" 2>&1
#   -> 4160 passed, 3 skipped, 25 deselected in 442.33s (0:07:22)   (board revision 3; revision 2: 4128 passed, 3 skipped, 25 deselected in 422.12s; earlier: 4108 passed; the first spike was 1 failed / 4107 passed -- see the mutation-table note;
#      revision 3 adds only the offline stub in test_pkgid_panel_fixes.py, re-measured by step 5b)

# 5b. Hermeticity: no update_server/health test may reach a real registry. The plugin
#     makes every registry lookup raise; run it over the files that drive either tool.
mkdir -p "$LOGDIR/plug" && cat > "$LOGDIR/plug/nonet.py" <<'PY'
import pmcp.manifest.version_checker as vc
async def _boom(*a, **k):
    raise RuntimeError("NETWORK-LOOKUP")
for n in ("get_npm_version", "get_pypi_version", "get_cargo_version", "get_docker_version"):
    setattr(vc, n, _boom)
PY
F=$(grep -rln "update_server(\|\.health()\|gateway.health" tests/ | grep -v harness.py | sort | tr '\n' ' ')
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  PYTHONPATH="$LOGDIR/plug" uv run pytest ${=F} -q --cov-fail-under=0 -p nonet -p no:cacheprovider | tail -1
#   (zsh: ${=F} word-splits; in bash use $F)
#   -> 910 passed, 1 deselected (board revision 3; 878 in revision 2, 858 in revision 1, measured with the stub). The spike WITHOUT the panel-fixes stub gave
#      "1 failed, 857 passed, 1 deselected" (the one offender named above); with the stub,
#      the offender and tests/test_version_pin.py pass under the plugin (84 passed for those
#      two files). On HEAD, the 6-file update_server subset gives 357 passed, 0 lookups.

# 6. The pre-existing pinned-refusal assertion still holds (substring kept; now stubbed offline).
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest "tests/test_pkgid_panel_fixes.py::test_update_server_still_refuses_a_pinned_manifest_server_as_before" -q --cov-fail-under=0 | tail -1
#   -> 1 passed

# 7. Coverage of the shipped manifest (read-only; shows which built-ins can be pinned).
uv run python - <<'EOF'
from dataclasses import replace
from pathlib import Path
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
m = load_manifest(Path("src/pmcp/manifest/manifest.yaml"))
res = {n: _materialize_version_pin(replace(s, version="1.0.0")).version for n, s in m.servers.items()}
print(sum(v is not None for v in res.values()), "pinnable,", sum(v is None for v in res.values()), "refused")
assert res["firecrawl"] == "1.0.0"
EOF
#   -> 77 pinnable, 30 refused   (19 uvx, 9 remote/empty command, cloudflare (url), context7 (windows `cmd /c npx`))

# 8. Mutation table below: each mutant applied to the implemented tree, then
#    `uv run pytest tests/test_version_pin.py -q --tb=line`, then restored and `cmp`-verified.

# 9. Plan-only commit check (for THIS planning commit, not the implementation PR).
git diff --stat 9ca081e -- src/ tests/          # -> empty
uv run python ~/code/pmcp/scripts/check_plan_consistency.py .consiliency/plans/detailed-294-version-pin-*.md
#   -> lane-contracted: 0   EC-proved node ids: 0 / consistent / blocking inconsistencies: 0
#      (vacuous for a detailed plan: it has no lane table or EC ids; run for the record)
```

```bash
# 10. Grammar conformance against npm's own classifier (needs node + npm; not a CI gate).
#     npa_classify.js prints npa's (type, name, registry) for each slot; the Python side
#     asserts every slot split_plain_registry_spec accepts is a same-name registry
#     version/tag (or a bare name). Both files are below.
uv run python grammar_conformance.py <dir-with-npa_classify.js>
#   -> slots: 461  accepted: 72 by npa class {'range': 6, 'version': 18, 'tag': 48}
#      refused: 389 by npa class {'ERROR:EINVALIDPACKAGENAME': 1, 'ERROR:EINVALIDTAGNAME': 115, 'alias': 6,
#               'directory': 61, 'file': 53, 'git': 30, 'range': 79, 'remote': 7, 'tag': 25, 'version': 12}
#      VIOLATIONS (accepted but not a same-name registry version/tag): 0
#   (the revision-2 rule on the same corpus: accepted 226, VIOLATIONS 136 -- file, range, invalid)
```

`npa_classify.js` (set `<NPM_ROOT>` to `$(dirname $(readlink -f $(which npm)))/..`):

```javascript
const npa = require("<NPM_ROOT>/node_modules/npm-package-arg");
const slots = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = {};
for (const s of slots) {
  try { const r = npa(s, "/tmp"); out[s] = [r.type, r.name || null, r.registry ? true : false]; }
  catch (e) { out[s] = ["ERROR:" + e.code, null, false]; }
}
console.log(JSON.stringify(out));
```

`grammar_conformance.py`:

```python
"""Every slot split_plain_registry_spec accepts must be an npm REGISTRY fetch of
the same name, of class version or tag (or the bare-name range `*`)."""
import json, subprocess, sys
from collections import Counter
from pmcp.manifest.loader import split_plain_registry_spec
S = sys.argv[1]
names = ["firecrawl-mcp", "@scope/pkg", "t", "corp.tgz", "x.tar", "a.TAR.GZ", "@s/p.tgz", "x", "latest"]
selectors = ["", "3.25.5", "3.25.5-rc.1", "3.25.5+b", "v3.25.5", "=3.25.5", "latest", "next", "beta-2", "x", "X", "x.x",
  "v1", "v1.2.x", "1.x", "*", "^3.25.5", "~3.25.5", ">=1.0.0", "1 - 2", "1||2", "corp.tgz", "corp-mcp.TGZ", "x.tar", "x.tar.gz",
  "X.Tar.Gz", "npm:other@1", "file:../x", "../x", "./x", "~/x", "/abs/x", "C:x", "github:a/b", "a/b", "git+https://h/x.git",
  "https://h/x.tgz", "git@github.com:a/b", "latest\n", "late st", "tag!", "tag(1)", "ｘ", "вeta", "a.b", "tgz", "tar", "x-beta", "v", "vnext"]
slots = []
for n in names:
    for sel in selectors:
        slots.append(n if sel == "" else f"{n}@{sel}")
slots += ["-y", "--package=x", "https://h/x.tgz", "git+ssh://h/x", "github:a/b", "../dir", "./x.tgz", "a/b", "@@x", "x@", "@s/p@corp.tgz"]
res = json.loads(subprocess.run(["node", f"{S}/npa_classify.js"], input=json.dumps(slots), capture_output=True, text=True, check=True).stdout)
bad = []; acc = Counter(); ref = Counter()
for s in slots:
    t, nm, reg = res[s]
    ours = split_plain_registry_spec(s)
    if ours is not None:
        acc[t] += 1
        ok = reg and nm == ours[0] and (t in ("version", "tag") or (t == "range" and ours[1] is None))
        if not ok: bad.append((s, t, nm, ours))
    else:
        ref[t] += 1
print(f"slots: {len(slots)}  accepted: {sum(acc.values())} by npa class {dict(acc)}")
print(f"refused: {sum(ref.values())} by npa class {dict(sorted(ref.items()))}")
print(f"VIOLATIONS (accepted but not a same-name registry version/tag): {len(bad)}")
for b in bad: print("   ", b)
```

### Live check (optional, network, not a gate)

The operator can confirm the report end to end against the real registry:

```bash
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; uv run python - <<'EOF'
import asyncio
from pmcp.manifest.version_checker import get_package_version, compare_versions
v, t = asyncio.run(get_package_version("npx", ["-y", "firecrawl-mcp@3.25.4"], None, None, timeout=10))
print(v, t, compare_versions("3.25.4", v, t))
EOF
#   measured 2026-09-26: 3.25.5 npm newer
```

### Mutation table

Each mutant was applied to the spike with an exact one-occurrence string replace, then
confirmed by `diff` against the saved spike copy (the `diff` hunk header names the mutated
line). `tests/test_version_pin.py` was run with `--tb=line`, and the file was restored and
checked with `cmp` (`restored=True` for every row). The driver script is
`mutants.py`, embedded at the end of this plan.

| # | mutation | applied | result | first `E` line (verbatim, truncated at 160) / failing tests |
|---|---|---|---|---|
| M1 | grammar accepts any string | `579c579` | **13 failed, 76 passed** (restored=True) | `AssertionError: assert '^3.25.5' is None`; `test_server_version_refuses_anything_but_one_exact_version["*"]`, `test_server_version_refuses_anything_but_one_exact_version["../../tmp/x"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25.5 --registry=http://evil.test"]`, `test_server_version_refuses_anything_but_one_exact_version["3.x"]`, `test_server_version_refuses_anything_but_one_exact_version[">=3.25.0"]`, `test_server_version_refuses_anything_but_one_exact_version["^3.25.5"]`, `test_server_version_refuses_anything_but_one_exact_version["evil-pkg@1.0.0"]` (+5 more) |
| M2 | install argv not pinned | `726c726` | **3 failed, 86 passed** (restored=True) | `AssertionError: assert {'mac': ['npx...recrawl-mcp']} == {'mac': ['npx...-mcp@3.25.5']}`; `test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns`, `test_version_key_on_a_whole_servers_entry_is_materialised`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M3 | existing tag not replaced | `671c671` | **7 failed, 82 passed** (restored=True) | `AssertionError: assert ['-y', '@play...latest@1.2.3'] == ['-y', '@play...ht/mcp@1.2.3']`; `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-rc.1-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@beta-2.tgzx-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@next-t@3.25.5]`, `test_version_replaces_a_dist_tag_slot`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M4 | servers: version: key ignored | `846c846` | **38 failed, 51 passed** (restored=True) | `AssertionError: assert ['-y', 'custo...port', '3000'] == ['-y', 'custo...port', '3000']`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: "npx"\n    args: ["-y", "ok-mcp"]\n    install:\n      linux: ["npx", "-y", 123]]`, `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: "npx"\n    args: ["-y", 123]]`, `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]`, `test_version_key_on_a_whole_servers_entry_is_materialised`, `test_version_pins_every_plain_registry_class[@s/p-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[@s/p.tgz-@s/p.tgz@3.25.5]`, `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t-t@3.25.5]` (+30 more) |
| M5 | unapproved project overlay applied | `1121c1121` | **2 failed, 87 passed** (restored=True) | `AssertionError: assert '3.25.5' is None`; `test_fingerprint_changes_when_the_project_overlay_is_approved`, `test_unapproved_project_server_version_contributes_nothing` |
| M6 | non-npx command accepted | `699c699` | **2 failed, 87 passed** (restored=True) | `assert False`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]`, `test_version_on_a_uvx_server_is_refused_with_the_escape_hatch` |
| M7 | install may name another package | `721c721` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert '1.0.0' is None`; `test_version_is_refused_when_an_install_argv_names_another_package` |
| M8 | comparison arguments swapped | `5635c5635` | **2 failed, 87 passed** (restored=True) | `AssertionError: assert 'not_newer' == 'newer'`; `test_update_server_labels_build_metadata_with_what_npm_runs`, `test_update_server_reports_a_newer_version_for_a_pinned_server` |
| M9 | relaxer not required for the warning | `505,506d504` | **1 failed, 88 passed** (restored=True) | `assert ["'fc' talks ...nifest.yaml."] == []`; `test_health_is_silent_when_the_relaxer_is_not_active` |
| M10 | pin not consulted for the warning | `535,536d534` | **3 failed, 86 passed** (restored=True) | `assert ["'fc' talks ...nifest.yaml."] == []`; `test_health_is_silent_when_the_client_is_pinned`, `test_health_judges_the_configured_entry_not_the_manifest`, `test_health_reads_an_exact_pin_structurally_when_identity_is_disabled` |
| M11 | health judges the manifest, not the connected config | `2863c2863` | **1 failed, 88 passed** (restored=True) | `assert ["'fc' talks ...nifest.yaml."] == []`; `test_health_judges_the_configured_entry_not_the_manifest` |
| M12 | update_server drops the warning | `5508,5509d5507` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_update_server_carries_the_unpinned_self_hosted_warning` |
| M13 | CLI keys the status off ok | `1032c1032` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert ['[FAILED] fi...long message'] == ['[PINNED] fi...able: 3.26.0']`; `test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed` |
| M14 | health never attaches warnings | `2787d2786` | **10 failed, 79 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_loads_the_manifest_once_until_a_source_changes`, `test_health_says_it_cannot_verify_an_unreadable_slot_without_identity`, `test_health_still_warns_when_npm_identity_is_disabled`, `test_health_treats_latest_as_unpinned`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@3.x]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@^3.25.5]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@next]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@~3.25.5]` (+2 more) |
| M15 | P1: any selector accepted (alias/url/git/file/dir/range) | `645c645` | **22 failed, 67 passed** (restored=True) | `AssertionError: assert ('myalias', 'npm:firecrawl-mcp@3.25.5') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[absolute-dir]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[alias]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[file-prefix]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-ssh]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-url]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[home-dir]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-path]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-shortcut]` (+14 more) |
| M16 | P1: dist-tag slots refused | `640c640` | **5 failed, 84 passed** (restored=True) | `AssertionError: assert ['-y', '@play...t/mcp@latest'] == ['-y', '@play...ht/mcp@1.2.3']`; `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t@beta-2.tgzx-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@next-t@3.25.5]`, `test_version_replaces_a_dist_tag_slot`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M17 | P2: any npm selector counts as exact | `472c472` | **5 failed, 84 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_warns_on_a_range_or_dist_tag[fc-mcp@3.x]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@^3.25.5]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@next]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@~3.25.5]`, `test_update_server_reports_a_range_as_floating_not_pinned` |
| M18 | P2: update reports a range as pinned | `5622c5622` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert ('^3.25.0', None) == (None, '^3.25.0')`; `test_update_server_reports_a_range_as_floating_not_pinned` |
| M19 | P3: materialisation not contained per entry | `1190c1190` | **1 failed, 88 passed** (restored=True) | `TypeError: expected str, bytes or os.PathLike object, not int`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]` |
| M20 | F2: inherited env ignored | `503c503` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_warns_when_the_relaxer_comes_from_the_gateway_environment` |
| M21 | F3: no cache | `2836c2836` | **1 failed, 88 passed** (restored=True) | `assert 3 == 1`; `test_health_loads_the_manifest_once_until_a_source_changes` |
| M22 | F3: fingerprint misses the user overlay | `771d770` | **1 failed, 88 passed** (restored=True) | `assert 1 == 2`; `test_health_loads_the_manifest_once_until_a_source_changes` |
| M23 | N2: build metadata accepted | `579c579` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert '3.25.5+evil' is None`; `test_server_version_refuses_anything_but_one_exact_version["3.25.5+evil"]` |
| M24 | CLI labels a range [FAILED] | `1026c1026` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert ['[FAILED] fc: long message'] == ['[FLOATING] ...test 3.26.0)']`; `test_pmcp_update_renders_a_range_as_floating` |
| M25 | B1: tarball SELECTOR accepted as a tag | `641d640` | **5 failed, 84 passed** (restored=True) | `AssertionError: assert ('t', 'corp.tgz') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-TGZ-mixed]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-Tar.Gz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-tar.gz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-tar]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-tgz]` |
| M26 | B1: bare-tarball / tarball-NAME slot accepted | `635,636d634` | **3 failed, 86 passed** (restored=True) | `AssertionError: assert ('corp.tgz', None) is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[bare-tarball-TAR]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[bare-tarball-tgz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-name-with-version]` |
| M27 | N4: x/X/v1.2.x range words accepted as tags | `642d641` | **3 failed, 86 passed** (restored=True) | `AssertionError: assert ('t', 'x') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-X]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-v-partial]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-x]` |
| M28 | N4: match instead of fullmatch (trailing newline) | `640c640` | **8 failed, 81 passed** (restored=True) | `AssertionError: assert ('myalias', 'npm:firecrawl-mcp@3.25.5') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[alias]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[file-prefix]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-ssh]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-url]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-path]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-shortcut]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[not-uri-safe-tag]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tag-trailing-newline]` |
| M29 | N2: silent when npm identity is disabled | `517,518c517` | **2 failed, 87 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_says_it_cannot_verify_an_unreadable_slot_without_identity`, `test_health_still_warns_when_npm_identity_is_disabled` |
| M30 | N2: silent when the slot cannot be read either | `521a522` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_says_it_cannot_verify_an_unreadable_slot_without_identity` |
| M31 | N3: label keeps +metadata | `5629c5629` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert ('3.25.5+evil', 'newer') == ('3.25.5', 'newer')`; `test_update_server_labels_build_metadata_with_what_npm_runs` |
| M32 | N1: fingerprint misses the project overlay | `774c774` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert (('/mnt/workspace/worktrees/viperjuice/pmcp-294/src/pmcp/manifest/manifest.yaml', 1790403902071608017, 78260), ('/tmp/...l', None, None),`; `test_fingerprint_changes_when_a_project_overlay_appears` |
| M33 | N1: fingerprint misses the trust store | `780c780` | **1 failed, 88 passed** (restored=True) | `AssertionError: assert (('/mnt/workspace/worktrees/viperjuice/pmcp-294/src/pmcp/manifest/manifest.yaml', 1790403902071608017, 78260), ('/tmp/...-viperjuice/pyte`; `test_fingerprint_changes_when_the_project_overlay_is_approved` |

**33 of 33 mutants red** on the board-revision-3 spike (M1-M14 and M17-M24 re-run; M15/M16 rewritten for the new grammar; M25-M33 new for board round 2), each for the named reason. **M26 (bare-tarball clause) is red on `bare-tarball-tgz`, `bare-tarball-TAR` and `tarball-name-with-version`.** After each run the file was restored
from the spike copy, and `cmp` confirmed it identical (`restored=True`).

**A finding from the first full-suite run (fixed in the diff above).** The first spike
made `update_server` a wrapper and moved the long docstring onto the inner
`_update_server_unwarned`. `tests/test_tools.py::test_update_server_docstring_states_both_probe_window_env_contracts`
went red (`1 failed, 4107 passed`), because it asserts that `GatewayTools.update_server.__doc__`
states both probe-window environment contracts. The fix, which is what the diff shows,
keeps the full contract docstring on the **public** `update_server`, adds the #294
paragraph to it, and gives the inner body a one-line pointer docstring. **Implementers
must not move that docstring.**


## Acceptance criteria

- [ ] `tests/test_version_pin.py` passes: 89 tests, Verification step 2.
- [ ] A user overlay line `server_version: {firecrawl: "3.25.5"}` makes
  `load_manifest().servers["firecrawl"].args == ["-y", "firecrawl-mcp@3.25.5"]`, every
  `install` argv equal to `["npx", "-y", "firecrawl-mcp@3.25.5"]`, and
  `provision_gate._config_runs_exactly(entry, "firecrawl-mcp@3.25.5")` true. Proven by
  `test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns`.
- [ ] Every non-exact pin (the 16 parametrized values) leaves the entry's
  `args`/`install` byte-identical to shipped, with `version is None`, and logs a WARNING
  naming `server_version`. Proven by `test_server_version_refuses_anything_but_one_exact_version`.
- [ ] `gateway.update_server` on a pinned server returns `ok=False`, runs no probe, and
  returns `pinned_version="3.25.5"`, `latest_available="3.26.0"`,
  `latest_comparison="newer"`, with a message containing both
  `is pinned to '3.25.5' in the manifest entry` and `newer available: 3.26.0`.
  `pmcp update` renders it as `[PINNED] firecrawl: pinned at 3.25.5, newer available: 3.26.0`.
  Proven by `test_update_server_reports_a_newer_version_for_a_pinned_server` and
  `test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed`.
- [ ] `gateway.health` returns exactly one warning for a relaxer-active, unpinned (or
  `@latest`) client, and `[]` when the relaxer is inactive, when the client is pinned,
  or when a configured entry pins it. `gateway.update_server` carries the same warning
  without being blocked. Proven by the five `test_health_*` tests and
  `test_update_server_carries_the_unpinned_self_hosted_warning`.
- [ ] CI gates are clean (`ruff check`, `ruff format --check`, `mypy src/`), and the
  whole suite shows no failure that is not also present on `9ca081e`
  (Verification steps 3 and 5).

- [ ] Board revision 2: a non-plain npx slot (alias, URL, git, file, range) is never
  pinned; a range or tag warns and reports `[FLOATING]`; a malformed overlay entry
  costs only its own pin (`len(servers) == shipped + 1`); an inherited relaxer warns
  while `credential_requirement(...).required` stays `True`; and health loads the
  manifest once per source change. Proven by the tests named in the Revision 2 table.

- [ ] Board revision 3: no slot that npa classifies as file, directory, git, remote, alias,
  range or invalid is ever pinned. That includes every tarball form, bare or after `@`,
  in any case (`test_version_refuses_a_slot_that_is_not_a_plain_registry_spec`, 25 ids,
  plus Verification step 10: 0 violations). Both fingerprint components have tests. With
  npm identity disabled, the warning still fires or says it cannot verify. A configured
  `+metadata` pin is labelled with what npm runs.

## Non-goals (explicit)

- **Proposals 3 and 4.** These are follow-up slices with design notes above.
- **Pinning a shipped entry in `manifest.yaml`.** A pin shipped for firecrawl would pin
  vendor-hosted users too (see Q1).
- **uvx/pip/cargo/docker `version:`.** Refused with a message pointing at the existing
  explicit-args pin (D5).
- **Merge semantics for `servers:`.** Whole-entry replace is unchanged (Q2).
- **Refreshing the descriptions cache version for a pinned server.** `refresher.py`
  (around line 305 and 340) labels a server's generated descriptions with
  `get_package_version`, which is the registry's **latest**, even when the argv is
  pinned. The tools are listed from the pinned binary but labelled with latest. This is
  pre-existing for `.mcp.json` pins, and this plan makes it more common. The freshness
  short-circuit then regenerates on every upstream release, which costs work but gives
  no wrong answer to `catalog_search`. It is listed as risk R2 and left for a follow-up.
- **Exit codes.** `pmcp update` stays exit 0 for per-server outcomes (Q3).
- **`cmd /c npx` Windows wrappers** (`context7`'s windows install). Refused, consistent
  with `_config_runs_exactly`.

## Risks

- **R1: a pin to a version that does not exist.** It is not caught at load, because
  load is offline by design (D1). The spawn fails loudly (npx: `No matching version
  found`), and `update_server` reports `latest_available` next to the bad pin. This is
  acceptable, and better than a network call in `load_manifest`.
- **R2: the descriptions-cache version label** (see Non-goals). It wastes regeneration
  work, and nothing reads it as the running version (#150 removed the notices that did).
- **R3: `pmcp init` snapshots pinned args.** `cli.py` (around line 1703) writes
  `server.args` into the generated `.mcp.json`. With a manifest pin active, the generated
  entry carries `pkg@X` explicitly, and a later `server_version` change no longer
  reaches it, because explicit args win. That is the same snapshot semantics `init`
  already has for `@latest` entries. The README subsection should say it in one line.
- **R4: health cost (superseded by revision 2, measured).** Now one manifest load per source change, and no config I/O: 1.3 ms steady (was 220.7 ms). Original text follows.  It is one `load_manifest()` per `gateway.health` call. When a
  relaxer-declaring server is in the list, it adds one `load_configs` and one resolver
  query per such server (~0.5 ms each, `npm_resolver.py:460`). There is no config I/O
  when no entry declares a relaxer. The work is wrapped so a failure can never cost
  health its answer.
- **R5: the warning's judgement is only as good as `credential_requirement`.** A
  self-hosted backend that is selected by some *other* variable (not declared in
  `api_key_optional_when`) is not detected. That is deliberate: the relaxer declaration
  is the manifest's only structured statement that "this entry can be self-hosted".
- **R6: the `_OverlayDocument` 4-tuple.** Any out-of-tree caller that unpacks the
  3-tuple breaks. There are none in-tree (grep), and it is a private name.

- **R7 (rev 2): the fingerprint is `stat`-based.** An edit that preserves both mtime_ns
  and size is not seen until the next change. For health's advisory list that is
  acceptable; `update_server` always re-reads.

## Open questions for the maintainer

- **Q1.** Should the shipped `firecrawl` entry pin a default version? This plan says
  **no**: vendor-hosted users should keep following the vendor, and the self-hosted
  operator is warned (D7) and pins with one line.
- **Q2.** Should a partial `servers:` entry (only `version:`) be allowed to merge? This
  plan says **no** and uses `server_version:` instead, because changing `servers:` from
  replace to merge changes every existing overlay's meaning.
- **Q3.** Should `pmcp update --all` exit nonzero when any server FAILED? This is
  unchanged here: today it is always 0, and `[PINNED]` is not a failure either way.
- **Q4.** Should the health warning also fire for a server that exists **only** in
  `.pmcp.json`, with no manifest entry? Without a manifest entry there is no
  `api_key_optional_when` declaration, so there is no structured way to know it is
  self-hosted. This plan says no.
- **Q5.** Should `context7`'s windows install (`cmd /c npx ...`) be normalised so that
  context7 becomes pinnable? That would also touch `_config_runs_exactly`'s notion of an
  npx argv, so it is out of scope here.

## Execution Policy

- execute: effort=high, reason=security-adjacent argv rewriting (npm identity, overlay
  consent, credential-gate reads); every invariant has a named test and mutant.

## Handoff

- Branch: implement on a fresh worktree from `origin/main`. Apply the production diff
  and the test file verbatim, add the three doc edits, and run Verification 0-7.
- Commit subject: `feat(manifest): first-class client version pins and an unpinned
  self-hosted warning (see Consiliency/pmcp#294)`. The body references
  Consiliency/pmcp#294 and must not use a closing keyword, because proposals 3 and 4
  remain open.
- PR: cross-vendor panel CR plus reconcile before merge (repo rule).

## Appendix: mutation driver (`mutants.py`, run from the scratch dir with the board-revision-3 spike copies under `rev5/`)

```python
"""Apply one mutant at a time to the spike, run the pin tests, restore, cmp."""
import filecmp, shutil, subprocess, sys
from pathlib import Path

WT = Path("/home/viperjuice/workspace/worktrees/pmcp-294")
S = Path(sys.argv[0]).parent
MUTANTS = [
    ("M1", "src/pmcp/manifest/loader.py", 'is_valid_package_version(raw) and "+" not in raw:', 'raw and "+" not in raw:', "grammar accepts any string"),
    ("M2", "src/pmcp/manifest/loader.py", "install[platform] = [argv[0], *pinned_install[0]]", "install[platform] = argv", "install argv not pinned"),
    ("M3", "src/pmcp/manifest/loader.py", 'return [*args[:index], f"{name}@{version}", *args[index + 1 :]], name', 'return [*args[:index], f"{arg}@{version}", *args[index + 1 :]], name', "existing tag not replaced"),
    ("M4", "src/pmcp/manifest/loader.py", 'version=_parse_version_pin(name, data.get("version"), "version"),', "version=None,", "servers: version: key ignored"),
    ("M5", "src/pmcp/manifest/loader.py", "                    log_refusal(decision, logger)\n                    continue", "                    log_refusal(decision, logger)\n                    content = overlay_path.read_bytes()", "unapproved project overlay applied"),
    ("M6", "src/pmcp/manifest/loader.py", "    if not _is_npx(server.command):\n        return refuse(", "    if False:\n        return refuse(", "non-npx command accepted"),
    ("M7", "src/pmcp/manifest/loader.py", "        if pinned_install is None or pinned_install[1] != package:", "        if pinned_install is None:", "install may name another package"),
    ("M8", "src/pmcp/tools/handlers.py", "compare_versions(pinned_to, latest_available, package_type)", "compare_versions(latest_available, pinned_to, package_type)", "comparison arguments swapped"),
    ("M9", "src/pmcp/tools/handlers.py", "    if relaxed_by is None:\n        return None\n", "", "relaxer not required for the warning"),
    ("M10", "src/pmcp/tools/handlers.py", "    if pin and _is_exact_pin(package_type, pin):\n        return None\n", "", "pin not consulted for the warning"),
    ("M11", "src/pmcp/tools/handlers.py", "                resolved = connected.get(info.name)\n", "                resolved = manifest_server_to_config(relaxable[info.name])\n", "health judges the manifest, not the connected config"),
    ("M12", "src/pmcp/tools/handlers.py", "        if warning:\n            result.warnings.append(warning)\n        return result", "        return result", "update_server drops the warning"),
    ("M13", "src/pmcp/cli.py", "    elif pinned:\n", "    elif False:\n", "CLI keys the status off ok"),
    ("M14", "src/pmcp/tools/handlers.py", "        self._attach_version_pin_warnings(servers)\n", "", "health never attaches warnings"),
    ("M17", "src/pmcp/tools/handlers.py", '        return "*" not in pin and _parse_version(pin) is not None\n    return False\n', '        return "*" not in pin and _parse_version(pin) is not None\n    return True\n', "P2: any npm selector counts as exact"),
    ("M18", "src/pmcp/tools/handlers.py", "            exact = _is_exact_pin(package_type, pinned_to)", "            exact = True", "P2: update reports a range as pinned"),
    ("M19", "src/pmcp/manifest/loader.py", "name: _materialize_version_pin_soft(entry) for", "name: _materialize_version_pin(entry) for", "P3: materialisation not contained per entry"),
    ("M20", "src/pmcp/tools/handlers.py", "    child_env = sanitized_subprocess_env(resolved.config.env, project_root)", "    child_env = resolved.config.env or {}", "F2: inherited env ignored"),
    ("M21", "src/pmcp/tools/handlers.py", "        if cached is not None and cached[0] == key:", "        if False:", "F3: no cache"),
    ("M22", "src/pmcp/manifest/loader.py", '        stat(Path.home() / ".pmcp" / "manifest.yaml"),\n', "", "F3: fingerprint misses the user overlay"),
    ("M23", "src/pmcp/manifest/loader.py", ' and "+" not in raw:', ":", "N2: build metadata accepted"),
    ("M24", "src/pmcp/cli.py", "    if floating:\n", "    if False:\n", "CLI labels a range [FAILED]"),
    ("M15", "src/pmcp/manifest/loader.py", "        return name, selector\n    return None\n", "        return name, selector\n    return name, selector\n", "P1: any selector accepted (alias/url/git/file/dir/range)"),
    ("M16", "src/pmcp/manifest/loader.py", "        _TAG_WORD_RE.fullmatch(selector)\n", "        False\n", "P1: dist-tag slots refused"),
    ("M25", "src/pmcp/manifest/loader.py", "        and not _NPM_FILE_TYPE_RE.search(selector)\n", "", "B1: tarball SELECTOR accepted as a tag"),
    ("M26", "src/pmcp/manifest/loader.py", '    if not name.startswith("@") and _NPM_FILE_TYPE_RE.search(name):\n        return None\n', "", "B1: bare-tarball / tarball-NAME slot accepted"),
    ("M27", "src/pmcp/manifest/loader.py", "        and not _PARTIAL_VERSION_WORD_RE.fullmatch(selector)\n", "", "N4: x/X/v1.2.x range words accepted as tags"),
    ("M28", "src/pmcp/manifest/loader.py", "        _TAG_WORD_RE.fullmatch(selector)\n", "        _TAG_WORD_RE.match(selector)\n", "N4: match instead of fullmatch (trailing newline)"),
    ("M29", "src/pmcp/tools/handlers.py", "        if not _is_npx(command):\n            return None\n", "        return None\n", "N2: silent when npm identity is disabled"),
    ("M30", "src/pmcp/tools/handlers.py", "        if plain is None:\n            return (", "        if plain is None:\n            return None\n            return (", "N2: silent when the slot cannot be read either"),
    ("M31", "src/pmcp/tools/handlers.py", '            if exact and package_type in ("npm", "cargo") and "+" in pinned_to:', "            if False:", "N3: label keeps +metadata"),
    ("M32", "src/pmcp/manifest/loader.py", "    parts.append(stat(project) if project is not None else None)", "    parts.append(None)", "N1: fingerprint misses the project overlay"),
    ("M33", "src/pmcp/manifest/loader.py", "        parts.append(stat(trust_store_path()))", "        parts.append(None)", "N1: fingerprint misses the trust store"),
]
only = set(sys.argv[1:])
for mid, rel, old, new, desc in MUTANTS:
    if only and mid not in only:
        continue
    path = WT / rel
    spike = S / "rev5" / rel
    text = path.read_text()
    assert text.count(old) == 1, (mid, old)
    path.write_text(text.replace(old, new, 1))
    diff = subprocess.run(["diff", str(spike), str(path)], capture_output=True, text=True).stdout.splitlines()
    header = diff[0] if diff else "NO DIFF"
    try:
        r = subprocess.run(
            ["uv", "run", "pytest", "tests/test_version_pin.py", "-q", "--cov-fail-under=0", "--tb=line", "-p", "no:cacheprovider"],
            cwd=WT, capture_output=True, text=True, timeout=600,
        )
        out = r.stdout.splitlines()
        summary = out[-1] if out else r.stderr[-300:]
        fails = [l for l in out if l.startswith("FAILED")]
        elines = [l for l in out if "Error" in l or l.startswith("E ")][:1]
    finally:
        shutil.copyfile(spike, path)
    same = filecmp.cmp(spike, path, shallow=False)
    print(f"{mid} | {desc} | applied {header} | {summary} | restored={same}")
    for f in fails:
        print(f"    {f}")
    for e in elines:
        print(f"    first: {e[:220]}")
```
