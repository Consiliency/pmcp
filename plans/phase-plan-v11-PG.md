---
phase_loop_plan_version: 1
phase: PG
roadmap: specs/phase-plans-v11.md
roadmap_sha256: 2f03c6f3c01d903e55b87bdbe4ca8b9b25fcbb318a48a30161d35cd6b76b3be0
---

# PG: pangram-mcp onto mcp 2.x

> **This plan document lives in `pmcp`. Every lane executes in `Consiliency/pangram-mcp`.**
> Paths under `## Lanes` are relative to the *pangram-mcp* repo root, not this one.
> The only pmcp-repo artifacts this phase writes are this plan and its manifest entry.

## Context

`pangram-mcp` 0.1.3 pins `mcp>=1.14.0,<2.0.0`. The upper bound is load-bearing:
`mcp` 2.0.0 **removed** `mcp.server.fastmcp` outright — it is not a rename — and
`src/pangram_mcp/server.py:23` imports `FastMCP` at module scope. That cap is now
what holds this server on the previous protocol revision.

The port surface was measured empirically during planning against a real
`mcp==2.0.0` install, not read out of source. Findings that shape the lanes:

1. **`MCPServer` is the successor and it is a drop-in for this server's usage.**
   `from mcp.server import MCPServer` exists in 2.0.0 and is **absent from
   1.29.0**, the last 1.x. `MCPServer("pangram")` takes the name positionally
   exactly as `FastMCP("pangram")` did; `MCPServer.tool()` still accepts
   `annotations`; `MCPServer.run()` still defaults to `transport="stdio"`, so
   `main()` is unchanged. `ToolAnnotations` still imports from `mcp.types`
   (re-exported from `mcp_types._types`).

2. **`ToolAnnotations` fields were renamed to snake_case with camelCase
   serialization aliases.** 1.x field names were literally `readOnlyHint`,
   `destructiveHint`, `idempotentHint`, `openWorldHint`. In 2.x they are
   `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`
   with those camelCase strings as aliases. Because `populate_by_name=True`,
   **the existing camelCase kwargs still construct correctly at runtime and the
   wire output is camelCase either way** — but `mypy src` (which this repo's CI
   runs on every push) reports four `call-arg` errors against the camelCase
   kwargs. The port must move to snake_case kwargs. This is the part of the
   change that a source-reading estimate misses, and it is also why there is no
   dual-1.x/2.x option worth having.

3. **`serverInfo.version` regresses to `""` unless set explicitly.** 1.x FastMCP
   defaulted it to the *mcp library* version (a probe against the current build
   reports `{"name": "pangram", "version": "1.29.0"}` — plainly wrong, it is not
   this package's version). `MCPServer.__init__` defaults `version: str = ""`, so
   a literal port reports an empty string. The fix is to pass this package's own
   `__version__`, which is both correct and what the existing `__version__` drift
   test already pins.

4. **`httpx` must stay explicitly declared.** `mcp` 2.x depends on **`httpx2`**,
   not `httpx`, and `server.py` does `import httpx`. A fresh resolve of
   `mcp>=2.0.0,<3.0.0` alone installs `httpx2 2.9.1` and **no `httpx`**.
   `pangram-mcp` already declares `httpx>=0.27` directly, so it is immune to the
   trap that `pmcp`'s `cli.py` walks into (roadmap P2 Lane B). That line is now
   load-bearing and must not be "cleaned up" as redundant.

5. **The tool contract survives the port byte-identically.** A serialized
   `list_tools()` descriptor (`model_dump(mode="json", by_alias=True,
   exclude_none=True)`) was captured from the unported server on `mcp==1.29.0`
   and from the ported server on `mcp==2.0.0` and diffed: **identical**, across
   `name`, `description`, `inputSchema` (including `title: "analyzeArguments"`),
   `outputSchema` (including `$defs`), and `annotations` (camelCase on the wire).
   `capabilities` in the `initialize` result are identical too.

**Only one stable `mcp` 2.x release exists** (`2.0.0`; the rest of the 2.x line on
PyPI is `2.0.0a1`…`2.0.0rc1`, all prereleases that uv excludes by default). So the
declared floor and the resolved ceiling are the same version today, and
`min-version-smoke` is temporarily degenerate with `install-smoke`. That is not a
reason to skip it — it becomes load-bearing the moment 2.1 ships, and the job
verifies the *declared* floor is honest either way.

This repo already ships all four IF-0-P1-1 guards (`install-smoke`,
`min-version-smoke`, a `schedule:` trigger, and the `__version__` drift test) —
it is the repo the roadmap's P1 copies them *from*. So PG does not build guards;
it must prove they still hold, unmodified, across the bump. `uv.lock` is
gitignored here, so CI always resolves fresh — the opposite of `pmcp`.

## Interface Freeze Gates

- [ ] IF-0-PG-1 — `pangram-mcp` runs on `mcp>=2.0.0,<3.0.0` via `mcp.server.MCPServer`; the `analyze` tool descriptor (`name`, `description`, `inputSchema`, `outputSchema`, `annotations` serialized with camelCase aliases) is byte-identical to the 0.1.3 / `mcp` 1.29.0 baseline; `serverInfo` reports `{"name": "pangram", "version": <package __version__>}`; the package is reachable over stdio from both a `mcp` 1.x and a `mcp` 2.x client, and is published to PyPI.

## Lane Index & Dependencies

SL-0 — Packaging preamble (bound raise + version bump)
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3
  Parallel-safe: no (preamble — sole writer of the version-carrying files)

SL-1 — Server API port to MCPServer
  Depends on: SL-0
  Blocks: SL-2, SL-3
  Parallel-safe: yes

SL-2 — Guard proof and cross-version reachability
  Depends on: SL-0, SL-1
  Blocks: SL-3
  Parallel-safe: no (reducer — consumes both producer lanes' merged state)

SL-3 — Documentation & spec reconciliation
  Depends on: SL-0, SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no (terminal)

## Lanes

### SL-0 — Packaging preamble (bound raise + version bump)

- **Scope**: Raise the `mcp` bound to a two-sided 2.x range and bump the package version, atomically across both files that carry it, so every downstream lane develops against a real 2.x environment from day one.
- **Owned files**: `pyproject.toml`, `src/pangram_mcp/__init__.py`, `tests/test_packaging.py`
- **Interfaces provided**: `mcp>=2.0.0,<3.0.0` two-sided bound; `pangram_mcp.__version__ == "0.1.4"`; explicit `httpx>=0.27` declaration retained
- **Interfaces consumed**: (none)
- **Parallel-safe**: no

`pyproject.toml` `version` and `src/pangram_mcp/__init__.py` `__version__` are a
**single atomic edit** — the pre-existing `test_dunder_version_matches_package_metadata`
test fails if they diverge, and 0.1.2 shipped with exactly that divergence. They
are deliberately owned by one lane for that reason.

Replace the existing bound comment rather than deleting it; it is the record of
why both ends are declared. The new comment must state that **both** ends were
established by installing, and must state why `httpx` is declared explicitly.

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-0.1 | test | — | `tests/test_packaging.py` | declared `mcp` bound is two-sided with floor `2.0.0` and ceiling `3.0.0`; the installed `mcp` satisfies the declared floor; `httpx` appears as an explicit entry in `[project].dependencies`; `pangram_mcp.__version__` equals `[project].version` in `pyproject.toml` | `uv run pytest tests/test_packaging.py -q` |
| SL-0.2 | impl | SL-0.1 | `pyproject.toml`, `src/pangram_mcp/__init__.py` | — | — |
| SL-0.3 | verify | SL-0.2 | `pyproject.toml`, `src/pangram_mcp/__init__.py`, `tests/test_packaging.py` | all SL-0 tests | `uv sync --extra dev && uv run pytest tests/test_packaging.py -q && uv run python -c "import importlib.metadata as m; assert m.version('mcp').startswith('2.'), m.version('mcp')"` |

### SL-1 — Server API port to MCPServer

- **Scope**: Move `server.py` off the removed `FastMCP` API onto `MCPServer`, holding the `analyze` tool descriptor byte-identical to the 1.29.0 baseline.
- **Owned files**: `src/pangram_mcp/server.py`, `tests/test_server.py`, `tests/golden/analyze_tool.json`
- **Interfaces provided**: `pangram_mcp.server.mcp` as an `MCPServer` instance; the frozen `analyze` tool descriptor; `serverInfo.version == pangram_mcp.__version__`
- **Interfaces consumed**: `pangram_mcp.__version__` (SL-0, `src/pangram_mcp/__init__.py`); the `mcp>=2.0.0,<3.0.0` environment (SL-0, `pyproject.toml`)
- **Parallel-safe**: yes

`tests/golden/analyze_tool.json` is the captured 0.1.3 / `mcp` 1.29.0 descriptor
and is the contract EC-PG-1 means by "byte-identical". It is checked in as a
**new file** so the assertion survives the port rather than being re-derived from
the ported code. Capture it from the pre-port state before editing `server.py`.

The port is three edits and no behaviour change:
`from mcp.server.fastmcp import FastMCP` → `from mcp.server import MCPServer`;
`mcp = FastMCP("pangram")` → `mcp = MCPServer("pangram", version=__version__)`
(add `from pangram_mcp import __version__` — `__init__.py` does not import
`server`, so there is no cycle); and the four `ToolAnnotations` kwargs to
snake_case. **Do not touch the `analyze` body, the models, `_resolve_text`,
`_explain_http_error`, or `main()`.**

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/golden/analyze_tool.json`, `tests/test_server.py` | golden descriptor captured pre-port; `await mcp.list_tools()` serialized with `model_dump(mode="json", by_alias=True, exclude_none=True)` equals the golden file; `annotations` keys are camelCase on the wire; `type(server.mcp).__name__ == "MCPServer"`; `serverInfo.version == pangram_mcp.__version__` | `uv run pytest tests/test_server.py -q` |
| SL-1.2 | impl | SL-1.1 | `src/pangram_mcp/server.py` | — | — |
| SL-1.3 | verify | SL-1.2 | `src/pangram_mcp/server.py`, `tests/test_server.py`, `tests/golden/analyze_tool.json` | all SL-1 tests plus the 12 pre-existing tests | `uv run pytest -q && uv run ruff check . && uv run mypy src` |

### SL-2 — Guard proof and cross-version reachability

- **Scope**: Prove all four IF-0-P1-1 guards still hold unmodified across the bump, and prove a `mcp` 1.x client and the currently-published 1.x `pmcp` gateway both still reach `pangram::analyze` on the ported server.
- **Owned files**: `.github/workflows/test.yml`, `tests/guard_contract_check.py`, `tests/ec_pg3_gateway_check.py`
- **Interfaces provided**: EC-PG-2 guard evidence; EC-PG-3 Assumption-6 verdict
- **Interfaces consumed**: the built wheel and declared floor (SL-0, `pyproject.toml`); the ported `pangram_mcp.server` module and its console script (SL-1, `src/pangram_mcp/server.py`)
- **Parallel-safe**: no

`.github/workflows/test.yml` is owned here because this lane is the one entitled
to change it if a guard needs adjusting for 2.x. **The expected outcome is no
change.** The `min-version-smoke` floor parser regex `"mcp>=([0-9.]+),<` was
checked against the new bound during planning and parses `2.0.0` correctly, and
`install-smoke` already imports `pangram_mcp.server` (the entry-point module, not
the bare package). If this lane ends with the file unmodified, it must say so in
its commit message — that is the EC-PG-2 audit trail. Per the roadmap's
cross-cutting principle 2, **never weaken a guard to make this phase pass**; a
red guard means the change is wrong.

EC-PG-3 is the roadmap's live test of Assumption 6 and is run **before** publish,
against a locally built wheel — `uvx --from ./dist/<wheel> pangram-mcp` exercises
the identical code path the manifest's `uvx pangram-mcp` will take, so a failure
is caught before anything reaches PyPI. `PANGRAM_API_KEY` lives at
`op://Consiliency Deploy Secrets/PANGRAM_API_KEY/credential`; inject it with
`op run -- <command>` and **never read, echo, or write the value**.

> **A spare port is NOT sufficient isolation. Read this before starting the
> gateway.** Two mechanisms in the published 1.x `pmcp` reach outside the port:
>
> 1. **Singleton lock.** `_run_http` calls `acquire_singleton_lock(self._lock_dir)`
>    (`src/pmcp/server.py:775-784`) and `--lock-dir` defaults to `~/.pmcp`
>    (`src/pmcp/identity.py:176-195`) — a *global per-user* lock. A second
>    instance on a spare port collides with the live gateway's lock and refuses
>    to start.
> 2. **Orphan reaping — this one is destructive.** `_kill_orphan_processes`
>    (`src/pmcp/server.py:683`) scans `/proc` and sends **SIGKILL** to any process
>    whose `(argv0 basename, args tuple)` matches a **configured** local stdio
>    server. The live gateway's downstream children are exactly such processes,
>    so a second instance that loads the operator's config **kills the live
>    gateway's servers**. Roadmap cross-cutting principle 3 forbids this.
>
> **`--config` alone does not fix (2).** Config paths are *additive*:
> `_candidate_config_paths` appends the custom path **after** the user paths
> (`src/pmcp/config/loader.py:285-299`), so the operator's `~/.mcp.json` and
> `~/.claude/.mcp.json` still load and still populate the kill fingerprints.
> Because `default_user_config_paths()` resolves `Path.home()` **at call time**
> (`src/pmcp/config/loader.py:48-53`), the only reliable isolation is to
> **override `HOME`** to a throwaway directory. Do that, and pass an explicit
> `--lock-dir`, `--project` (empty dir), `--config`, and `--policy` on top. The
> exact invocation is in `## Verification` step 4 — use it verbatim.

`gateway.connect_server` and `gateway.invoke` must be driven by a real MCP client
that **asserts**, not by prose. Step 4 supplies the client.

If SL-2.4 exits **2**, **stop and report** — do not work around it. Per the
roadmap, that outcome means PG gains a dependency on P2 and
`specs/phase-plans-v11.md` needs amending; SL-3 carries it. An exit of **3**
(checker bug) or **4** (credential never injected) is **not** a compatibility
result and must never reach SL-3.3 — fix the script or the invocation and re-run.
Routing on "the command failed" instead of on the code is how a parsing bug
becomes a false roadmap amendment.

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `.github/workflows/test.yml` | all four IF-0-P1-1 guards asserted structurally — see the guard-contract script below | `python3 tests/guard_contract_check.py` (script body inline below; run from repo root) |
| SL-2.2 | impl | SL-2.1 | `.github/workflows/test.yml` | — | — |
| SL-2.3 | verify | SL-2.2 | `.github/workflows/test.yml` | local replication of `install-smoke` and `min-version-smoke`, plus green CI | see `## Verification` steps 1, 2 and 2b |
| SL-2.4 | verify | SL-2.3 | — | EC-PG-3: 1.x reachability | see `## Verification` steps 3 and 4 |

SL-2.1's guard contract must assert **all four** IF-0-P1-1 guards, not just the
floor parser. A `workflow_dispatch` run does **not** prove the `schedule:`
trigger exists — that is a separate key and must be asserted structurally. Run
this from the repo root; it is a check script, not a pytest module, so it does
not collide with SL-0's or SL-1's owned test files:

```python
# tests/guard_contract_check.py  (owned by SL-2)
import pathlib, re, sys
wf = pathlib.Path(".github/workflows/test.yml").read_text()
pj = pathlib.Path("pyproject.toml").read_text()
fails = []

# Behavioural assertions must run against COMMAND LINES, not raw text: the
# unchanged workflow explains itself with the comment "No --frozen / no uv.lock"
# (.github/workflows/test.yml:53), and a raw substring check flags that valid
# baseline as a failure. Strip comments first; keep `wf` for structural keys.
wf_cmds = "\n".join(re.sub(r"#.*$", "", ln) for ln in wf.splitlines())

# Guard 1 — install-smoke resolves fresh and imports the ENTRY-POINT module.
if "install-smoke:" not in wf: fails.append("install-smoke job missing")
if "import pangram_mcp.server" not in wf_cmds: fails.append("install-smoke does not import the entry-point module")
if "--frozen" in wf_cmds: fails.append("install-smoke must not use a lockfile")

# Guard 2 — min-version-smoke pins the DECLARED floor, and the floor is 2.0.0.
if "min-version-smoke:" not in wf: fails.append("min-version-smoke job missing")
if 'mcp==${FLOOR}' not in wf_cmds: fails.append("min-version-smoke does not pin the parsed floor")
m = re.search(r'"mcp>=([0-9.]+),<([0-9.]+)"', pj)
if not m: fails.append("mcp bound is not two-sided")
elif m.group(1) != "2.0.0": fails.append(f"floor is {m.group(1)}, expected 2.0.0")
elif m.group(2) != "3.0.0": fails.append(f"ceiling is {m.group(2)}, expected 3.0.0")

# Guard 3 — the schedule: trigger. workflow_dispatch does NOT substitute for it.
if not re.search(r"^\s*schedule:", wf, re.M): fails.append("schedule: trigger missing")
if not re.search(r"^\s*- cron:", wf, re.M): fails.append("schedule: has no cron entry")
if not re.search(r"^\s*workflow_dispatch:", wf, re.M): fails.append("workflow_dispatch missing")

# Guard 4 — the __version__ drift test exists and is wired into the suite.
tst = pathlib.Path("tests/test_server.py").read_text()
if "test_dunder_version_matches_package_metadata" not in tst: fails.append("drift test missing")
if "importlib.metadata" not in tst and "import importlib.metadata" not in tst:
    fails.append("drift test does not compare against distribution metadata")

print("\n".join(f"FAIL: {f}" for f in fails) or "all four guards asserted")
sys.exit(1 if fails else 0)
```

And the EC-PG-3 client, which **asserts** rather than describing. Run it with the
`mcp` 1.29.0 interpreter (`/tmp/pg-client-1x/bin/python`) so the client half is
genuinely 1.x. It exits non-zero on any failure, which is what makes SL-2.4 a
real gate:

**The fault taxonomy below is load-bearing, not stylistic.** This plan routes an
EC-PG-3 failure to a roadmap amendment that adds a P2 dependency to PG. A bug in
*this script* must therefore never be able to look like a compatibility failure,
or it would produce a false amendment — a wrong structural conclusion drawn from
a working system. The distinction is made **mechanical via exit codes**, not left
to a reader's judgement:

| Exit | Meaning | Action |
|---|---|---|
| 0 | pass | proceed |
| 2 | **compatibility** fault — the 1.x gateway could not reach or serve the 2.x server | Assumption 6 broke → SL-3.3 amendment |
| 3 | **checker** fault — the call succeeded but this script could not parse the envelope | fix this script; **never** amend |
| 4 | **environment** fault — the credential did not arrive | fix the `op run` invocation; never amend |

Envelope shape is not guesswork: `InvokeOutput` is
`{tool_id, ok, result, task, truncated, summary, raw_size_estimate, …}`
(`src/pmcp/types.py:743-756`), so the classification lives at
**`result.structuredContent`** — one layer deeper than a naive read.

```python
# tests/ec_pg3_gateway_check.py  (owned by SL-2)
"""EC-PG-3: drive the PUBLISHED 1.x pmcp gateway and assert pangram::analyze works.

Exit codes are a contract — the plan routes on them. See the table in the plan:
  0 pass | 2 compatibility (amend) | 3 checker bug (fix me) | 4 credential (fix env)
"""
import asyncio, json, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1]
PROMPT = "The quick brown fox jumps over the lazy dog. " * 20

class Compat(Exception): pass    # -> 2
class Checker(Exception): pass   # -> 3
class Env(Exception): pass       # -> 4

def _text(res):
    return "".join(c.text for c in res.content if getattr(c, "text", None))

async def run() -> int:
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            if "gateway.connect_server" not in names:
                raise Compat(f"gateway tools absent: {sorted(names)[:10]}")

            res = await s.call_tool("gateway.connect_server", {"server_name": "pangram"})
            body = _text(res)
            if res.isError or "online" not in body.lower():
                raise Compat(f"pangram did not come online: {body[:400]}")

            res = await s.call_tool("gateway.invoke", {
                "tool_id": "pangram::analyze", "arguments": {"text": PROMPT},
            })
            body = _text(res)
            # A missing credential is an ENV fault, not a compatibility fault:
            # server.py raises this exact message when PANGRAM_API_KEY is unset.
            if "PANGRAM_API_KEY is not set" in body:
                raise Env("the gateway did not receive PANGRAM_API_KEY — see step 4c's op run")
            if res.isError:
                raise Compat(f"pangram::analyze errored: {body[:400]}")

            # ---- everything below is PARSING: failures here are CHECKER faults ----
            try:
                env = json.loads(body)
            except ValueError as e:
                raise Checker(f"invoke body is not JSON ({e}): {body[:300]}")
            if "ok" not in env:
                raise Checker(f"no `ok` in InvokeOutput envelope; keys={sorted(env)[:10]}")
            if env["ok"] is not True:
                raise Compat(f"gateway reported ok=false: {json.dumps(env)[:400]}")
            result = env.get("result")
            if not isinstance(result, dict):
                raise Checker(f"`result` is {type(result).__name__}, expected dict")
            payload = result.get("structuredContent")
            if not isinstance(payload, dict):
                raise Checker(f"no `result.structuredContent` dict; keys={sorted(result)[:10]}")

            # ---- the actual acceptance assertion ----
            pred = payload.get("prediction")
            if not pred:
                raise Checker(f"no `prediction`; structuredContent keys={sorted(payload)[:10]}")
            for k in ("fraction_ai", "fraction_ai_assisted", "fraction_human"):
                if not isinstance(payload.get(k), (int, float)):
                    raise Checker(f"`{k}` missing or non-numeric: {payload.get(k)!r}")
            print(f"EC-PG-3 PASS — prediction={pred!r} fraction_ai={payload['fraction_ai']}")
            return 0

def main() -> int:
    try:
        return asyncio.run(run())
    except Compat as e:
        print(f"EC-PG-3 COMPATIBILITY FAULT — Assumption 6 may be broken: {e}", file=sys.stderr)
        return 2
    except Checker as e:
        print(f"EC-PG-3 CHECKER FAULT — fix this script; do NOT amend the roadmap: {e}",
              file=sys.stderr)
        return 3
    except Env as e:
        print(f"EC-PG-3 ENVIRONMENT FAULT — fix the invocation; do NOT amend: {e}",
              file=sys.stderr)
        return 4

sys.exit(main())
```

If the envelope shape differs from the above the script exits **3**, which by the
table means fix the **parsing** and never the assertions — the criterion remains
a real classification with a `prediction` and three numeric fractions. **Only an
exit of 2 may trigger SL-3.3's amendment.**

### SL-3 — SL-docs: Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, update cross-cutting documentation touched or invalidated by this phase's impl lanes, and append any post-execution amendments to phase specs whose interface freezes turned out wrong.
- **Owned files**: `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `.claude/docs-catalog.json`
- **Interfaces provided**: (none)
- **Interfaces consumed**: SL-0's declared bound and version; SL-1's ported module and frozen descriptor; SL-2's guard evidence and EC-PG-3 verdict
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-0, SL-1, SL-2

Scope notes for this phase specifically. `README.md` (73 lines) makes **no claim
about the `mcp` library version, the Python API, or `FastMCP`** — it documents
the `analyze` tool contract, which this phase holds byte-identical. So the
expected outcome is **no README change**, recorded explicitly in the commit
message. This repo has **no `CHANGELOG.md`** and does not use one; `release.yml`
runs `gh release create --generate-notes`, so release notes come from commit
messages. Do not add a CHANGELOG as a side effect of this phase.

`CONTRIBUTING.md` and `SECURITY.md` do not currently exist in this repo; they are
listed as owned so that if the catalog rescan creates them this lane is their
writer. Do not create them merely because they are listed.

The roadmap amendment target (`specs/phase-plans-v11.md`) lives in the **pmcp**
repo, not here — see `## Execution Notes` → "Cross-repo amendment path".

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-3.1 | docs | — | `.claude/docs-catalog.json` | Rescan: `python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/scaffold_docs_catalog.py" --rescan`. If the helper is absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and audit by hand. |
| SL-3.2 | docs | SL-3.1 | per catalog | For each catalogued file decide whether this phase changes it. Expected: no change to `README.md` (it makes no version or Python-API claim). Record every intentionally-skipped file in the commit message. |
| SL-3.3 | docs | SL-3.2 | (pmcp repo — see Execution Notes) | Only if `ec_pg3_gateway_check.py` exited **2**: raise the amendment to `specs/phase-plans-v11.md` in the pmcp repo per "Cross-repo amendment path". An exit of 3 or 4 is a checker/credential bug, **not** a compatibility result — do not amend on either. On exit 0, record "Assumption 6 confirmed; no roadmap amendment" and make no cross-repo commit. |
| SL-3.4 | verify | SL-3.3 | — | Run repo doc linters if configured. This repo configures none (`ruff` covers Python only), so this is a recorded no-op. |

## Execution Notes

- **Lane sequencing is genuinely serial-ish, and that is correct.** SL-0 must land
  before SL-1 can even resolve a 2.x environment (the current cap forbids it), and
  SL-2 needs both. The parallelism win here is small; the decomposition exists for
  disjoint ownership and reviewable atomic commits, not for wall-clock speed. Do
  not "optimize" it by collapsing SL-0 into SL-1 — that would put the version bump
  and the API port in one commit and make a revert of either impossible.
- **Single-writer files**: `pyproject.toml` → SL-0. `src/pangram_mcp/__init__.py` →
  SL-0 (paired with `pyproject.toml`; the drift test binds them). `src/pangram_mcp/server.py`
  → SL-1. `tests/test_server.py` → SL-1. `.github/workflows/test.yml` → SL-2.
  `README.md` → SL-3.
- **Known destructive changes**: none — every lane is purely additive or an
  in-place edit. No file is deleted by any lane.
- **Expected add/add conflicts**: none. SL-0 stubs nothing that a later lane
  replaces; `tests/test_packaging.py` (SL-0) and `tests/golden/analyze_tool.json`
  (SL-1) are new files owned by exactly one lane each.
- **SL-0 re-exports**: not applicable — SL-0 adds no symbol to `__init__.py`. It
  only changes the existing `__version__` string literal. Do not add imports to
  `__init__.py`: `server.py` imports `__version__` from it, so any import of
  `server` from `__init__.py` would create a cycle.
- **Do not add a `CHANGELOG.md`.** See SL-3 scope notes.
- **Do not regenerate `uv.lock`.** It is gitignored in this repo by design so CI
  resolves fresh. `uv sync` will create one locally; leave it untracked.
- **Cross-repo amendment path** (SL-3.3, only on EC-PG-3 failure): the roadmap
  lives in `Consiliency/pmcp` at `specs/phase-plans-v11.md`. Amending it is a
  **separate commit in a separate repo** from every other commit in this phase.
  Append a `### Post-execution amendments` subsection to the Phase 4 (PG) section
  recording the dated failure of EC-PG-3, and add `P2` to PG's `Depends on`. Do
  not attempt to edit it from a pangram-mcp worktree.
- **Publishing is an operator action, not a lane.** EC-PG-5 requires a git tag
  push that triggers `release.yml` → PyPI trusted publishing. No lane teammate
  should publish autonomously; the tag is pushed by the operator after SL-2.4
  passes and the phase merges. See `## Verification` step 6.
- **EC-PG-4 is deferred, not skipped.** It cannot run until roadmap phase P2
  lands a 2.x `pmcp`. It is not a blocker on publishing (roadmap PG scope notes);
  it is follow-up verification recorded against this phase.
- **Secrets**: `PANGRAM_API_KEY` is at `op://Consiliency Deploy Secrets/PANGRAM_API_KEY/credential`.
  Inject via `op run --` only. Never read it into a variable, echo it, write it to
  a file, or pass it on a command line.
- **Service safety**: the live gateway on `127.0.0.1:3344` is the operator's daily
  driver. **A spare port is not isolation.** The published 1.x `pmcp` takes a
  global singleton lock at `~/.pmcp` and SIGKILLs `/proc` matches for any
  *configured* local stdio server, so a second instance that sees the operator's
  config kills the live gateway's children. `--config` is additive and does not
  prevent this; `HOME` must be overridden. Use `## Verification` step 4's
  invocation verbatim, and run its 4b/4d before-and-after checks — they are the
  evidence for roadmap cross-cutting principle 3. Never restart or reconfigure
  the live unit.
- **Stale-base guidance** (copy verbatim): Lane teammates working in isolated
  worktrees do not see sibling-lane merges automatically. If a lane finds its
  worktree base is pre-SL-0-merge, it MUST stop and report rather than committing —
  the orchestrator will re-spawn or rebase. Silent `git reset --hard` or
  `git checkout HEAD~N -- …` in a stale worktree produces commits that destroy
  peer-lane work on `--no-ff` merge.

## Dispatch Hints

- required_capabilities: live_launch, structured_output
- executors: codex, claude

## Execution Policy
- execute: effort=low, reason=the port is three mechanical edits in one file
- SL-1: effort=medium, reason=wire-contract parity must not drift silently
- SL-2: effort=medium, reason=this lane is the roadmap's Assumption 6 test
- SL-3: effort=minimal, reason=docs sweep only

## Acceptance Criteria

- [ ] EC-PG-1 — proven by `uv run pytest tests/test_server.py -q` (golden-descriptor equality against `tests/golden/analyze_tool.json` plus the `MCPServer` type assertion) and `rg -n 'from mcp.server import MCPServer' src/pangram_mcp/server.py`
- [ ] EC-PG-2 — proven by `uv run pytest tests/test_packaging.py -q`, plus `python3 tests/guard_contract_check.py` asserting **all four** IF-0-P1-1 guards structurally (including that `schedule:` carries a `cron:` entry — a `workflow_dispatch` run does not substitute for it, and that the `__version__` drift test compares against distribution metadata), plus `## Verification` steps 1, 2 and 2b with every CI job reporting `success`
- [ ] EC-PG-3 — proven by `## Verification` steps 3 and 4: a `mcp` 1.29.0 stdio client, and then `tests/ec_pg3_gateway_check.py` driving the currently-published 1.x `pmcp` gateway, each obtain a real classification (a `prediction` plus three numeric fractions) from `pangram::analyze` — read at `result.structuredContent` per `InvokeOutput` (`src/pmcp/types.py:743-756`) — with the checker **exiting 0**; an exit of 3 or 4 is a checker/credential bug and neither satisfies nor refutes this criterion. Step 4d additionally asserts the live gateway on `127.0.0.1:3344` has the **same PID and same children** as before the run
- [ ] EC-PG-4 — proven by `## Verification` step 7, deferred until roadmap phase P2 lands a 2.x `pmcp`; not a blocker on EC-PG-5
- [ ] EC-PG-5 — proven by `## Verification` step 6: after `gh run watch` confirms `release.yml` succeeded **and** PyPI is serving 0.1.4, a `mcp` client run against `uvx pangram-mcp` under an empty `UV_CACHE_DIR` asserts `serverInfo.version == "0.1.4"` and the `analyze` tool is present

## Verification

Run from the `pangram-mcp` repo root after all lanes merge. Steps 1–5 gate the
release; step 6 is the release; step 7 is deferred follow-up.

```bash
# 1. install-smoke replication — fresh resolve, NO lockfile, import the
#    entry-point module (not the bare package).
rm -rf dist /tmp/pg-fresh
uv build --wheel --out-dir dist
uv venv --python 3.12 /tmp/pg-fresh
VIRTUAL_ENV=/tmp/pg-fresh uv pip install dist/*.whl
VIRTUAL_ENV=/tmp/pg-fresh uv pip list | grep -E '^(mcp|httpx|httpx2) '
#    expect: mcp 2.x, AND httpx present (from our explicit dep), AND httpx2
#    present (from mcp). A missing httpx here means the explicit dep was dropped.
/tmp/pg-fresh/bin/python -c "import pangram_mcp.server"

# 2. min-version-smoke replication — install pinned at exactly the declared floor.
FLOOR=$(python3 -c "
import re, pathlib
t = pathlib.Path('pyproject.toml').read_text()
m = re.search(r'\"mcp>=([0-9.]+),<', t)
assert m, 'could not parse the mcp lower bound'
print(m.group(1))
")
echo "declared floor: $FLOOR"     # expect 2.0.0
rm -rf /tmp/pg-floor && uv venv --python 3.12 /tmp/pg-floor
VIRTUAL_ENV=/tmp/pg-floor uv pip install dist/*.whl "mcp==${FLOOR}"
/tmp/pg-floor/bin/python -c "import pangram_mcp.server"

# 2b. Guard contract + green CI. Structural assertions cannot prove the jobs
#     actually pass, and a green run cannot prove `schedule:` is configured —
#     both halves are required.
python3 tests/guard_contract_check.py
#    Same run-selection rule as step 6a: `-L1` races the dispatch and can select
#    an older run. Filter on this branch's head SHA and capture the id once.
BRANCH=$(git rev-parse --abbrev-ref HEAD); HEAD_SHA=$(git rev-parse HEAD)
gh workflow run test.yml --ref "$BRANCH"
TEST_RUN=""
until [ -n "$TEST_RUN" ]; do
  TEST_RUN=$(gh run list --workflow=test.yml -L 50 --json databaseId,headSha,event \
      -q "[.[] | select(.headSha==\"$HEAD_SHA\" and .event==\"workflow_dispatch\")] | .[0].databaseId // empty")
  [ -n "$TEST_RUN" ] || { echo "waiting for a test.yml dispatch at $HEAD_SHA..."; sleep 10; }
done
gh run watch "$TEST_RUN" --exit-status
gh run view "$TEST_RUN" --json jobs -q '.jobs[] | "\(.name): \(.conclusion)"'
#    expect every job `success`, including install-smoke and min-version-smoke.

# 3. EC-PG-3a — a mcp 1.x CLIENT reaches the ported 2.x server over stdio.
#    This is the protocol-level half of Assumption 6.
rm -rf /tmp/pg-client-1x && uv venv --python 3.12 /tmp/pg-client-1x
VIRTUAL_ENV=/tmp/pg-client-1x uv pip install "mcp==1.29.0"
#    Drive stdio_client -> ClientSession against /tmp/pg-fresh/bin/pangram-mcp.
#    Assert: initialize negotiates a HANDSHAKE-era version (expect 2025-11-25),
#    serverInfo.version == the package version, list_tools returns ['analyze']
#    with camelCase annotation keys, and call_tool('analyze', ...) returns
#    structuredContent with a `prediction` and per-class fractions.
#    Point PANGRAM_API_BASE at a local stub to avoid spending credits, OR use
#    the real endpoint under `op run --` for a live classification.

# 4. EC-PG-3b — the CURRENTLY PUBLISHED 1.x pmcp gateway reaches it.
#    READ THE ISOLATION WARNING IN SL-2 BEFORE RUNNING THIS. A spare port alone
#    does NOT protect the live gateway: a second instance that loads the
#    operator's config SIGKILLs the live gateway's downstream children.
#
#    4a. Build a throwaway HOME. This is the load-bearing isolation step —
#        `--config` is ADDITIVE, so without it the operator's ~/.mcp.json and
#        ~/.claude/.mcp.json still load and still populate the kill fingerprints.
export PG_HOME=$(mktemp -d /tmp/pg-ec3-home.XXXXXX)
mkdir -p "$PG_HOME/.pmcp-lock" "$PG_HOME/project"
WHEEL=$(ls "$PWD"/dist/*.whl | head -1)
#        NO "env" block. A local stdio server's env is passed VERBATIM —
#        ${VAR} is NOT expanded (src/pmcp/config/loader.py:640-650), so
#        {"PANGRAM_API_KEY": "${PANGRAM_API_KEY}"} is a dead literal that
#        merely clobbers the inherited value. The credential reaches the child
#        through the gateway's own environment, which `op run` populates in 4c.
cat > "$PG_HOME/mcp.json" <<JSON
{"mcpServers": {"pangram": {
  "command": "uvx",
  "args": ["--from", "$WHEEL", "pangram-mcp"]
}}}
JSON
#        Note the args tuple ("--from", "<wheel>", "pangram-mcp") deliberately
#        does NOT match the live gateway's ("pangram-mcp",) fingerprint.
cat > "$PG_HOME/policy.yaml" <<'YAML'
# NO denylist. `is_server_allowed` checks the denylist FIRST and returns False
# on a match (src/pmcp/policy/policy.py:104-117), so a `denylist: ["*"]` here
# would deny `pangram` itself before the allowlist is ever consulted and
# gateway.connect_server would return policy-denied. A non-empty allowlist
# already excludes every server not named in it — that is the whole mechanism.
servers:
  allowlist: ["pangram"]     # ONLY pangram may start; everything else excluded
tools:
  allowlist: ["pangram::analyze", "gateway::*"]
YAML

#    4b. Identify the LIVE gateway by PID and snapshot ITS OWN children. Do not
#        snapshot all `uvx` processes — step 4c deliberately starts one, so a
#        whole-system diff could never come back clean. Scoping to children of
#        LIVE_PID excludes our test child automatically (ours is a child of this
#        shell, not of the live gateway).
LIVE_PID=$(ss -ltnpH 'sport = :3344' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$LIVE_PID" ] || { echo "ABORT: nothing listening on :3344 — start the live gateway first"; exit 1; }
echo "live gateway pid: $LIVE_PID"
#        `|| true` is required: pgrep exits 1 when there are zero children, which
#        would abort the run on a live gateway that happens to have none started.
pgrep -P "$LIVE_PID" | sort > "$PG_HOME/live-children-before.txt" || true
echo "live gateway children before: $(wc -l < "$PG_HOME/live-children-before.txt")"

#    4c. Install the CURRENTLY PUBLISHED 1.x gateway into its own venv and
#        invoke its binary DIRECTLY. Do not use `uv run`/`uvx` here: HOME is
#        redirected, so uv would look for an absent cache under $PG_HOME and
#        may resolve something other than the published artifact.
uv venv --python 3.12 /tmp/pg-gw
VIRTUAL_ENV=/tmp/pg-gw uv pip install 'pmcp<2.0.0'
/tmp/pg-gw/bin/pmcp --version    # record which 1.x is under test

#        Two ordering rules in the line below, both load-bearing:
#        (a) The op:// reference must be SUPPLIED to `op run` as an env var.
#            `op run` only substitutes references it finds in the environment or
#            an --env-file; naming the vault path in prose provisions nothing.
#        (b) HOME is redirected INSIDE `op run --`, applying only to the spawned
#            gateway. Wrapping `op` itself in the throwaway HOME would make the
#            1Password CLI lose its own session and fail to authenticate.
PANGRAM_API_KEY="op://Consiliency Deploy Secrets/PANGRAM_API_KEY/credential" \
  op run -- env HOME="$PG_HOME" /tmp/pg-gw/bin/pmcp \
      --transport http --host 127.0.0.1 --port 3399 -l info \
      --lock-dir "$PG_HOME/.pmcp-lock" \
      --project "$PG_HOME/project" \
      --config "$PG_HOME/mcp.json" \
      --policy "$PG_HOME/policy.yaml" > "$PG_HOME/gw.log" 2>&1 &

#        Capture the test gateway's PID and guarantee teardown. This process
#        holds a live credential in its environment; it must never outlive the
#        check, including on an early exit.
TEST_GW_PID=$!
trap 'kill "$TEST_GW_PID" 2>/dev/null; wait "$TEST_GW_PID" 2>/dev/null' EXIT INT TERM
echo "test gateway pid: $TEST_GW_PID (will be terminated on exit)"

#        TRIPWIRE — abort if isolation leaked. Assert on the KILL SET, which is
#        exactly `resolution.lazy_configs + resolution.eager_configs`
#        (src/pmcp/server.py:604), so eager+lazy measures the blast radius
#        directly. It must be our ONE fixture server.
#
#        Do NOT assert on a total server count. The shipped manifest contributes
#        ~106 entries and they surface as `policy_denied` — a three-digit number
#        there is the CORRECT output for a properly isolated run, so a
#        "more than N servers" tripwire fires on a healthy run and blocks it.
sleep 20
grep -o 'Startup policy summary:.*' "$PG_HOME/gw.log" | tail -1
python3 - "$PG_HOME/gw.log" <<'PY'
import re, sys
log = open(sys.argv[1], errors="replace").read()
m = None
for m in re.finditer(r"Startup policy summary: eager=(\d+), lazy=(\d+)", log):
    pass
if m is None:
    sys.exit("ABORT: no startup policy summary in the log — the gateway never started")
eager, lazy = int(m.group(1)), int(m.group(2))
if eager + lazy != 1:
    sys.exit(f"ISOLATION LEAK: kill set is eager={eager} + lazy={lazy} = {eager + lazy}, "
             "expected exactly 1. Kill this gateway and verify the live one now.")
print(f"isolation ok — kill set is exactly 1 (eager={eager} lazy={lazy})")
PY

#        Then run the asserting client (NOT prose — it exits non-zero on failure).
#        Use the 1.x interpreter so the client half is genuinely 1.x.
set +e
/tmp/pg-client-1x/bin/python tests/ec_pg3_gateway_check.py http://127.0.0.1:3399/mcp
EC_PG3_RC=$?
set -e
echo "ec_pg3_gateway_check exit: $EC_PG3_RC"
#        Route STRICTLY on the exit code — see the fault table in SL-2:
#          0 pass | 2 compatibility -> SL-3.3 amendment | 3 fix the checker | 4 fix op run
#        Only a 2 may amend the roadmap. Never amend on a 3 or a 4.

#    4d. Prove the LIVE gateway survived — SAME pid and SAME children. Roadmap
#        cross-cutting principle 3. A listener on :3344 is not enough: a restart
#        would also show a listener. Compare identity, not liveness.
NOW_PID=$(ss -ltnpH 'sport = :3344' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$NOW_PID" ] || { echo "REGRESSION: nothing on :3344 — the live gateway died"; exit 1; }
[ "$NOW_PID" = "$LIVE_PID" ] || { echo "REGRESSION: :3344 pid changed $LIVE_PID -> $NOW_PID"; exit 1; }
pgrep -P "$LIVE_PID" | sort > "$PG_HOME/live-children-after.txt" || true
diff "$PG_HOME/live-children-before.txt" "$PG_HOME/live-children-after.txt" \
  && echo "live gateway intact: same pid, same children"

#    4e. Tear down the test gateway explicitly (the trap is the backstop).
kill "$TEST_GW_PID" 2>/dev/null; wait "$TEST_GW_PID" 2>/dev/null; trap - EXIT INT TERM
pgrep -f 'port 3399' || echo "test gateway terminated"

#    An EC_PG3_RC of 2 is the roadmap's Assumption 6 breaking. STOP and report;
#    do not work around it. SL-3.3 carries the amendment.
#    A 4d failure means isolation leaked — restore the live gateway before
#    anything else, then treat it as a blocker, not a compatibility result.

# 5. Full suite, lint, types.
uv sync --extra dev
uv run pytest -q && uv run ruff check . && uv run mypy src

# 6. EC-PG-5 — RELEASE. Operator action; run only after steps 1-5 are green
#    and the phase has merged to main.
git tag v0.1.4 && git push origin v0.1.4    # triggers release.yml -> PyPI

#    6a. WAIT for the release workflow to finish. Pushing the tag proves nothing;
#        trusted publishing can fail after a green build job.
#        Select the run by the TAG'S HEAD SHA, not `-L1`. Immediately after a
#        push the new run may not be listed yet, so `-L1` can silently select an
#        EARLIER successful release run and "prove" a release that never ran.
#        Capture the id ONCE and reuse it for both watch and job assertions.
TAG_SHA=$(git rev-list -n1 v0.1.4)
RUN_ID=""
until [ -n "$RUN_ID" ]; do
  RUN_ID=$(gh run list --workflow=release.yml -L 50 --json databaseId,headSha \
             -q "[.[] | select(.headSha==\"$TAG_SHA\")] | .[0].databaseId // empty")
  [ -n "$RUN_ID" ] || { echo "waiting for a release.yml run at $TAG_SHA..."; sleep 10; }
done
echo "release run for $TAG_SHA: $RUN_ID"
gh run watch "$RUN_ID" --exit-status
gh run view "$RUN_ID" --json jobs -q '.jobs[] | "\(.name): \(.conclusion)"'
#        expect build, publish and github-release all `success`.

#    6b. WAIT for PyPI to actually serve 0.1.4. The workflow succeeding and the
#        index serving the artifact are different events.
until python3 -c "
import json,sys,urllib.request
d=json.load(urllib.request.urlopen('https://pypi.org/pypi/pangram-mcp/json'))
sys.exit(0 if '0.1.4' in d['releases'] and d['releases']['0.1.4'] else 1)
"; do echo "waiting for PyPI to serve 0.1.4..."; sleep 15; done

#    6c. Cold cache means an EMPTY cache dir, not an evicted package. Then a
#        real client asserts the version — `uvx pangram-mcp` alone just blocks
#        on stdio forever and establishes nothing.
export UV_CACHE_DIR=$(mktemp -d /tmp/pg-coldcache.XXXXXX)
/tmp/pg-client-1x/bin/python - <<'PY'
import asyncio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main() -> int:
    params = StdioServerParameters(
        command="uvx", args=["pangram-mcp"],
        env={**os.environ, "PANGRAM_API_KEY": "unused-not-a-real-secret"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            assert init.serverInfo.name == "pangram", init.serverInfo
            assert init.serverInfo.version == "0.1.4", \
                f"published serverInfo.version is {init.serverInfo.version!r}, expected '0.1.4'"
            names = {t.name for t in (await s.list_tools()).tools}
            assert names == {"analyze"}, names
            print("EC-PG-5 PASS — cold-cache uvx served 0.1.4 with the analyze tool")
            return 0

sys.exit(asyncio.run(main()))
PY
rm -rf "$UV_CACHE_DIR"

# 7. EC-PG-4 — DEFERRED until roadmap phase P2 lands a 2.x pmcp.
#    Repeat step 4 against the 2.x gateway, from PyPI rather than a local wheel:
#          gateway.connect_server pangram   -> online
#          gateway.invoke pangram::analyze  -> live classification
#    Record the result against this phase; it does not block the release.
```

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pangram_mcp/server.py`, `pyproject.toml`, `src/pangram_mcp/__init__.py`, `tests/`, `.github/workflows/test.yml`
- evidence paths: `plans/phase-plan-v11-PG.md`
- redaction posture: `metadata_only`
- downstream handling: `none` — unless EC-PG-3 fails, in which case SL-3.3 escalates to a `roadmap_amendment` against `specs/phase-plans-v11.md` in the pmcp repo and this decision is superseded
