---
phase_loop_plan_version: 1
phase: P1
roadmap: specs/phase-plans-v11.md
roadmap_sha256: 2f03c6f3c01d903e55b87bdbe4ca8b9b25fcbb318a48a30161d35cd6b76b3be0
---

# P1: Release-guard hardening

## Context

`pmcp`'s `.github/workflows/test.yml` currently declares four jobs — `test` (3.10/3.11/3.12 matrix), `install-smoke`, `lint`, `typecheck` — and two triggers, `push: [main]` and `pull_request: [main]`. Three of the four guards this phase requires are missing: there is no `min-version-smoke`, no `schedule:`/`workflow_dispatch:`, and no test pinning `pmcp.__version__` against distribution metadata. `install-smoke` (added in #111) already exists and already works.

`pangram-mcp`'s `.github/workflows/test.yml` runs all four. Its `min-version-smoke` job parses the floor out of `pyproject.toml` with `re.search(r'"mcp>=([0-9.]+),<', text)`, writes it to `$GITHUB_OUTPUT`, and passes it into the install step through `env:`. Its drift guard is `tests/test_server.py::test_dunder_version_matches_package_metadata`. Both port to `pmcp` with only mechanical substitutions (`actions/checkout@v7` + `astral-sh/setup-uv@v7` + `uv python install 3.12`, which is `pmcp`'s house style; `pmcp.client.manager, pmcp.server, pmcp.config.loader` as the startup-import set, which is exactly what `install-smoke` already imports).

**The declared floor is false, and this was verified by installing and booting, not by reading source.** `pyproject.toml` declares `mcp>=1.0.0,<2.0.0`. Building the wheel and installing it pinned at each candidate produces:

| pinned `mcp` | `import pmcp.client.manager, pmcp.server, pmcp.config.loader` | boots and listens |
|---|---|---|
| 1.0.0 | `ModuleNotFoundError: No module named 'mcp.client.streamable_http'` | n/a — never imports |
| 1.6.0 | same failure | n/a |
| 1.7.0 | same failure | n/a |
| 1.7.1 | same failure | n/a |
| 1.8.0 | **OK** (also `import pmcp.transport.http` OK, `pmcp --version` OK) | **yes — boots, listens, and serves a real downstream tool call** |
| 1.9.0 | OK | not separately booted |
| 1.10.0 | OK | not separately booted |

`client/manager.py:22` imports `streamablehttp_client` from `mcp.client.streamable_http`, a module that first exists in `mcp` 1.8.0.

**Import success is not sufficient evidence for a floor — and neither is `/health`.** Cross-Cutting Principle 1 is explicit that 2276 tests passed while the gateway could not boot: `import pmcp.server` never instantiates `Server`, never registers the six handlers, and never listens — which is precisely how the `mcp` 2.x break survived every import check and died at startup. But `/health` is barely stronger: `transport/http.py:435-436` returns `"ok": True` as a **hardcoded literal** in `handle_health`, next to `__version__` and the static diagnostics blob. It is never computed from session state, downstream connectivity, or tool inventory, so a green `/health` proves only that Starlette is answering. It cannot detect an `mcp` failure in session initialization, tool discovery, or invocation.

Principle 1's second half — "a real downstream server serves a real tool call through it" — is what actually establishes a *functional* floor, so the floor was proven that way. At `mcp==1.8.0` with the `http` extra, against a throwaway FastMCP stdio fixture allowlisted by policy: the gateway reached `LISTEN` in ~1s; an MCP client over Streamable HTTP at `/mcp` initialized (negotiating `2024-11-05`); `gateway.connect_server` brought the fixture online; and `gateway.invoke` on `p1probe::p1_echo` returned `{"type": "text", "text": "p1-floor-ok:floor"}` with `isError: false`. That round trip — client → gateway → stdio downstream → back — is the evidence that 1.8.0 is the floor. `/health` and the boot log are retained as cheap preconditions, not as the proof.

So `min-version-smoke` lands **red on `main`** unless the floor is corrected in the same phase. This is the guard doing its job — it is the identical defect `pangram-mcp` shipped (`mcp>=1.2.0` declared, 1.14.0 actually required) and the reason the job exists. See `## Execution Notes > Roadmap deviation` for why correcting the floor is in scope despite P1's stated non-goal.

**Booting `pmcp` for verification is not safely isolated by `--config`, and not by `HOME` either — this was discovered while producing the evidence above.** A boot with `--config <empty {"mcpServers": {}}> --project <tmp>` still logged `0/107 servers online`: `config/loader.py` layers `load_manifest()` over the config file, and `manifest/loader.py:601-602` falls back to the **manifest shipped inside the installed package**, then overlays `~/.pmcp/manifest.yaml` (`loader.py:495`), the project's `.pmcp/manifest.yaml`, and `$PMCP_MANIFEST_PATH` (`loader.py:503`). `PMCP_MANIFEST_PATH` only *adds* an overlay; nothing suppresses the shipped catalog, so redirecting `HOME` removes the user overlay and still leaves ~106 shipped servers resolved. `server.py:604` then calls `_kill_orphan_processes` unconditionally over every resolved config, and that function (`server.py:683-700`) scans `/proc` and sends **SIGKILL** to each process whose `(command basename, args tuple)` matches. What does bound it is `--policy`: `is_server_allowed` threads into `resolve_startup_configs` (`server.py:569`) and only survivors reach the kill call. Adding `--policy` with `servers: {allowlist: ["p1probe"]}` took the same boot from `0/107 servers online` to `0/1`. See `## Execution Notes > Boot-check isolation` for the full rule set.

`uv.lock` is committed in this repo (it is gitignored in `pangram-mcp`), and it embeds the project's own constraint at `uv.lock:1066` as `{ name = "mcp", specifier = ">=1.0.0,<2.0.0" }`. Editing `pyproject.toml` therefore dirties `uv.lock`; both belong to the same lane.

`.claude/docs-catalog.json` exists and is currently an empty JSON array. `actionlint` is installed at `/usr/local/bin/actionlint`. `gh` is authenticated as `ViperJuice`. Dependabot PR #112 currently shows exactly one failing check — `install-smoke` — against 8 passing ones (`build`, `lint`, `notify-worker`, `test (3.10/3.11/3.12)`, `typecheck`), which is the baseline EC-P1-4 must still hold after this phase.

## Interface Freeze Gates

- [ ] IF-0-P1-1 — The four-guard CI contract for `pmcp`, frozen as: (a) job `install-smoke` in `.github/workflows/test.yml`, **byte-for-byte unchanged from `main`** — the job's own line range, compared as raw text, not as a parsed YAML object (see V4); (b) job `min-version-smoke` in the same file, parsing the floor from `pyproject.toml` with `re.search(r'"mcp>=([0-9.]+),<', text)`, exporting it as step output `floor`, consuming it **only** through `env: FLOOR: ${{ steps.floor.outputs.floor }}`, installing `dist/*.whl` plus `mcp==${FLOOR}`, importing `pmcp.client.manager, pmcp.server, pmcp.config.loader`, **and serving a real downstream tool call** — booting on a spare port under the isolation rules below, then driving `gateway.connect_server` + `gateway.invoke` against a throwaway stdio fixture and asserting the round-tripped payload; (c) triggers `schedule: - cron: "0 8 * * 1"` and `workflow_dispatch:` on that workflow; (d) test `tests/test_package_metadata.py::test_dunder_version_matches_distribution_metadata` asserting `pmcp.__version__ == importlib.metadata.version("pmcp")`. P2 and PG both rely on (a)+(b) to detect a bad constraint.

  The downstream-call step in (b) is a deliberate **tightening** of EC-P1-1, which says only "imports the gateway's startup modules". Cross-Cutting Principle 1 forbids treating imports as acceptance for a gateway phase and requires a real downstream tool call; the `mcp` 2.x failure this whole roadmap exists to fix was invisible to imports, and would also have been invisible to `/health`. Strengthening a guard is always in scope; weakening one never is.

## Lane Index & Dependencies

SL-1 — CI release guards (workflow + honest floor)
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes

SL-2 — Version drift test
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes

SL-3 — Documentation & spec reconciliation (author-facing alias: SL-docs)
  Depends on: SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-1 — CI release guards (workflow + honest floor)

- **Scope**: Add the `min-version-smoke` job and the `schedule:`/`workflow_dispatch:` triggers to `.github/workflows/test.yml`, and correct the `mcp` lower bound to the version that installation proves is required, leaving `install-smoke` untouched.
- **Owned files**: `.github/workflows/test.yml`, `.github/probe/p1_probe_server.py`, `.github/probe/p1_probe_client.py`, `pyproject.toml`, `uv.lock`
- **Interfaces provided**: IF-0-P1-1 parts (a), (b), (c); workflow job name `min-version-smoke`; step output `floor`; the corrected `mcp>=1.8.0,<2.0.0` specifier
- **Interfaces consumed**: (none)
- **Parallel-safe**: yes

Implementation contract (port from `pangram-mcp`, do not re-derive):

1. Triggers — insert under the existing `on:` block, after `pull_request:`, keeping `push`/`pull_request` exactly as they are:

   ```yaml
     # 1.21.0 could have broken with ZERO commits to this repo: mcp 2.0.0 was
     # published after the last CI run and nothing re-ran to notice. Push/PR
     # triggers cannot catch a dependency that moves underneath a released
     # package, so run weekly against the released artifact.
     schedule:
       - cron: "0 8 * * 1"
     workflow_dispatch:
   ```

   `0 8 * * 1` deliberately differs from `pangram-mcp`'s `0 7 * * 1` so the two first-party repos do not contend for runners in the same minute.

2. `min-version-smoke` — append as a new job after `install-smoke`, using `pmcp`'s house action pins (`actions/checkout@v7`, `astral-sh/setup-uv@v7` with `enable-cache: true`, then `uv python install 3.12`), not `pangram-mcp`'s. The floor MUST reach the shell through `env:`; it must never be interpolated into a `run:` body.

   ```yaml
     min-version-smoke:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v7
         - name: Install uv
           uses: astral-sh/setup-uv@v7
           with:
             enable-cache: true
         - name: Set up Python 3.12
           run: uv python install 3.12

         - name: Resolve the declared minimum mcp from pyproject
           id: floor
           run: |
             FLOOR=$(python3 <<'PY'
             import pathlib
             import re

             text = pathlib.Path("pyproject.toml").read_text()
             match = re.search(r'"mcp>=([0-9.]+),<', text)
             assert match, "could not parse the mcp lower bound from pyproject.toml"
             print(match.group(1))
             PY
             )
             echo "floor=$FLOOR" >> "$GITHUB_OUTPUT"
             echo "declared mcp floor: $FLOOR"

         - name: Install at the floor and import the startup modules
           env:
             FLOOR: ${{ steps.floor.outputs.floor }}
           run: |
             uv build --wheel --out-dir dist
             uv venv /tmp/floor
             VIRTUAL_ENV=/tmp/floor uv pip install dist/*.whl "mcp==${FLOOR}"
             VIRTUAL_ENV=/tmp/floor uv pip list
             /tmp/floor/bin/pmcp --version
             /tmp/floor/bin/python -c "import pmcp.client.manager, pmcp.server, pmcp.config.loader"

         # Imports are not acceptance for a gateway, and neither is /health —
         # transport/http.py:435-436 returns "ok": True as a hardcoded literal.
         # Only a real downstream tool call exercises session init, discovery,
         # and invocation, which is where an mcp break actually lands.
         - name: Serve a real downstream tool call at the floor
           env:
             FLOOR: ${{ steps.floor.outputs.floor }}
           run: |
             VIRTUAL_ENV=/tmp/floor uv pip install "$(ls dist/*.whl)[http]" "mcp==${FLOOR}"
             mkdir -p /tmp/floorhome/lock /tmp/floorhome/xdg
             cp .github/probe/p1_probe_server.py .github/probe/p1_probe_client.py /tmp/floorhome/
             printf '{"mcpServers": {"p1probe": {"command": "/tmp/floor/bin/python", "args": ["/tmp/floorhome/p1_probe_server.py"]}}}\n' > /tmp/floorhome/mcp.json
             printf 'servers:\n  allowlist: ["p1probe"]\n' > /tmp/floorhome/policy.yaml
             HOME=/tmp/floorhome XDG_CONFIG_HOME=/tmp/floorhome/xdg /tmp/floor/bin/pmcp \
               --transport http --host 127.0.0.1 --port 3399 \
               --config /tmp/floorhome/mcp.json \
               --project /tmp/floorhome \
               --policy /tmp/floorhome/policy.yaml \
               --lock-dir /tmp/floorhome/lock -l info > /tmp/floorboot.log 2>&1 &
             PROBE_PID=$!
             trap 'kill "$PROBE_PID" 2>/dev/null' EXIT
             for _ in $(seq 1 30); do
               curl -sf --max-time 2 http://127.0.0.1:3399/health > /dev/null && break
               sleep 1
             done
             cat /tmp/floorboot.log
             # Assert the RESOLVED set is exactly the one fixture. Do NOT tripwire on
             # "a count in the hundreds" — the shipped manifest always contributes 106,
             # so a correctly isolated run legitimately reports skipped=106 and
             # policy_denied=106. Hundreds in those buckets is the CORRECT output; what
             # proves isolation is that lazy+eager (the denominator here) is exactly 1.
             grep -E 'Gateway initialized: [0-9]+/1 servers online' /tmp/floorboot.log
             /tmp/floor/bin/python /tmp/floorhome/p1_probe_client.py http://127.0.0.1:3399/mcp
             kill "$PROBE_PID" 2>/dev/null || true
   ```

   `HOME` and `XDG_CONFIG_HOME` are redirected because the runner's `$HOME` holds `~/.pmcp/manifest.yaml` and the default singleton lock; `--lock-dir` and `--policy` are passed explicitly as well. The venv binary is invoked directly — `uv run` under a redirected `HOME` chases an absent cache. Port `3399` is arbitrary but must never be `3344`.

3. Probe fixtures — SL-1 adds two small files under `.github/probe/`, which keeps them out of `tests/` (Lane B's territory) and out of the shipped wheel:

   - `.github/probe/p1_probe_server.py` — a FastMCP stdio server exposing exactly one tool, `p1_echo(text: str) -> str`, returning `f"p1-floor-ok:{text}"`. Its command/args pair (`python`, `("/tmp/floorhome/p1_probe_server.py",)`) is the orphan-kill fingerprint and cannot collide with a real process.
   - `.github/probe/p1_probe_client.py` — connects to the gateway's `/mcp` with `streamablehttp_client` + `ClientSession`, initializes, calls `gateway.connect_server` for `p1probe`, then `gateway.invoke` on `p1probe::p1_echo`, and asserts `p1-floor-ok:` appears in the returned text content. Both were written and run end-to-end at `mcp==1.8.0` while producing this plan's evidence.

   The heredoc form replaces `pangram-mcp`'s `python3 -c "…"` with escaped inner quotes; the regex and the `assert` are preserved verbatim. Both forms were run against this repo's `pyproject.toml` and both print `1.0.0` today.

4. Floor correction — in `pyproject.toml`, change `"mcp>=1.0.0,<2.0.0"` to `"mcp>=1.8.0,<2.0.0"` and extend the existing comment block above it to record that 1.8.0 is where `mcp.client.streamable_http` first exists and that the bound was set by installing (Cross-Cutting Principle 5). Do **not** touch the upper bound — the cap raise is P2's. Then run `uv lock` so `uv.lock`'s `[package.metadata] requires-dist` entry for `mcp` matches; do not hand-edit `uv.lock`.

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `.github/workflows/test.yml` | negative control: the guard must reject a false floor | `uv build --wheel --out-dir /tmp/p1 && uv venv /tmp/p1env && VIRTUAL_ENV=/tmp/p1env uv pip install /tmp/p1/*.whl "mcp==1.7.1" && ! /tmp/p1env/bin/python -c "import pmcp.client.manager, pmcp.server, pmcp.config.loader"` |
| SL-1.2 | impl | SL-1.1 | `.github/workflows/test.yml`, `.github/probe/p1_probe_server.py`, `.github/probe/p1_probe_client.py` | — | `actionlint .github/workflows/test.yml` |
| SL-1.3 | impl | SL-1.1 | `pyproject.toml`, `uv.lock` | — | `uv lock && git diff --stat -- uv.lock` |
| SL-1.4 | verify | SL-1.3 | `.github/workflows/test.yml`, `.github/probe/*.py`, `pyproject.toml`, `uv.lock` | IF-0-P1-1 (a)(b)(c) | see `## Verification` steps V0–V5; V2-N1/V2-N2 (both negative controls), V2b+V2c (bounded boot and the real downstream tool call) and V4/V4b (`install-smoke` byte-freeze and real unpinned execution) are all mandatory, not optional |

### SL-2 — Version drift test

- **Scope**: Add a test that fails when `pmcp.__version__` and the installed distribution's metadata version disagree.
- **Owned files**: `tests/test_package_metadata.py`
- **Interfaces provided**: IF-0-P1-1 part (d); `tests/test_package_metadata.py::test_dunder_version_matches_distribution_metadata`
- **Interfaces consumed**: (none)
- **Parallel-safe**: yes

Implementation contract:

- New file, not an addition to an existing test module — `tests/test_transport_http.py:481` already owns a semver-shape assertion on `__version__` and must not be touched by this lane.
- Body ports `pangram-mcp/tests/test_server.py::test_dunder_version_matches_package_metadata`, substituting `pmcp` / `"pmcp"`. Keep the failure history in the comment: a release that bumps `pyproject.toml` without `src/pmcp/__init__.py` (or the reverse) makes everything reading `pmcp.__version__` — including `/health` and the `pmcp/{version}` User-Agent — report the wrong release.
- Assert equality directly. Do **not** wrap the assertion in a `try/except` that downgrades a mismatch to a skip; that is weakening the guard. A bare `importlib.metadata.PackageNotFoundError` means the distribution is not installed at all, which is an environment fault rather than drift — the test may `pytest.fail` on it with an explanatory message, but must never pass silently.
- Verified viable in this repo: after `uv sync --all-extras`, `uv run python -c "import importlib.metadata as md, pmcp; print(pmcp.__version__, md.version('pmcp'))"` prints `1.21.1 1.21.1`.
- The file must satisfy `ruff format --check` and `ruff check` over `tests/`, and `mypy` is scoped to `src/pmcp` so it does not see this file.

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/test_package_metadata.py` | `test_dunder_version_matches_distribution_metadata` | `uv run pytest tests/test_package_metadata.py -q` |
| SL-2.2 | impl | SL-2.1 | `tests/test_package_metadata.py` | — | `uv run ruff format --check tests/ && uv run ruff check tests/` |
| SL-2.3 | verify | SL-2.2 | `tests/test_package_metadata.py` | IF-0-P1-1 (d) | `uv run pytest tests/ -q` |

### SL-docs — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, add the `### Changed` CHANGELOG entry EC-P1-5 requires, and append the post-execution amendments recording this phase's roadmap deviation and its post-merge operational evidence.
- **Owned files**: `CHANGELOG.md`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SPEC_COMPLIANCE.md`, `.claude/docs-catalog.json`, `specs/phase-plans-v11.md`, `plans/phase-plan-v11-P1.md`
- **Interfaces provided**: (none)
- **Interfaces consumed**: from SL-1 — the corrected `mcp>=1.8.0,<2.0.0` specifier in `pyproject.toml`, the job name `min-version-smoke`, the cron `0 8 * * 1`; from SL-2 — the test id `tests/test_package_metadata.py::test_dunder_version_matches_distribution_metadata`
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-1, SL-2

**Lane completion is SL-docs.1 through SL-docs.4 only.** SL-docs.5 is a post-merge closeout appended to the same lane for ownership of the files it touches, but it is **deliberately outside the DAG**: it gates nothing and blocks nothing. Binding it into the lane would make the plan circular — SL-docs must complete before SL-1 merges, yet SL-docs.5's evidence (a `workflow_dispatch` run on the default branch, and #112 re-run against post-P1 `main`) can only exist afterwards. The pre-merge amendment lives in SL-docs.3; the post-merge evidence lives in SL-docs.5.

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | — | `.claude/docs-catalog.json` | Rescan with `scaffold_docs_catalog.py --rescan`. The repo has no `.claude/skills/_shared/`, so resolve the helper from the installed skills root; if it is not resolvable, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and enumerate the root docs by hand. The catalog is currently `[]`, so this run populates it. |
| SL-docs.2 | docs | SL-docs.1 | `CHANGELOG.md`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SPEC_COMPLIANCE.md` | Add under `## [Unreleased]` a `### Changed` entry covering: the `min-version-smoke` job, the weekly `schedule:` + `workflow_dispatch:` triggers, the `__version__`-vs-metadata drift test, and the floor correction `mcp>=1.0.0` → `mcp>=1.8.0` with the reason (1.0.0 was never installable; `mcp.client.streamable_http` first exists in 1.8.0). Record any catalog file intentionally skipped in the commit message. |
| SL-docs.3 | docs | SL-docs.2 | `specs/phase-plans-v11.md`, `plans/phase-plan-v11-P1.md` | **Pre-merge, gates the merge.** Append `### Post-execution amendments` to the Phase 1 section recording that P1's "Non-goals: changing any dependency bound" was narrowed to the ceiling only, with the floor evidence (import bisect, bounded boot, and the downstream round trip) attached. Amend this plan file with the same fact. **Not deferrable**: it must be in the same commit as SL-1's `pyproject.toml` change. A floor correction that reaches `main` without the amendment leaves the roadmap asserting a non-goal the code has already violated. This task records only evidence that exists before the merge. |
| SL-docs.4 | verify | SL-docs.3 | — | `uv run ruff format --check src/ tests/`; no markdown linter is configured in this repo, so the doc-lint step is a no-op and must be recorded as such. |
| SL-docs.5 | docs | (post-merge; see below) | `specs/phase-plans-v11.md`, `plans/phase-plan-v11-P1.md` | **Post-merge closeout — does NOT gate the merge and is not a dependency of any lane.** Once the phase is on `main`, run the two post-merge blocks in `## Verification` and append their results to the same `### Post-execution amendments` subsection: the correlated `workflow_dispatch` run URL, conclusion, and `headSha` for EC-P1-2; and #112's refreshed head SHA with the conclusion of the run against it for EC-P1-4. |


## Execution Notes

- **Roadmap deviation — P1's non-goal is narrowed to the ceiling.** The roadmap says P1 changes no dependency bound. Holding that literally makes the phase unmergeable: `min-version-smoke` at the declared `mcp>=1.0.0` fails (verified above), which contradicts EC-P1-5 (full suite green) and would leave a permanently red job on `main` — which in turn blocks P2, since P2's whole premise is that these guards are trustworthy. Cross-Cutting Principle 2 forbids weakening the guard to make the phase pass, and Principles 4 and 5 require both bounds be declared and set by installing. The only reading that satisfies all three is: the non-goal reserves the **ceiling** raise for P2, while a floor that installation proves is false is corrected here. SL-1 owns that correction; SL-docs.3 records the narrowing as a roadmap amendment **in the same merge** — the correction and the amendment ship together or neither ships. **P2 is unaffected** — it still raises the cap and re-derives the floor for `mcp` 2.x.
- **Boot-check isolation — `--policy` is the mechanism that bounds the kill set; `--config` and `HOME` are not.** `server.py:604` calls `_kill_orphan_processes` unconditionally at startup, and it SIGKILLs `/proc` matches for every *resolved* stdio server. Two mitigations that look sufficient are not:
  - `--config` alone: config loading layers `load_manifest()` over the file, and the manifest starts from the package-shipped `manifest.yaml` (`manifest/loader.py:601-602`). A boot with an empty `{"mcpServers": {}}` config logged **`0/107 servers online`**.
  - `HOME` alone: it removes the user overlay at `manifest/loader.py:495` but not the shipped catalog, so the kill set is still ~106 lazy servers.

  What actually bounds it is the policy layer: `is_server_allowed` is threaded into `resolve_startup_configs` (`server.py:569`) and only survivors reach `_kill_orphan_processes` (`server.py:604`). Verified — adding `--policy` with `servers: {allowlist: ["p1probe"]}` took the same boot from `0/107 servers online` to **`0/1 servers online`**. Every step in this plan that starts `pmcp` must therefore pass **all** of:
  1. **`--policy <tmp>/policy.yaml`** allowlisting only the throwaway fixture. This is the load-bearing one.
  2. **Spare port only.** `3388` locally, `3399` in CI. Never `3344`.
  3. **`--lock-dir <tmp>/lock`.** `acquire_singleton_lock(None)` (`server.py:775-784`) otherwise takes the global lock at `~/.pmcp`, which either fails against the live gateway or steals its lock.
  4. **`HOME` and `XDG_CONFIG_HOME` both redirected** to throwaway directories, plus `--config` and `--project`.
  5. **Invoke the venv binary directly** (`/tmp/floor/bin/pmcp`), never `uv run` — a redirected `HOME` sends `uv` after an absent cache.
  6. **A fixture that cannot collide on either fingerprint key.** `server.py:700` keys on `(Path(command).name, tuple(args))` and **both** must match. The fixture is `<venv>/bin/python` with a single absolute path to a throwaway script under the temp dir, so no unrelated process can match.
  7. **Assert the live gateway before and after.** `ss -ltnp | grep ':3344 '` for the pid and `curl -s http://127.0.0.1:3344/health` for liveness, plus an unchanged child set for that pid. The isolation assertion is **`lazy` + `eager` == exactly 1** (the fixture), which is the denominator in the `Gateway initialized: <n>/<total> servers online` line. Do **not** tripwire on "counts in the hundreds": the package-shipped manifest always contributes 106 entries, so a correctly isolated run reports `eager=0, lazy=1, skipped=106, policy_denied=106` — hundreds in the skipped/denied buckets is the *correct* output of a good run, and aborting on it would reject exactly the runs that are safe.
- **Cross-phase file collision — the roadmap's "roots run fully parallel" is false at file level.** The DAG says P1, PG, P5, and P6CLEAN are concurrent roots, and that is true of their *lanes*, but not of their *files*. Within this repo, P1 (SL-docs), **P5**, and **P6CLEAN** all write `CHANGELOG.md` and `.claude/docs-catalog.json`. Neither file is lane-partitionable — `CHANGELOG.md` has one `## [Unreleased]` block and the catalog is a single JSON array. Execution must not assume zero contention: **`CHANGELOG.md` and `.claude/docs-catalog.json` writes serialize on merge order.** Whichever phase merges second rebases and re-applies its entry rather than resolving a content conflict in place, and the catalog rescan is re-run after the rebase so it reflects both phases' files. The orchestrator should expect an add/add or content conflict on exactly these two paths across phases and should not treat it as a stale-base signal. (PG is exempt — it is a different repository.)
- **Post-merge operational evidence.** EC-P1-2 and EC-P1-4 cannot be proven from a lane worktree or a PR branch. `workflow_dispatch` is only dispatchable once the workflow exists on the default branch, and #112's check results are only meaningful when re-run against post-P1 `main` (GitHub recomputes the `pull_request` merge ref per run, so a re-run picks up `min-version-smoke`; existing check results predating the P1 merge prove nothing about it). Evidence artifact: the amendment written by **SL-docs.5**, a post-merge closeout task that is deliberately outside the lane DAG — it gates nothing, because requiring it pre-merge would make the plan circular (SL-docs must finish before SL-1 merges, but this evidence only exists after). It carries the correlated `workflow_dispatch` run URL/conclusion/`headSha` and #112's refreshed head SHA. Both post-merge blocks (V6, V7) must correlate on a SHA — an uncorrelated `gh run list --limit 1` or a bare `gh pr checks 112` reads someone else's run or a stale check set. Do not merge #112 — the roadmap keeps it open as a standing canary. Note that after P1, #112 is expected to fail `install-smoke` **only**: it raises the cap but not the floor, so `min-version-smoke` still installs `mcp==1.8.0` and passes.
- **Single-writer files**: `.github/workflows/test.yml` — SL-1 only. `pyproject.toml` and `uv.lock` — SL-1 only. `CHANGELOG.md` — SL-docs only; SL-1 and SL-2 must not add entries, or the two lanes collide on the `## [Unreleased]` block.
- **`install-smoke` is frozen at the byte level, and must also still actually run.** Two separate obligations, both mandatory:
  - **Freeze** — V4 extracts the job's own line range as **raw text** from `git show main:.github/workflows/test.yml` and from the working tree and compares bytes. An earlier draft of this plan compared `yaml.safe_load(...)["jobs"]["install-smoke"]` objects, which is semantic equality: comments, indentation, key order, and quoting could all change while the check passed. That is not the freeze IF-0-P1-1 claims and it has been replaced. The extractor stops at the next job key and strips the trailing blank/comment run that introduces the following job, so inserting a commented `min-version-smoke` after `install-smoke` does not perturb it (verified by simulating the insertion).
  - **Execution** — V4b runs the real job locally: fresh venv, `uv pip install dist/*.whl` with **no version pin and no lockfile**, then the entry-point smoke. V1 pins `mcp==1.8.0` and therefore proves the *floor*; it says nothing about whether the unpinned resolve still works. Only V4b covers the ceiling.
  - If a change makes `install-smoke` fail, the change is wrong.
- **Workflow-injection hygiene.** The parsed floor is untrusted input to the shell. It reaches `run:` only via `env: FLOOR: ${{ steps.floor.outputs.floor }}` and is referenced as `"${FLOOR}"`. A reviewer seeing `${{ steps.floor.outputs.floor }}` inside a `run:` body must reject the change.
- **`uv sync --all-extras`, never bare `uv sync`.** Bare `uv sync` prunes `pytest`, after which `uv run pytest` silently falls through to a system `pytest` that cannot import `pmcp` and reports a misleading pass/collection error.
- **`uv.lock` is committed here.** That is why `test`, `lint`, and `typecheck` are lock-pinned and can never see a constraint problem; only `install-smoke` and `min-version-smoke` resolve fresh. It is also why SL-1 must run `uv lock` after editing `pyproject.toml`.
- **PyYAML parses `on:` as the boolean `True`.** Any verification script inspecting the triggers must look up `cfg.get("on", cfg.get(True))`, not `cfg["on"]`. Confirmed against the current file: `list(cfg.keys())` is `['name', True, 'concurrency', 'jobs']`.
- **Never start anything on `127.0.0.1:3344`.** The gateway runs there as the operator's live systemd service. Several verification steps *do* bind a port — V2b and V2c bind `3388`, and the CI boot/probe steps bind `3399` — which is exactly why the isolation rules below are mandatory rather than advisory. No step may bind `3344`.
- **`pgrep -f "pmcp …"` self-matches the invoking shell.** A liveness check written that way returns the pid of the `zsh -c` wrapper running it, and the "before/after child set" comparison it feeds is then meaningless — this happened while producing this plan's evidence. Get the live gateway's pid from the listening socket instead: `ss -ltnp | grep ':3344 '`, or use a bracketed pattern (`pgrep -af "[p]mcp"`).
- **Known destructive changes**: none — every lane is purely additive except SL-1's two-character edit to an existing version specifier in `pyproject.toml` and the `uv lock` regeneration that follows it.
- **Expected add/add conflicts**: none — there is no preamble lane and no lane stubs a file another lane replaces.
- **SL-0 re-exports**: not applicable — this phase has no preamble lane and touches no `__init__.py`.
- **Worktree naming**: `claude-execute-phase` allocates unique worktree names via `scripts/allocate_worktree_name.sh`. This plan does not spell out lane worktree paths. On this host, worktrees belong under `/mnt/workspace/worktrees/`.
- **Stale-base guidance** (copy verbatim): Lane teammates working in isolated worktrees do not see sibling-lane merges automatically. If a lane finds its worktree base is pre-SL-1, it MUST stop and report rather than committing — the orchestrator will re-spawn or rebase. Silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree produces commits that destroy peer-lane work on `--no-ff` merge.

## Execution Policy
- execute: effort=low, reason=mechanical port of two already-proven CI guards
- repair: effort=low
- SL-1: effort=medium, reason=floor correction contradicts a stated non-goal and install-smoke must stay byte-identical
- SL-2: effort=minimal, reason=single ported assertion in a new test file
- SL-3: effort=low

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `roadmap_amendment`
- target surfaces: `.github/workflows/test.yml`, `pyproject.toml`, `uv.lock`, `tests/test_package_metadata.py`, `specs/phase-plans-v11.md`
- evidence paths: `plans/phase-plan-v11-P1.md`, `specs/phase-plans-v11.md`
- redaction posture: `metadata_only`
- downstream handling: roadmap amendment — P1's non-goal is narrowed to the dependency ceiling, and the post-merge operational evidence for EC-P1-2 and EC-P1-4 is appended to the Phase 1 section by SL-docs.5

## Acceptance Criteria

- [ ] EC-P1-1 — proven by `## Verification` V1 (the guard's import step passes at the declared floor), V2-N1 (the declared floor refuses `mcp==1.7.1` at resolution), V2-N2 (`mcp==1.7.1` genuinely breaks the startup import, `--no-deps` so it survives the correction), V2b (the gateway boots at the floor with the startup set bounded to the fixture), and **V2c (a real downstream server serves a real tool call through the gateway at the floor)** — V2c is the acceptance step; V2b and `/health` are preconditions only. Plus `actionlint .github/workflows/test.yml` clean and the job present in `yaml.safe_load(...)["jobs"]`.
- [ ] EC-P1-2 — proven pre-merge by V3 (`schedule` and `workflow_dispatch` both present in the parsed trigger map, cron `0 8 * * 1`), and post-merge by V6 — a `workflow_dispatch` run correlated on `event` **and** the exact `main` SHA, waited on with `gh run watch --exit-status`, with URL/conclusion/`headSha` recorded in the SL-docs.5 amendment.
- [ ] EC-P1-3 — proven by `uv run pytest tests/test_package_metadata.py -q` passing, and by mutating `src/pmcp/__init__.py` to a wrong version in a scratch checkout and confirming the same command fails.
- [ ] EC-P1-4 — proven pre-merge by V4 (the `install-smoke` job's **raw bytes** are identical between `main` and this branch) **and** V4b (the real, unpinned, lockfile-free `install-smoke` procedure executed locally and passing); proven post-merge by V7 — #112 refreshed against post-P1 `main` so its head SHA actually moves (a rerun keeps the old SHA and would re-report the stale, pre-P1 check set), then the checks on the **new** head showing exactly one failure, named `install-smoke`, with both SHAs recorded in the SL-docs.5 amendment.
- [ ] EC-P1-5 — proven by V5: `uv run pytest tests/ -q`, `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/pmcp --exclude baml_client` all green, and `rg -n '^### Changed' -A 20 CHANGELOG.md` showing the new entry under `## [Unreleased]`.
- [ ] Plan-internal — the roadmap amendment narrowing P1's dependency-bound non-goal is present in the same merge as the `pyproject.toml` floor change, proven by `git show --stat HEAD` (or the phase merge commit) listing both `pyproject.toml` and `specs/phase-plans-v11.md`, and by `rg -n 'Post-execution amendments' -A 10 specs/phase-plans-v11.md`.

## Verification

Run from the merged branch. `/tmp/p1*` are throwaway paths. **V2b and V2c start a gateway on port `3388`** — read `## Execution Notes > Boot-check isolation` first and prefer a CI runner or container over the operator's host. No step may bind `3344`.

```bash
# V0 — dependencies present (never bare `uv sync`)
uv sync --all-extras

# V1 — min-version-smoke, reproduced locally: parse the floor, install pinned
#      at it, import the startup set. This is exactly what the CI job does.
rm -rf /tmp/p1 /tmp/p1env
FLOOR=$(python3 <<'PY'
import pathlib
import re

text = pathlib.Path("pyproject.toml").read_text()
match = re.search(r'"mcp>=([0-9.]+),<', text)
assert match, "could not parse the mcp lower bound from pyproject.toml"
print(match.group(1))
PY
)
echo "declared mcp floor: $FLOOR"       # expect 1.8.0
uv build --wheel --out-dir /tmp/p1
uv venv /tmp/p1env
VIRTUAL_ENV=/tmp/p1env uv pip install /tmp/p1/*.whl "mcp==${FLOOR}"
/tmp/p1env/bin/pmcp --version
/tmp/p1env/bin/python -c "import pmcp.client.manager, pmcp.server, pmcp.config.loader"

# V2 — negative controls. TWO of them, because once the floor is corrected to
#      >=1.8.0 the old single control is invalid: `uv pip install <whl> mcp==1.7.1`
#      now fails RESOLUTION before any import runs, so under `set -e` it aborts and
#      without it the script reports a pass for a venv where nothing was installed.
#
# V2-N1 — the corrected floor must be ENFORCED: installing below it must fail.
rm -rf /tmp/p1bad
uv venv /tmp/p1bad
if VIRTUAL_ENV=/tmp/p1bad uv pip install /tmp/p1/*.whl "mcp==1.7.1" 2>/tmp/p1bad.err; then
  echo "FAIL: the declared floor did not refuse mcp==1.7.1"; exit 1
fi
grep -qiE 'resolve|conflict|incompatible' /tmp/p1bad.err
echo "OK: declared floor refuses mcp==1.7.1 at resolution"

# V2-N2 — and the floor must be HONEST: 1.7.1 must genuinely break the import.
#         Bypass resolution with --no-deps so this stays runnable after the
#         correction; otherwise N1 masks the evidence that motivated it.
rm -rf /tmp/p1bad2
uv venv /tmp/p1bad2
VIRTUAL_ENV=/tmp/p1bad2 uv pip install --no-deps /tmp/p1/*.whl
VIRTUAL_ENV=/tmp/p1bad2 uv pip install "mcp==1.7.1" "pydantic>=2" "pyjwt[crypto]" pyyaml python-dotenv aiohttp
if /tmp/p1bad2/bin/python -c "import pmcp.client.manager, pmcp.server, pmcp.config.loader"; then
  echo "FAIL: 1.7.1 imports cleanly — the 1.8.0 floor is not justified"; exit 1
fi
echo "OK: mcp==1.7.1 fails the startup import (ModuleNotFoundError: mcp.client.streamable_http)"

# V2b — the floor must BOOT under bounded isolation. Read
#       `## Execution Notes > Boot-check isolation` before running this on the
#       operator's host; prefer a CI runner or a container. Port 3388, never 3344.
#       Capture the live gateway's identity from the LISTENING SOCKET — `pgrep -f
#       "pmcp …"` self-matches the shell running it.
LIVE_PID=$(ss -ltnp 2>/dev/null | sed -n "s/.*:3344 .*pid=\([0-9]*\).*/\1/p" | head -1)
# An unresolved pid would silently compare two empty child sets and look like a pass.
[ -n "$LIVE_PID" ] || { echo "FAIL: could not resolve live gateway pid from :3344"; exit 1; }
# `pgrep -P` exits 1 when the process has no children, which under `set -euo
# pipefail` aborts on a perfectly valid empty set — hence `|| true`. The
# LIVE_PID guard above still fails hard, so an unresolved pid cannot slip past.
CHILDREN_BEFORE=$(pgrep -P "$LIVE_PID" 2>/dev/null | sort || true)
curl -sf --max-time 5 http://127.0.0.1:3344/health > /dev/null || { echo "FAIL: live gateway already down — abort"; exit 1; }

rm -rf /tmp/p1home && mkdir -p /tmp/p1home/lock /tmp/p1home/xdg
cp .github/probe/p1_probe_server.py .github/probe/p1_probe_client.py /tmp/p1home/
VIRTUAL_ENV=/tmp/p1env uv pip install "$(ls /tmp/p1/*.whl)[http]" "mcp==${FLOOR}"
printf '{"mcpServers": {"p1probe": {"command": "/tmp/p1env/bin/python", "args": ["/tmp/p1home/p1_probe_server.py"]}}}\n' > /tmp/p1home/mcp.json
printf 'servers:\n  allowlist: ["p1probe"]\n' > /tmp/p1home/policy.yaml
HOME=/tmp/p1home XDG_CONFIG_HOME=/tmp/p1home/xdg /tmp/p1env/bin/pmcp \
  --transport http --host 127.0.0.1 --port 3388 \
  --config /tmp/p1home/mcp.json \
  --project /tmp/p1home \
  --policy /tmp/p1home/policy.yaml \
  --lock-dir /tmp/p1home/lock -l info > /tmp/p1boot.log 2>&1 &
# Clean up ONLY the process we started. A broad `pkill -f 'port 3388'` matches the
# invoking shell wrapper and any unrelated process whose argv contains that string
# — the same self-match class as `pgrep -f "pmcp …"`.
PROBE_PID=$!
trap 'kill "$PROBE_PID" 2>/dev/null; wait "$PROBE_PID" 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 30); do
  curl -sf --max-time 2 http://127.0.0.1:3388/health > /dev/null && break
  sleep 1
done
grep -i 'MCP Gateway server started' /tmp/p1boot.log
# Assert the RESOLVED set is exactly the one fixture: lazy+eager == 1, which is
# the denominator in this log line. Do NOT tripwire on "counts in the hundreds" —
# the package-shipped manifest always contributes 106 entries, so a correctly
# isolated run reports eager=0, lazy=1, skipped=106, policy_denied=106. Those
# hundreds are the CORRECT output of a good run; only the denominator proves it.
grep -E 'Gateway initialized: [0-9]+/1 servers online' /tmp/p1boot.log \
  || { echo "FAIL: policy did not bound the startup set to the single fixture"; exit 1; }

# V2c — the actual acceptance: a real downstream server serves a real tool call
#       through the gateway at the declared floor. /health cannot show this —
#       transport/http.py:435-436 returns "ok": True as a hardcoded literal.
/tmp/p1env/bin/python /tmp/p1home/p1_probe_client.py http://127.0.0.1:3388/mcp

kill "$PROBE_PID" 2>/dev/null; wait "$PROBE_PID" 2>/dev/null; trap - EXIT INT TERM

# Every post-check below must FAIL the step, not just print. A regression that
# only prints "FAIL" and returns 0 is indistinguishable from a pass.
if grep -qiE 'orphan|sigkill' /tmp/p1boot.log; then
  echo "FAIL: orphan kill occurred during the probe boot"; exit 1
fi
curl -sf --max-time 5 http://127.0.0.1:3344/health > /dev/null \
  || { echo "FAIL: live gateway is down after the probe"; exit 1; }
CHILDREN_AFTER=$(pgrep -P "$LIVE_PID" 2>/dev/null | sort || true)
if [ "$CHILDREN_AFTER" != "$CHILDREN_BEFORE" ]; then
  echo "FAIL: live gateway child set changed: [$CHILDREN_BEFORE] -> [$CHILDREN_AFTER]"; exit 1
fi
echo "OK: live gateway healthy, child set unchanged"

# V3 — triggers and job presence. Note PyYAML parses `on:` as boolean True.
actionlint .github/workflows/test.yml
python3 <<'PY'
import yaml

cfg = yaml.safe_load(open(".github/workflows/test.yml"))
triggers = cfg.get("on", cfg.get(True))
assert "schedule" in triggers, triggers
assert "workflow_dispatch" in triggers, triggers
assert triggers["schedule"] == [{"cron": "0 8 * * 1"}], triggers["schedule"]
assert "min-version-smoke" in cfg["jobs"], list(cfg["jobs"])
assert "install-smoke" in cfg["jobs"], list(cfg["jobs"])
print("OK: triggers and jobs")
PY

# V4 — install-smoke is frozen BYTE-FOR-BYTE. Raw text, not parsed YAML: a
#      parsed-object comparison passes while comments, indentation, quoting, and
#      key order all change, which is not the freeze IF-0-P1-1 claims.
python3 <<'PY'
import re
import subprocess
import sys

PATH = ".github/workflows/test.yml"
JOB = "install-smoke"


def block(text: str) -> str:
    """The job's own raw lines: from `  <JOB>:` to the next job key, with the
    trailing blank/comment run that introduces the NEXT job stripped off."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.rstrip("\n") == f"  {JOB}:")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z0-9_.-]+:\s*$", lines[i]):
            end = i
            break
    while end > start and (
        not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")
    ):
        end -= 1
    return "".join(lines[start:end])


head = block(open(PATH, encoding="utf-8").read())
base = block(
    subprocess.run(
        ["git", "show", f"main:{PATH}"], capture_output=True, text=True, check=True
    ).stdout
)
if head.encode() != base.encode():
    sys.exit(f"{JOB} is not byte-identical to main — the change is wrong, not the job")
print(f"OK: {JOB} byte-identical to main ({len(head.encode())} bytes)")
PY

# V4b — and it must still RUN. This is the real job: no pin, no lockfile.
#       V1 pins the floor and therefore proves nothing about the unpinned resolve.
rm -rf /tmp/p1fresh /tmp/p1dist
uv build --wheel --out-dir /tmp/p1dist
uv venv /tmp/p1fresh
VIRTUAL_ENV=/tmp/p1fresh uv pip install /tmp/p1dist/*.whl
VIRTUAL_ENV=/tmp/p1fresh uv pip list
/tmp/p1fresh/bin/pmcp --version
/tmp/p1fresh/bin/python -c "import pmcp.client.manager, pmcp.server, pmcp.config.loader"

# V5 — full suite, lint, format, types, changelog
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
rg -n '^### Changed' -A 20 CHANGELOG.md
```

Post-merge, once the workflow is on `main`. Run by **SL-docs.5**, which does not gate the merge and is not runnable from a lane worktree.

```bash
# V6 (EC-P1-2) — a MANUALLY DISPATCHED run passes on unchanged main.
#   `gh run list --limit 1` is not acceptable evidence: it neither waits for nor
#   correlates to the run just dispatched, so it can select the merge's own push
#   run or any concurrent run. Correlate on event AND the exact main SHA.
REPO=Consiliency/pmcp
MAIN_SHA=$(gh api "repos/$REPO/commits/main" --jq .sha)
echo "dispatching against main @ $MAIN_SHA"
gh workflow run test.yml --repo "$REPO" --ref main

RUN_ID=""
for _ in $(seq 1 40); do
  RUN_ID=$(gh run list --repo "$REPO" --workflow test.yml --event workflow_dispatch \
    --json databaseId,headSha,createdAt \
    --jq "[.[] | select(.headSha == \"$MAIN_SHA\")] | sort_by(.createdAt) | last | .databaseId // empty")
  [ -n "$RUN_ID" ] && break
  sleep 5
done
[ -n "$RUN_ID" ] || { echo "FAIL: no workflow_dispatch run appeared for $MAIN_SHA"; exit 1; }

gh run watch "$RUN_ID" --repo "$REPO" --exit-status
gh run view "$RUN_ID" --repo "$REPO" --json url,event,headSha,conclusion    # record all four

# V7 (EC-P1-4) — #112 must be RE-RUN against post-P1 main, then still fail only
#   install-smoke. `gh pr checks 112` alone reads the EXISTING check set, which
#   already shows exactly one install-smoke failure — it would "pass" without
#   min-version-smoke ever having run. Reruns keep their original SHA/ref, so the
#   head branch must actually move.
OLD_SHA=$(gh pr view 112 --repo "$REPO" --json headRefOid --jq .headRefOid)
gh pr comment 112 --repo "$REPO" --body "@dependabot rebase"
NEW_SHA="$OLD_SHA"
for _ in $(seq 1 60); do
  NEW_SHA=$(gh pr view 112 --repo "$REPO" --json headRefOid --jq .headRefOid)
  [ "$NEW_SHA" != "$OLD_SHA" ] && break
  sleep 10
done
if [ "$NEW_SHA" = "$OLD_SHA" ]; then
  echo "BLOCKED: #112 head did not move; rebase was a no-op. Record this and stop."
  echo "Do NOT close/reopen the PR to force a run — closing a Dependabot PR tells"
  echo "Dependabot to stop offering that version, and the roadmap keeps #112 open"
  echo "as the standing canary. Escalate to the operator instead."
  exit 1
fi
echo "#112 refreshed: $OLD_SHA -> $NEW_SHA"

# Wait for the checks on the NEW head, then assert exactly one failure named install-smoke.
gh pr checks 112 --repo "$REPO" --watch --fail-fast=false || true
gh pr checks 112 --repo "$REPO" --json name,state,link --jq \
  '[.[] | select(.state == "FAILURE" or .state == "ERROR") | .name]' | tee /tmp/p1_112_failures.json
python3 -c "
import json
names = json.load(open('/tmp/p1_112_failures.json'))
assert names == ['install-smoke'], f'expected only install-smoke to fail, got {names}'
print('OK: #112 fails only install-smoke against post-P1 main')
"
```

Record `$MAIN_SHA`, the dispatched run's URL/conclusion, `$OLD_SHA`, and `$NEW_SHA` in the SL-docs.5 amendment. A recorded result without the SHA it was measured against is not evidence.
