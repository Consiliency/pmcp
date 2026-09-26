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
   refused.
4. The same rewrite is applied to **every** `install[platform]` argv. Each must be npx
   and must name the **same package** as `args`. Otherwise the whole pin is refused, all
   or nothing, because pinning `args` and not `install` "approves X and runs latest".
5. A refusal logs a WARNING, returns the entry unchanged with `version=None`, and so
   leaves the entry **honestly unpinned**. Proposal 2's warning then still fires for it.
   "`version` is set" always means "every spawning argv is pinned". The firecrawl test
   pins that invariant through the gate's own predicate:
   `_config_runs_exactly(pinned, "firecrawl-mcp@3.25.5")`.

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

The warning appears in two places:

- **`gateway.health`.** `ServerHealthInfo.warnings: list[str]` (default `[]`) is filled by
  `_attach_version_pin_warnings(servers)` just before diagnostics. The cost: one
  `load_manifest()` per health call, and a config load plus one resolver query per
  relaxer-declaring server **that is present in the health list**. There is zero config
  I/O when no entry declares a relaxer (today only `firecrawl` does). It is wrapped in
  `try/except`, logged at DEBUG, and never costs health its answer.
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
| `_parse_server_config` | add `version=_parse_version_pin(name, data.get("version"), "version")` | D2 `version:` key |
| `_OverlayDocument` | widen to a 4-tuple `(servers, clis, server_env, server_version)` | D2. The only callers are `_load_overlay_file` and `load_manifest` (grep: no test imports it) |
| `_load_overlay_file`, `_parse_overlay_document` | return 4-tuples; parse `server_version:` (a mapping of non-empty str → `_parse_version_pin`; a non-mapping gets a WARNING); docstring paragraph | D2 |
| `load_manifest` | unpack 4-tuples in both branches; include the server_version count in the "Applying manifest overlay" INFO line; apply `server_version` per source after `server_env` (unknown name → WARNING, skip); **after the overlay loop, outside `if apply_overlays`**, the materialisation pass | D2/D3 |

### `src/pmcp/types.py` (modify)

| entity | action | reason |
|---|---|---|
| `ServerHealthInfo.warnings: list[str] = Field(default_factory=list)` | add | D7. The same `default_factory` shape as `missing_env_vars` |
| `UpdateServerOutput.pinned_version`, `.latest_available: str \| None = None`, `.latest_comparison: Literal["newer","not_newer","incomparable"] \| None = None`, `.warnings: list[str]` | add | D6/D7 |

### `src/pmcp/tools/handlers.py` (modify)

| entity | action | reason |
|---|---|---|
| imports | add `compare_versions` (version_checker) and `credential_requirement` (manifest.loader) | D6/D7 |
| `_unpinned_self_hosted_warning(...)` (module level, after `_detect_effective_version_pin`) | add | D7 |
| `health` | call `self._attach_version_pin_warnings(servers)` before the diagnostics block | D7 |
| `_version_pin_warning(server_name)`, `_attach_version_pin_warnings(servers)` (methods, before `_config_source_paths_by_server`) | add | D7 |
| `update_server` | becomes a wrapper that **keeps the full contract docstring** (plus one #294 paragraph); the body moves to `_update_server_unwarned` with a one-line pointer docstring and is otherwise unchanged except for the pinned branch | D7. `tests/test_tools.py::test_update_server_docstring_states_both_probe_window_env_contracts` reads `GatewayTools.update_server.__doc__` (measured: moving the docstring turns it red) |
| pinned branch of the body (`if pinned_to is not None:`) | add the registry read + `compare_versions`, set the three fields, and add the availability sentence to the message | D6 |

### `src/pmcp/cli.py` (modify)

| entity | action | reason |
|---|---|---|
| `run_update` print loop | `for line in _format_update_result(item): print(line)` | D6 |
| `_format_update_result(item)` (after `run_update`) | add | D6. Pure, so it is unit-tested without a gateway |

### `tests/test_pkgid_panel_fixes.py` (modify): keep the pinned-refusal test offline

| entity | action | reason |
|---|---|---|
| `test_update_server_still_refuses_a_pinned_manifest_server_as_before` | add a `monkeypatch.setattr(handlers_module, "get_package_version", no_registry)` stub returning `(None, "npm")` before the call | The pinned branch now makes a **registry read** (D6). Without the stub, this pre-existing test makes a real HTTP request to registry.npmjs.org for `@shipped/server`. That can take up to the 5 s timeout, and it is swallowed to `None`, so it would never go red: a silent hermeticity regression (the Consiliency/pmcp#235 class). The refusal path was offline before this diff and must stay offline. |

**Measured** with a no-network plugin (Verification step 5b) over every test file that
drives `update_server` or `health` (19 files):

- HEAD: 0 registry lookups.
- The spike without this stub: exactly **1** offender, this test (`1 failed, 857 passed`).
- The spike with the stub: 0.

The pinned-refusal tests in `tests/test_tools.py` (`test_update_server_refuses_pinned_configured_override`,
`..._pinned_docker_tag`) already stub `get_package_version` and need no change. The stub
is included in the production diff above.

### `tests/test_version_pin.py` (create)

37 tests (16 are a parametrized refusal set). Body verbatim below.

### Production diff (spike, verbatim; apply as-is)

The diff is against `9ca081e`, already `ruff format`-clean.

```diff
diff --git a/src/pmcp/cli.py b/src/pmcp/cli.py
index 74ced0d..be928ab 100644
--- a/src/pmcp/cli.py
+++ b/src/pmcp/cli.py
@@ -1005,15 +1005,42 @@ async def run_update(args: argparse.Namespace) -> None:
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
+    its availability line, not ``[FAILED]`` (Consiliency/pmcp#294). Warnings
+    are advisory and printed under the result whatever its status.
+    """
+    server = item.get("server", "unknown")
+    pinned = item.get("pinned_version")
+    if pinned:
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
index 9837e82..2c10828 100644
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
@@ -553,6 +560,105 @@ def _parse_api_key_optional_when(
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
+    if isinstance(raw, str) and is_valid_package_version(raw):
+        return raw
+    logger.warning(
+        f"Ignoring '{field_label}' {raw!r} for server '{name}': a version pin "
+        'must be one exact version such as "3.25.5" -- not a range, a '
+        'dist-tag such as "latest", or a package spec; the server stays '
+        "unpinned"
+    )
+    return None
+
+
+def _pin_npx_args(args: list[str], version: str) -> tuple[list[str], str] | None:
+    """*args* with the npx package slot pinned to *version*, and the name.
+
+    The slot is the provision gate's (`provision_gate._package_slot`): the
+    first argument that is not an allowlisted leading flag. Its NAME is kept
+    and only its version suffix is replaced, so a pin can select a version of
+    the package the entry already runs and never a different package.
+    ``None`` when the slot is missing or is not a package spec (``-p x``,
+    ``github:x/y``, ``./dir``).
+    """
+    # Local import: provision_gate is a consumer of this module's ServerConfig.
+    from pmcp.provision_gate import _NPX_LEADING_FLAGS
+
+    for index, arg in enumerate(args):
+        if arg in _NPX_LEADING_FLAGS:
+            continue
+        try:
+            name, _requested = parse_package_spec(arg)
+        except ValueError:
+            return None
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
+        return refuse("its args name no npm package to pin")
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
+                f"its {platform} install command does not run the package "
+                f"{package!r} its args run"
+            )
+        install[platform] = [argv[0], *pinned_install[0]]
+    return replace(server, args=args, install=install)
+
+
 def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
     """Parse a server config from raw YAML data."""
     install_data = data.get("install", {})
@@ -613,6 +719,7 @@ def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
         status=data.get("status"),
         source=data.get("source"),
         replacement=data.get("replacement"),
+        version=_parse_version_pin(name, data.get("version"), "version"),
     )
 
 
@@ -722,7 +829,10 @@ def _overlay_manifest_paths() -> list[tuple[str, Path]]:
 
 
 _OverlayDocument = tuple[
-    dict[str, ServerConfig], dict[str, CLIAlternative], dict[str, dict[str, str]]
+    dict[str, ServerConfig],
+    dict[str, CLIAlternative],
+    dict[str, dict[str, str]],
+    dict[str, str],
 ]
 
 
@@ -739,7 +849,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
         content = path.read_bytes()
     except OSError as exc:
         logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     return _parse_overlay_document(path, content)
 
@@ -747,7 +857,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
 def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     """Parse overlay bytes, fail-soft. ``path`` is for messages only.
 
-    Returns ``(servers, cli_alternatives, server_env)``. A YAML error or a
+    Returns ``(servers, cli_alternatives, server_env, server_version)``. A YAML error or a
     non-mapping top-level document logs a warning naming the file and returns
     empty dicts. Each entry is parsed in its own try/except so one malformed
     entry is skipped without dropping siblings.
@@ -759,18 +869,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
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
@@ -814,7 +928,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
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
@@ -868,19 +997,31 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
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
@@ -906,6 +1047,23 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
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
+    servers = {name: _materialize_version_pin(entry) for name, entry in servers.items()}
+
     manifest = Manifest(
         version=data.get("version", "1.0"),
         cli_alternatives=cli_alternatives,
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 956c8c8..e854bae 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -102,6 +102,7 @@ from pmcp.manifest.version_checker import (
     _npm_package_arg,
     _npm_tag,
     _uvx_package_arg,
+    compare_versions,
     detect_package_type,
     get_package_version,
 )
@@ -194,6 +195,7 @@ from pmcp.manifest.loader import (
     Manifest,
     ServerConfig,
     credential_lookup_keys,
+    credential_requirement,
     credential_storage_key,
     is_usable_credential_value,
     requires_credential,
@@ -444,6 +446,55 @@ def _detect_effective_version_pin(
     return None
 
 
+def _unpinned_self_hosted_warning(
+    server_name: str,
+    manifest_server: ServerConfig | None,
+    resolved: ResolvedServerConfig,
+) -> str | None:
+    """Warn when a self-hosted backend is served by an unpinned client.
+
+    Consiliency/pmcp#294, proposal 2. Fires only when BOTH hold:
+
+    * the manifest entry's ``api_key_optional_when`` relaxer is active on the
+      environment the child actually receives (``credential_requirement`` with
+      the resolved config's env as ``child_env`` -- the same judgement the
+      credential gates make, Consiliency/pmcp#114/#124); and
+    * the argv that actually spawns (the configured entry when there is one,
+      else the manifest's) is not pinned, by the same
+      ``_detect_effective_version_pin`` gateway.update_server uses, so the two
+      can never disagree about "pinned". ``@latest`` is unpinned.
+
+    A vendor-hosted server (relaxer inactive) never warns: following the
+    vendor's latest client is the intended default there.
+    """
+    if manifest_server is None or not isinstance(resolved.config, LocalMcpServerConfig):
+        return None
+    relaxed_by = credential_requirement(
+        manifest_server, child_env=resolved.config.env
+    ).relaxed_by
+    if relaxed_by is None:
+        return None
+    command, args = resolved.config.command, list(resolved.config.args)
+    env, cwd = resolved.config.env, resolved.config.cwd
+    package_type, package_name = detect_package_type(command, args, env, cwd)
+    if package_type == "unknown" or not package_name:
+        return None
+    if _detect_effective_version_pin(package_type, command, args, env, cwd):
+        return None
+    if package_type == "npm" and resolved.source == "manifest":
+        remedy = (
+            f"pin it with `server_version: {{{server_name}: <version>}}` in "
+            "~/.pmcp/manifest.yaml"
+        )
+    else:
+        remedy = "pin its version in the args of the config that launches it"
+    return (
+        f"'{server_name}' talks to a self-hosted backend ({relaxed_by} is set) "
+        f"but its client {package_type}:{package_name} is unpinned, so a spawn "
+        "or `pmcp update` can move it ahead of the server; " + remedy + "."
+    )
+
+
 # Human-readable label for a ResolvedServerConfig.source, used in messages
 # that need to point an operator at the file a pin (or other override) came
 # from.
@@ -2675,6 +2726,8 @@ class GatewayTools:
                 )
             )
 
+        self._attach_version_pin_warnings(servers)
+
         diagnostics = self._transport_diagnostics.model_copy()
         diagnostics.audit_buffer_size = self._audit_events.maxlen or len(
             self._audit_events
@@ -2697,6 +2750,48 @@ class GatewayTools:
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
+        return _unpinned_self_hosted_warning(server_name, manifest_server, resolved)
+
+    def _attach_version_pin_warnings(self, servers: list[ServerHealthInfo]) -> None:
+        """Add the unpinned-self-hosted warning to each health entry it applies to.
+
+        Advisory only, so it must never cost gateway.health its answer: any
+        failure is logged and the entries are left as they are. Cheap when no
+        manifest entry declares a relaxer (one manifest load, no config read);
+        otherwise one config load for the whole call.
+        """
+        try:
+            manifest = load_manifest()
+            relaxable = {
+                name: server
+                for name, server in manifest.servers.items()
+                if server.api_key_optional_when
+            }
+            wanted = [info for info in servers if info.name in relaxable]
+            if not wanted:
+                return
+            configured = self._load_all_configured_servers()
+            for info in wanted:
+                manifest_server = relaxable[info.name]
+                resolved = configured.get(info.name) or manifest_server_to_config(
+                    manifest_server
+                )
+                warning = _unpinned_self_hosted_warning(
+                    info.name, manifest_server, resolved
+                )
+                if warning:
+                    info.warnings.append(warning)
+        except Exception as exc:  # advisory; never fail health over it
+            logger.debug(f"version-pin warnings skipped: {exc}")
+
     def _config_source_paths_by_server(self) -> dict[str, tuple[str, str]]:
         paths: dict[str, tuple[str, str]] = {}
         for source in load_config_sources(
@@ -5319,7 +5414,26 @@ class GatewayTools:
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
 
@@ -5415,14 +5529,46 @@ class GatewayTools:
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
+            comparison = (
+                compare_versions(pinned_to, latest_available, package_type)
+                if latest_available
+                else None
+            )
+            if comparison == "newer":
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
+                pinned_version=pinned_to,
+                latest_available=latest_available,
+                latest_comparison=comparison,
                 message=(
                     f"'{server_name}' is pinned to '{pinned_to}' in {source_desc} "
-                    f"({command} {' '.join(args)}). gateway.update_server will not "
+                    f"({command} {' '.join(args)}). {availability} "
+                    "gateway.update_server will not "
                     "move a pinned server to the latest version -- edit or remove "
                     "the pin in that config to allow updates."
                 ),
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 215088d..74d0c18 100644
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
@@ -1371,6 +1374,16 @@ class UpdateServerOutput(BaseModel):
     cancelled_request_count: int = 0
     cancelled_task_count: int = 0
     message: str
+    # Set only when the server is pinned and therefore was not moved
+    # (Consiliency/pmcp#294): the pin, the registry's latest, and
+    # `compare_versions(pinned_version, latest_available)` -- None when the
+    # latest could not be fetched. Three-way on purpose (#164).
+    pinned_version: str | None = None
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

from pmcp.config.loader import _merge_manifest_defaults
from pmcp.manifest.loader import Manifest, ServerConfig, load_manifest
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
    gateway = GatewayTools(
        client_manager=cast(Any, _ClientManager(statuses)),
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
  - what `pmcp update` prints for a pinned server (`[PINNED] ...`);
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
#   -> 37 tests collected

# 1. Red on HEAD (before the diff; the test file alone).
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py -q --cov-fail-under=0 --tb=line | tail -1
#   -> 36 failed, 1 passed   (the 1 is test_explicit_config_args_win_over_the_manifest_pin,
#      an inertness guard that is green on HEAD by design)

# 2. Green with the diff.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py -q --cov-fail-under=0 | tail -1
#   -> 37 passed

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
    -q --cov-fail-under=0 | tail -1
#   -> 1562 passed, 19 deselected

# 5. Whole suite: Bash run_in_background, wait for the notification. Do not detach with
#    nohup/disown. Use a lane-unique log path.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/ -q --cov-fail-under=0 -p no:cacheprovider > "$LOGDIR/294-suite.log" 2>&1
#   -> 4108 passed, 3 skipped, 25 deselected in 418.12s (0:06:58)   (revision 2; revision 1 was 1 failed / 4107 passed -- see the mutation-table note;
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
#   -> 858 passed, 1 deselected (measured with the stub). The spike WITHOUT the panel-fixes stub gave
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

| # | mutation (spike line) | applied | result | red for (first `E` line, verbatim) |
|---|---|---|---|---|
| M1 | `_parse_version_pin`: `is_valid_package_version(raw)` → `raw` (grammar accepts any non-empty string) | `576c576` | **13 failed**, 24 passed | every string case of `test_server_version_refuses_anything_but_one_exact_version` (`^3.25.5`, `~`, `3.x`, `*`, `>=`, `latest`, `next`, `v3.25.5`, `"3.25"`, `--registry`, `evil-pkg@1.0.0`, `npm:evil-pkg@1.0.0`, `../../tmp/x`): `AssertionError: assert '^3.25.5' is None`. The 3 non-red cases (YAML float, `true`, `""`) are still refused by the `isinstance(raw, str) and raw` residue of the mutated line, so they are correctly not red. |
| M2 | install argv not rewritten: `install[platform] = [argv[0], *pinned_install[0]]` → `argv` | `658c658` | **3 failed** | firecrawl, scoped and whole-entry tests: `assert {'mac': ['npx...recrawl-mcp']} == {'mac': ['npx...-mcp@3.25.5']}` |
| M3 | slot keeps its old tag: `f"{name}@{version}"` → `f"{arg}@{version}"` | `607c607` | **1 failed** | `test_version_replaces_an_existing_tag_on_a_scoped_package`: `assert ['-y', '@play...latest@1.2.3'] == ['-y', '@play...ht/mcp@1.2.3']` |
| M4 | `servers:` entry's `version:` ignored (`version=None`) | `722c722` | **1 failed** | `test_version_key_on_a_whole_servers_entry_is_materialised` |
| M5 | consent bypass: after `log_refusal`, `continue` → `content = overlay_path.read_bytes()` | `997c997` | **1 failed** | `test_unapproved_project_server_version_contributes_nothing`: `assert '3.25.5' is None` |
| M6 | non-npx command accepted (`if not _is_npx(server.command)` → `if False`) | `635c635` | **1 failed** | `test_version_on_a_uvx_server_is_refused_with_the_escape_hatch`: `assert False`. The install check still refuses uvx, but with a message that does not name the `.mcp.json` escape hatch, and the test pins the message. |
| M7 | install may name another package (`or pinned_install[1] != package` removed) | `653c653` | **1 failed** | `test_version_is_refused_when_an_install_argv_names_another_package`: `assert '1.0.0' is None` |
| M8 | `compare_versions` arguments swapped | `5540c5540` | **1 failed** | `test_update_server_reports_a_newer_version_for_a_pinned_server`: `assert 'not_newer' == 'newer'` |
| M9 | warning no longer requires an active relaxer | `475,476d474` | **1 failed** | `test_health_is_silent_when_the_relaxer_is_not_active`: `assert ["'fc' talks ...nifest.yaml."] == []` |
| M10 | warning no longer consults the pin | `482,483d481` | **2 failed** | `test_health_is_silent_when_the_client_is_pinned`, `test_health_judges_the_configured_entry_not_the_manifest` |
| M11 | health judges the manifest entry, not the configured one | `2784c2784` | **1 failed** | `test_health_judges_the_configured_entry_not_the_manifest` (the ViperJuice/dotfiles#325 shape) |
| M12 | `update_server` wrapper drops the warning | `5429,5430d5428` | **1 failed** | `test_update_server_carries_the_unpinned_self_hosted_warning`: `assert 0 == 1` |
| M13 | CLI keys the status off `ok` (`if pinned:` → `if False:`) | `1023c1023` | **1 failed** | `test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed`: `assert ['[FAILED] fi...long message'] == ['[PINNED] fi...able: 3.26.0']` |
| M14 | health never calls `_attach_version_pin_warnings` | `2729d2728` | **2 failed** | `test_health_warns_when_a_self_hosted_backend_client_is_unpinned`, `test_health_treats_latest_as_unpinned`: `assert 0 == 1` |

**14 of 14 mutants red**, each for the named reason. After each run the file was restored
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

- [ ] `tests/test_version_pin.py` passes: 37 tests, Verification step 2.
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
- **R4: health cost.** It is one `load_manifest()` per `gateway.health` call. When a
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

## Appendix: mutation driver (`mutants.py`, run from the scratch dir with the spike copies under `spike/`)

```python
"""Apply one mutant at a time to the spike, run the pin tests, restore, cmp."""
import filecmp, shutil, subprocess, sys
from pathlib import Path

WT = Path("/home/viperjuice/workspace/worktrees/pmcp-294")
S = Path(sys.argv[0]).parent
MUTANTS = [
    ("M1", "src/pmcp/manifest/loader.py", "if isinstance(raw, str) and is_valid_package_version(raw):", "if isinstance(raw, str) and raw:", "grammar accepts any string"),
    ("M2", "src/pmcp/manifest/loader.py", "install[platform] = [argv[0], *pinned_install[0]]", "install[platform] = argv", "install argv not pinned"),
    ("M3", "src/pmcp/manifest/loader.py", 'return [*args[:index], f"{name}@{version}", *args[index + 1 :]], name', 'return [*args[:index], f"{arg}@{version}", *args[index + 1 :]], name', "existing tag not replaced"),
    ("M4", "src/pmcp/manifest/loader.py", 'version=_parse_version_pin(name, data.get("version"), "version"),', "version=None,", "servers: version: key ignored"),
    ("M5", "src/pmcp/manifest/loader.py", "                    log_refusal(decision, logger)\n                    continue", "                    log_refusal(decision, logger)\n                    content = overlay_path.read_bytes()", "unapproved project overlay applied"),
    ("M6", "src/pmcp/manifest/loader.py", "    if not _is_npx(server.command):\n        return refuse(", "    if False:\n        return refuse(", "non-npx command accepted"),
    ("M7", "src/pmcp/manifest/loader.py", "        if pinned_install is None or pinned_install[1] != package:", "        if pinned_install is None:", "install may name another package"),
    ("M8", "src/pmcp/tools/handlers.py", "compare_versions(pinned_to, latest_available, package_type)", "compare_versions(latest_available, pinned_to, package_type)", "comparison arguments swapped"),
    ("M9", "src/pmcp/tools/handlers.py", "    if relaxed_by is None:\n        return None\n", "", "relaxer not required for the warning"),
    ("M10", "src/pmcp/tools/handlers.py", "    if _detect_effective_version_pin(package_type, command, args, env, cwd):\n        return None\n", "", "pin not consulted for the warning"),
    ("M11", "src/pmcp/tools/handlers.py", "resolved = configured.get(info.name) or manifest_server_to_config(", "resolved = manifest_server_to_config(", "health judges the manifest, not the configured entry"),
    ("M12", "src/pmcp/tools/handlers.py", "        if warning:\n            result.warnings.append(warning)\n        return result", "        return result", "update_server drops the warning"),
    ("M13", "src/pmcp/cli.py", "    if pinned:\n", "    if False:\n", "CLI keys the status off ok"),
    ("M14", "src/pmcp/tools/handlers.py", "        self._attach_version_pin_warnings(servers)\n", "", "health never attaches warnings"),
]
only = set(sys.argv[1:])
for mid, rel, old, new, desc in MUTANTS:
    if only and mid not in only:
        continue
    path = WT / rel
    spike = S / "spike" / rel
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
    for f in fails[:6]:
        print(f"    {f}")
    for e in elines:
        print(f"    first: {e[:220]}")
```
