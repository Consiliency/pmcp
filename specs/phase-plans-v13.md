# PMCP phase plans v13 — trust boundaries

> **Revision 2 (2026-09-08).** Boarded 3x DISAGREE. Seven real defects, fixed below.
> The load-bearing ones: the DAG claimed CONSENT and PKGID "share no files" while both
> list `policy/policy.py`; an unapproved project overlay that *adds* a server (rather
> than replacing one) slipped the seam between CONSENT and PKGID; and IF-0-TRUST-1
> froze the record shape but not **where the store lives or who may write it** — a
> checkout-writable store lets a repository ship its own approval and makes
> absence-is-not-assent meaningless.
>
> **One verdict is rejected.** Two seats judged this as a pre-merge implementation
> review — "no implementation evidence, exit criteria unchecked, missing evidence is
> `contract_bug`". A roadmap is a plan; unchecked exit criteria are its purpose, and the
> `spec_delta_closeout` evidence rule binds *phase execution*, not the roadmap artifact.
> Their structural findings are folded in; the evidence objection is not.


## Context

PMCP is a local-first MCP gateway that brokers untrusted third-party servers on
behalf of a semi-trusted, prompt-injectable agent. The 2026-09-01 codebase review
(`plans/codebase-review-2026-09-01.md`) found that the remaining risk has moved up
a level: not bugs inside functions, but the **trust boundaries between the agent,
repository-supplied configuration, and the host**.

Four findings look separate and are one question — *what may introduce code the
gateway will execute or actions it will take, and who consents?*

| Finding | Severity | Substance |
|---|---|---|
| **S-01** | HIGH | `register_discovered_server` accepts any npm package; `provision` runs it with `npx -y`. Policy checks the **agent-chosen server name**, not the package. Reproduced: allowlisting `internal-approved-tool` executes `npx -y totally-arbitrary-evil-package`. |
| **S-03** | MEDIUM (HIGH shared/CI) | A checkout's `.pmcp/manifest.yaml` replaces any shipped server's command wholesale; `.mcp.json` is trusted the same way. "Clone a repo and use GitHub" becomes code execution. |
| **S-11** | MEDIUM | A project `.mcp-gateway-policy.yaml` **silently shadows** the operator's global policy — first match wins, project paths first, default allow-all. |
| **S-04** | MEDIUM | `gateway.submit_feedback` posts agent-authored text to a public repo using the ambient `GITHUB_TOKEN`. |

Each has a local patch. Patching them separately would produce four inconsistent
consent mechanisms, and S-11's own fix note already says it "ties into S-03's trust
store". This roadmap builds **one** trust model and routes all four through it.

## Architecture North Star

```
        agent (prompt-injectable)        repository checkout        operator
              |                                 |                      |
              | register / provision            | .pmcp/manifest.yaml  | pmcp trust
              | submit_feedback                 | .mcp.json            | pmcp approve
              v                                 v  policy.yaml         v
   +---------------------------------------------------------------------+
   |  TRUST  identity + provenance + approval record (the frozen core)    |
   |    package identity: (registry, name, resolved version, integrity)   |
   |    source provenance: (abs path, content hash, scope, decision)      |
   +---------------------------------------------------------------------+
        |                        |                            |
        v                        v                            v
   PKGID: provisioning     CONSENT: project-scoped      EGRESS: outbound acts
   binds to package        config gated + policy        with operator identity
   identity, not a name    narrowing-only               explicit + preview-first
        \                        |                            /
         \                       v                           /
          +--------------> SEAL: documented model + ---------+
                           adversarial end-to-end proof
```

## Assumptions (fail-loud if wrong)

1. Downstream tool output is untrusted and the agent is prompt-injectable. If the
   agent is trusted, most of this roadmap is unnecessary.
2. The operator is a human who can be asked once and remembered — an approval
   store is meaningful. If PMCP runs fully unattended, the default must be deny,
   not prompt.
3. `provision` consults the shipped manifest **before** the discovered registry
   (`handlers.py:4262-4266`), so a discovered entry cannot shadow a manifest name.
   Verified in the review; if it regresses, S-01's blast radius grows.
4. The npm identity machinery from #195 (`manifest/npm_resolver.py`,
   `manifest/version_checker.py`) can resolve a package identity without executing
   it. TRUST extends that work rather than starting a new subsystem.
5. Existing operators keep working configurations. A default-deny that silently
   breaks a working setup is a failed phase, not a strict one.

## Non-Goals

- Sandboxing downstream servers, or any container/namespace isolation.
- Sanitising shell-exported secrets — deliberate, documented in SECURITY.md, and
  unchanged by this roadmap (#229 closed the plain-`.env` path only).
- Auditing PMCP's own supply chain (that is #217/#228 territory).
- A general policy engine. Policy gains package identifiers and narrowing
  semantics; it does not become a rules language.
- Retroactive approval of already-provisioned servers beyond a one-time migration.

## Cross-Cutting Principles

- **Fail closed on the new gates, fail open on nothing.** Every gate added here
  refuses on ambiguity, matching #202 and #217.
- **Identity, not labels.** A decision binds to what will execute (package,
  file content hash), never to a name the agent supplied.
- **Consent is recorded, revocable, and legible.** An approval is a durable record
  an operator can list and revoke — not a flag buried in a config.
- **A narrowing-only rule for project scope.** Repository-supplied configuration
  may restrict, never widen.
- **Every refusal names the decision and how to grant it.** A blocked action must
  print the exact `pmcp` command that would allow it.
- **No behaviour change without a CHANGELOG entry**, and no security claim in
  SECURITY.md that the tests do not prove.

## Phases

### Phase 1 — Trust primitives (TRUST)

**Objective**
Freeze the two data contracts every other phase codes against: a package identity
tuple and a source-provenance/approval record, with a store to read and write them.

**Exit criteria**
- [ ] EC-TRUST-1 — a package identity `(registry, name, resolved_version, integrity)` is resolvable for any npm spec without executing it, reusing `manifest/npm_resolver.py`; proven by a test that resolves a real spec offline from a fixture.
- [ ] EC-TRUST-2 — a source provenance record `(absolute_path, content_sha256, scope, decision, recorded_at)` round-trips through the store; a file whose content changes after approval reads back as **not approved**.
- [ ] EC-TRUST-3 — the store refuses to answer "approved" for any path it has no record of; absence is never assent.
- [ ] EC-TRUST-4 — the full CLI surface exists and is covered by tests: `pmcp trust approve <path>` (records), `pmcp trust list`, `pmcp trust revoke <path>`. The **approve** verb is what every downstream refusal message names, so omitting it would leave those messages naming a command that does not exist.
- [ ] EC-TRUST-5 — the store lives **outside any repository** (user scope, e.g. `~/.config/pmcp/`), and a store path inside the current checkout is refused at startup. A repository that ships its own approval record must not be believed; without this, absence-is-not-assent is decorative.
- [ ] EC-TRUST-6 — `is_approved` answers about **bytes, not paths**: the caller passes the content it is about to apply and the store hashes that, so a file swapped between check and use is not approved by a stale decision.

**Scope notes**
Decompose into 2 lanes with disjoint files: **lane A** owns the provenance/approval
store and its CLI surface; **lane B** owns the package-identity resolver adapter over
the existing #195 machinery. Both publish their shapes on day 1 so CONSENT and PKGID
can start against the contract rather than the implementation. No caller is wired in
this phase — that is deliberate, and it is why this phase is small.

**Non-goals**
Wiring any consumer; changing provisioning or policy behaviour.

**Key files**
- `src/pmcp/trust_store.py` (new)
- `src/pmcp/manifest/npm_resolver.py`
- `src/pmcp/cli.py`
- `tests/test_trust_store.py` (new)

**Depends on**
- (none)

**Produces**
- IF-0-TRUST-1
- IF-0-TRUST-2

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/trust_store.py`, `src/pmcp/cli.py`
- evidence paths: `tests/test_trust_store.py`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- missing or malformed evidence routes to `blocker_class=contract_bug` (non-human).

### Post-execution amendments — TRUST (2026-09-08)

Recorded by SL-docs after SL-1, SL-2 and SL-3 landed and were merged. Both freeze
gates shipped with exactly the signatures they declared, and no exit criterion had to
be weakened — nothing below reopens a contract. What each gate got wrong is a detail
it left implicit, and in both cases the phase that has to consume the gate is the one
that pays. The third item is a defect in `plans/phase-plan-v13-TRUST.md`, not in this
roadmap, recorded here because it is the file a later planner reads.

1. **IF-0-TRUST-1 froze a `"denied"` decision that no shipped command can write.**
   The gate fixes `decision` as the closed vocabulary `{"approved", "denied"}` and
   requires `is_approved` to distinguish a `"denied"` record from an absent one, and
   `src/pmcp/trust_store.py` implements precisely that: `DECISIONS` rejects any other
   value at `record` time rather than storing it, and `is_approved` returns `False`
   for a `"denied"` record even when the digest matches. But EC-TRUST-4 froze the CLI
   as exactly `approve` / `list` / `revoke`, and `run_trust` (`src/pmcp/cli.py`)
   dispatches those three and no more — so no operator surface constructs
   `decision="denied"`, and `revoke` *deletes* the record rather than denying it.
   **This is not a security hole.** Absence already refuses, so the reachable
   behaviour is a strict subset of the frozen behaviour; the gap is a reserved
   vocabulary value with no writer, not a permissive default. It is recorded because
   a downstream lane reading IF-0-TRUST-1 would reasonably plan a "the operator
   denied this" path and find nothing to call. **CONSENT and PKGID must treat
   `"denied"` as reserved, not as available.** Whichever phase first needs an
   explicit deny owns adding the verb — with its own CHANGELOG entry — or SEAL
   documents the value as reserved when it writes the trust-model section of
   `SECURITY.md`. TRUST did neither on purpose: shipping a verb whose records no
   caller reads would have broken this phase's "wire no caller" rule.

2. **IF-0-TRUST-2 froze a synchronous signature over blocking network I/O. Read this
   before PKGID SL-1 is executed.** The gate declares
   `resolve_package_identity(spec: str) -> PackageIdentity | None`, and the shipped
   code honours it: `src/pmcp/manifest/package_identity.py:120` calls
   `_OPENER.open(request, timeout=_FETCH_TIMEOUT)` over `urllib`, with
   `_FETCH_TIMEOUT = 10.0` (`:58`). Every other registry lookup in this codebase is a
   coroutine — `get_npm_version`, `get_pypi_version`, `get_cargo_version` and
   `get_docker_version` (`src/pmcp/manifest/version_checker.py:1375-1530`) are all
   `async def` over `aiohttp.ClientSession` — so the house pattern a PKGID lane will
   assume by analogy is the one this function does not follow. That matters because
   PKGID SL-1 owns `src/pmcp/tools/handlers.py` and its EC-PKGID-5 decision resolves
   and pins **at registration**, inside `async def register_discovered_server`
   (`handlers.py:5513`). Calling `resolve_package_identity` directly from that
   coroutine blocks the gateway's event loop for up to ten seconds per registration —
   no other downstream call is served meanwhile, and a slow or unreachable registry
   turns one agent's registration into a gateway-wide stall.
   The signature stays frozen and PKGID must **not** change it: that file belongs to
   TRUST and an async rewrite would edit a merged phase's contract. The fix is
   caller-side and belongs in PKGID's plan — hand the call to a thread
   (`asyncio.get_running_loop().run_in_executor(None, resolve_package_identity, spec)`
   or `anyio.to_thread.run_sync`) and give the enclosing handler its own timeout
   rather than treating the 10 s socket timeout as the bound, since a redirect chain
   can spend that timeout more than once. `plans/phase-plan-v13-PKGID.md` does not
   mention any of this; it was found only by reading TRUST's merged code, after that
   plan was written.

3. **The TRUST phase plan names frozen tests in one lane and behaviours in another,
   and says nowhere that it is doing so.** In `plans/phase-plan-v13-TRUST.md`, SL-1.1's
   "Tests owned" cell reads "**exactly these names**, because the acceptance criteria
   address them individually" and lists eight `test_*` identifiers, which the
   EC-TRUST-2/3/5/6 criteria then cite by name. SL-2.1's cell for the same column
   reads "identity resolves offline from a fixture; an unresolvable spec returns
   `None` rather than raising; no subprocess is spawned" — behaviour descriptions, not
   names. The asymmetry is defensible in substance (EC-TRUST-1 proves itself by naming
   the whole file, so there was no name to freeze) but it is stated nowhere, and a lane
   brief written from the plan read the two cells as carrying the same instruction. A
   phase plan that mixes frozen test names with behaviour descriptions must say which
   kind each cell is, in the cell — the reader cannot infer it from the column header,
   which is identical for both.

### Phase 2 — Project-scoped configuration requires consent (CONSENT)

**Objective**
Gate every repository-supplied configuration source through the trust store, and
make a project policy able only to narrow the operator's policy.

**Exit criteria**
- [ ] EC-CONSENT-1 — an unapproved `.pmcp/manifest.yaml` does **not** replace a shipped server's command; the shipped definition is used and the skip is logged at WARNING naming the exact `pmcp trust` command.
- [ ] EC-CONSENT-2 — an unapproved project `.mcp.json` is not applied, on the same terms.
- [ ] EC-CONSENT-3 — a project policy may only **narrow**: a project file that allows a server the user policy denies leaves it denied; proven by a test that fails on today's first-match-wins behaviour.
- [ ] EC-CONSENT-4 — approving a file, then editing it, revokes the approval automatically (content hash, not path).
- [ ] EC-CONSENT-5 — an operator with **no project files** sees byte-identical behaviour to today, proven by the suites that contain no project-scoped case passing unmodified. **WAS WRONG (rev 2):** it said "the existing config/policy suites passing unmodified", which is unsatisfiable — `tests/test_manifest_overlay.py::test_project_overrides_user_overrides_shipped` asserts that an *unapproved* overlay replaces a shipped command, `tests/test_config_loader.py` carries many project `.mcp.json` cases, and `tests/test_policy_fail_open.py` is entirely about discovered project policy. Those three suites encode the behaviour this phase changes and are updated by their owning lanes; demanding they pass unmodified would have forced an implementer to weaken the gate until the old assertions held.
- [ ] EC-CONSENT-6 — an unapproved overlay that **adds a new server** rather than replacing a shipped one is also not applied, and the added server is **not** treated as manifest-backed downstream. Without this the add path slips the seam: CONSENT only refuses replacement, PKGID's default-deny exempts manifest-backed servers, and an unapproved package executes through the gap between two phases that each look complete.
- [ ] EC-CONSENT-7 — an unapproved project `.mcp-gateway-policy.yaml` is **not applied at all** (not merely narrowed), on the same trust terms as the manifest and `.mcp.json`. EC-CONSENT-3 governs what an *approved* project policy may do; this governs whether it is read.

**Scope notes**
Decompose into 3 lanes with disjoint files: **lane A** owns the manifest overlay
walk-up (`manifest/loader.py`), **lane B** owns `.mcp.json` (`config/loader.py`),
**lane C** owns policy precedence and intersection (`policy/policy.py`). The three
consume IF-0-TRUST-1 and never write to each other's files. Lane C is the one with a
behaviour change an existing operator could feel — sequence its CHANGELOG note first.

**Non-goals**
Package identity (PKGID owns it); any change to user- or env-scoped sources.

**Key files**
- `src/pmcp/manifest/loader.py`
- `src/pmcp/config/loader.py`
- `src/pmcp/policy/policy.py`
- `tests/test_project_source_consent.py` (new)

**Depends on**
- TRUST

**Produces**
- IF-0-CONSENT-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/manifest/loader.py`, `src/pmcp/config/loader.py`, `src/pmcp/policy/policy.py`
- evidence paths: `tests/test_project_source_consent.py`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- missing or malformed evidence routes to `blocker_class=contract_bug` (non-human).

### Post-execution amendments — CONSENT (2026-09-11)

Recorded by SL-docs after SL-1, SL-2, SL-3 and SL-4 landed, were merged, and a
single-writer repair pass ran over the assembled branch. Every exit criterion
shipped as written, and both freeze gates shipped with one recorded deviation
between them (item 8); nothing below reopens a contract, and no criterion had to
be weakened. What follows is what the plan and this roadmap got wrong about the
*work*, and two findings a later phase inherits whether or not it reads them.
Items 1 and 2 are the ones PKGID and SEAL pay for if they are not read.

1. **EC-CONSENT-5's own defect note undercounts the damage, and the phase learned
   the repair rule the hard way.** `plans/phase-plan-v13-CONSENT.md` records
   (correctly) that the criterion's "passing unmodified" proof clause is
   unsatisfiable, and names three suites that assert an *unapproved* project
   source applies: `tests/test_manifest_overlay.py`, `tests/test_config_loader.py`,
   `tests/test_policy_fail_open.py`, assigned to SL-2/3/4. **Three was the
   lane-owned subset, not the blast radius.** Measured on the assembled branch:
   the four lanes together broke **16 tests across six further files**, none of
   them owned by any lane — `tests/test_secrets_command.py` (5),
   `tests/test_credential_optionality_e2e.py` (5), `tests/test_registry.py` (2),
   `tests/test_credential_gates_startup.py` (1), `tests/test_tools.py` (1),
   `tests/test_phase4_e2e.py` (1). A grep for test files creating a
   project-scoped source returns **18**, of which only 3 were lane-owned. Fifteen
   of the sixteen were one mechanical class — a project fixture written without an
   approval — repaired by recording the approval next to the fixture write; the
   sixteenth was item 3 below wearing a disguise.
   **The rule this phase arrived at, and which the next phase should plan for up
   front: the owning lane repairs its OWN suite, and all cross-cutting fallout is
   repaired in ONE single-writer pass after the lanes merge.** Not out of
   tidiness — a file like `tests/test_tools.py` can break from more than one
   lane's gate, so concurrent lanes editing it lose each other's updates, and the
   interaction set is by construction invisible to any single lane working in an
   isolated worktree. A phase that introduces a gate in front of an
   already-widely-fixtured source must budget that pass as work, not treat it as
   an overrun.

2. **The acceptance criteria are structurally blind to a gate-then-reread (TOCTOU)
   regression — the one defect the phase exists to prevent.** IF-0-CONSENT-1's
   central rule is that `read_and_gate` reads the path **exactly once** and the
   caller parses the bytes it was handed; re-opening the path after gating means
   parsing bytes nobody approved. No required test in EC-CONSENT-1 through -7 can
   see a violation: in every one of them the bytes on disk and the bytes that were
   gated are identical, so a caller that re-opens the file observes nothing
   different. Measured by SL-2 — mutating its loader to re-read after gating failed
   exactly **one** test, an extra one the lane had added beyond the eight its plan
   row required, and nothing else in the suite. All three consuming lanes did add
   an equivalent guard (`test_the_parsed_bytes_are_the_gated_bytes_not_a_second_read`,
   `test_an_approved_project_mcp_json_is_read_exactly_once`, and SL-4's, caught by
   three of its tests), but by independent judgement, not because anything asked
   them to. **Every future consumer of `read_and_gate` must be REQUIRED to prove
   the one-read property, by a named test in its acceptance criteria** — PKGID and
   SEAL included. A criterion that only checks the gate's verdict leaves the
   window the gate was built to close entirely untested.

3. **Phase assembly created an import cycle that no lane and no test run could
   see.** Not fallout — a genuine defect introduced by the *merge*, and the
   lesson generalises past this phase. SL-3 made `config/loader.py` import
   `project_consent`, which imports `trust_store`, which already imported
   `config.loader` at module scope:
   `config.loader -> project_consent -> trust_store -> config.loader`. Each file
   imported cleanly on its own branch; the cycle only closes with all three
   present, so no lane could have found it, and `import pmcp.config.loader` in a
   clean interpreter raised `ImportError` on the assembled branch — a hard failure
   on a core module. **The test suite actively concealed it**: `tests/conftest.py`
   imports `trust_store` at collection time, so the cycle is already resolved
   before any test touches `config.loader`. It surfaced in exactly one place, a
   test that spawns a subprocess, and presented as a process-reaping symptom
   ("a hung probe leaves grandchildren alive") with no visible connection to
   imports. Fixed by making the `find_project_root` import call-time in
   `trust_store`; guarded by a regression test that spawns a **real subprocess per
   module**, because an in-process import assertion proves nothing once conftest
   has resolved the cycle. **Any phase that merges lanes which add cross-module
   imports should run a clean-interpreter import of each touched module as an
   assembly step.** A green suite is not evidence that the package imports.

4. **The evidence path `tests/test_project_source_consent.py` does not exist and
   was never creatable.** A single file cannot be disjointly owned by three
   concurrent lanes. Phase 2's **Key files** entry and its **spec closeout
   policy → evidence paths** entry should both be read as naming these four:
   `tests/test_project_consent_gate.py` (the gate itself, SL-1),
   `tests/test_project_source_consent_manifest.py` (SL-2),
   `tests/test_project_source_consent_config.py` (SL-3) and
   `tests/test_project_source_consent_policy.py` (SL-4). SEAL's closeout should
   collect all four.

5. **The phase decomposed into four implementation lanes, not the three this
   roadmap's Scope notes prescribe** (five with SL-docs). The Scope notes assign
   one loader per lane and assume the three consume IF-0-TRUST-1 directly. They
   cannot: IF-0-CONSENT-1's "the single call every loader uses" is itself a new
   file all three loaders import, so it is a lane — SL-1, `src/pmcp/project_consent.py`
   — and the only DAG root. Lanes A/B/C became SL-2/SL-3/SL-4 and open together
   once SL-1 lands. A roadmap that names a shared interface as a phase output
   should count it as a lane.

6. **Two IF-0-TRUST-1 assumptions this phase stated up front proved wrong, and
   both were harmless only by luck.** (a) The plan assumed `pmcp trust approve`
   records `scope="project"`. It records `scope="user"` — `_TRUST_SCOPE = "user"`
   at `src/pmcp/cli.py:2489`, used at `:2515`. Harmless because `is_approved(path,
   content)` takes no scope argument and never consults it, but every lane had
   been briefed to assert on a value that was never there; all four briefs were
   corrected to forbid asserting on `scope` at all. The two approval paths now
   disagree in opposite directions — the test helper records `"project"`, the
   shipped CLI records `"user"` — and nothing reads either. **Pick one before
   `scope` acquires a consumer**; today it is descriptive metadata, and the first
   phase to make it load-bearing inherits a field with two conflicting writers.
   (b) TRUST gap 5 ("no frozen test seam for the store location") is resolved, but
   not by the monkeypatch the plan assumed. Patching
   `pmcp.trust_store.trust_store_path` would break TRUST's own suite:
   `tests/test_trust_store.py` binds the name at import time, so its direct calls
   would use the original while `record`/`is_approved` resolve the patched module
   global — the two would disagree about where the store lives. The published seam
   is an autouse HOME redirect in `tests/conftest.py` (SL-1), which is what TRUST's
   tests already use and which keeps the store's checkout-residency check live
   rather than stubbing it. No cross-phase write into `src/pmcp/trust_store.py` was
   needed.

7. **EC-CONSENT-2 undercounts the project `.mcp.json` read sites: there are five,
   not four.** The plan groups them as consumers of `_iter_config_source_paths`;
   `registry_allow_private_from_config` is not one — it builds its own candidate
   list and appends the project `.mcp.json` directly. An implementer working from
   the count leaves that reader fail-open, which is the `allowPrivateRegistry`
   flag, set by an unapproved repository file. SL-3 routed all five through a
   single helper, `_gate_project_config`, so the omission is structurally
   impossible rather than merely tested for. **Prefer one choke point to N gates**
   wherever a phase gates a source with more than one reader.

8. **Accepted deviation from IF-0-CONSENT-1: `reason` is a `Literal`, not a bare
   `str`.** `ConsentReason = Literal["approved", "no_record", "content_changed",
   "unreadable"]`. `Literal` is a subtype of `str`, so every downstream `reason:
   str` annotation and comparison still type-checks (verified). What it buys,
   precisely: mypy rejects constructing a `ConsentDecision` with a reason outside
   the vocabulary under this repo's current settings. What it does **not** buy:
   catching a typo'd `==` comparison, which needs `--strict-equality`, and this
   repo does not enable it. Recorded so the next reader does not overstate the
   guarantee.

9. **Two UX findings handed to SEAL, both out of scope here.** (a) The frozen
   remediation string is unquoted, so a project path containing a space yields
   `pmcp trust approve /path/with a space/.mcp.json` — a command that is not
   runnable as printed. Not deviated from the freeze; SEAL should decide the
   quoting rule. (b) `set_startup_policy` stays ungated by design — it is an
   operator-initiated *write*, not passive trust — but it rewrites the project
   `.mcp.json` through `_atomic_write_json`, which changes its bytes and therefore
   silently revokes any approval the operator recorded for it. `pmcp startup add
   --source project` invalidates the operator's own trust record and they get a
   refusal on the next startup. Arguably correct (the bytes did change), but a
   trap; it sits next to the existing "no bulk approve" note.

10. **`plans/phase-plan-v13-CONSENT.md`'s Context line references have gone stale**
    as a result of this phase's own work: `_load_overlay_file` no longer opens the
    path, so `manifest/loader.py:696`, `:801` and `:806-809` no longer point where
    the plan says (`servers.update(overlay_servers)` is now at `:848`). Expected
    for a merged phase plan, recorded because the plan is still the document a
    reviewer reads to check the criteria.

11. **Five lane briefs, five factual errors — a plan-authoring lesson, in the
    genre of TRUST amendment 3.** Every brief written from these plans by hand
    carried at least one error the lane caught by reading the plan instead of
    trusting the brief: TRUST SL-2 (claimed SL-2.1 freezes test *names*; it lists
    descriptions — TRUST amendment 3); TRUST SL-4 (claimed the docs-catalog helper
    was absent; it is installed); CONSENT SL-2, SL-3 and SL-4 (each transcribed
    one fewer node id than its plan row lists — SL-4's omission was the redaction
    test, the redaction rule's *only* falsifier, which would have shipped that rule
    unproven). The mitigation that worked, and which every future brief should
    carry verbatim: **"trust the document over this brief, and tell me when they
    disagree."** The structural fix is to stop hand-transcribing node ids into
    briefs and generate them from the plan row.

### Phase 3 — Provisioning binds to package identity (PKGID)

**Objective**
Stop the agent choosing what executes. Bind provisioning decisions to a resolved
package identity and put discovered-package installation behind an operator opt-in.

**Exit criteria**
- [ ] EC-PKGID-1 — the review's reproduction fails closed: registering `internal-approved-tool` with package `totally-arbitrary-evil-package` and provisioning it does **not** exec `npx -y totally-arbitrary-evil-package`; the refusal names the package and the command that would approve it.
- [ ] EC-PKGID-2 — policy can allow or deny **package identifiers**, and `provision` checks the package, not only the server name.
- [ ] EC-PKGID-3 — discovered-package provisioning is default-deny; an operator opt-in (config flag or recorded approval) is required, and a manifest-backed server is unaffected.
- [ ] EC-PKGID-4 — the exact argv is logged at WARNING before every install spawn.
- [ ] EC-PKGID-5 — a registration without a resolvable version is refused, or the version is resolved and recorded at registration and passed as `pkg@<version>`; whichever is chosen is asserted by test.
- [ ] EC-PKGID-6 — servers already provisioned before this phase keep working: a one-time migration records approvals for them, or they are grandfathered by an explicit recorded decision. Assumption 5 says a default-deny that silently breaks a working setup is a failed phase; this criterion is the phase that owns it, and no other phase did.

**Scope notes**
Decompose into 3 lanes with disjoint files: **lane A** owns the register/provision
gate in `tools/handlers.py`, **lane B** owns policy package identifiers in
`policy/policy.py` and `types.py`, **lane C** owns argv logging plus version pinning
in `manifest/installer.py`. Lane A is the single-writer risk — `handlers.py` is large
and touched by other work; serialise any other writer against it. Parallel-safe with
CONSENT: no shared file, and both depend only on TRUST.

**Before executing lane A, read TRUST → Post-execution amendments item 2.**
`resolve_package_identity` is synchronous and performs blocking network I/O with a
10 s socket timeout; `register_discovered_server` is a coroutine, so calling it
directly there stalls the event loop. The fix is caller-side (a thread executor) and
belongs to this phase — `plans/phase-plan-v13-PKGID.md` was written before the
finding and does not carry it.

**Non-goals**
Sandboxing what does run; auditing PMCP's own dependencies.

**Key files**
- `src/pmcp/tools/handlers.py`
- `src/pmcp/policy/policy.py`
- `src/pmcp/manifest/installer.py`
- `src/pmcp/validation.py`
- `tests/test_package_identity_gate.py` (new)

**Depends on**
- TRUST

**Produces**
- IF-0-PKGID-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/tools/handlers.py`, `src/pmcp/policy/policy.py`, `src/pmcp/manifest/installer.py`
- evidence paths: `tests/test_package_identity_gate.py`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- missing or malformed evidence routes to `blocker_class=contract_bug` (non-human).

### Phase 4 — Outbound actions need explicit authority (EGRESS)

**Objective**
Stop `gateway.submit_feedback` acting publicly under the operator's ambient identity;
make preview the default and explicit opt-in the only path to submission.

**Exit criteria**
- [ ] EC-EGRESS-1 — ambient `GITHUB_TOKEN` is never used; only a dedicated `PMCP_FEEDBACK_TOKEN` is honoured, asserted by a test that sets `GITHUB_TOKEN` and requires no request to be attempted.
- [ ] EC-EGRESS-2 — the default is preview-only: the handler returns the payload and a browser URL, and `confirm_submission` alone cannot cause a post without `enable_feedback_submission: true`.
- [ ] EC-EGRESS-3 — the default repository is corrected to the real remote, asserted against the value in the packaged config rather than a literal duplicated in the test.
- [ ] EC-EGRESS-4 — no blocking HTTP call runs on the event loop in this path (addresses P-03).

**Scope notes**
Decompose into 2 lanes with disjoint concerns in one file: **lane A** owns the
credential and consent gate, **lane B** owns the repository default and moving HTTP
off the loop. Both touch `tools/handlers.py:4796-4992` — treat that range as a
single-writer region and serialise the two lanes if the phase is executed
concurrently. Root phase: it shares the consent *principle* with TRUST but none of
its data contracts, so forcing a dependency would serialise the roadmap for nothing.

**Non-goals**
Redesigning the feedback feature; the wider redaction rework (that is S-12/#234).

**Key files**
- `src/pmcp/tools/handlers.py`
- `tests/test_feedback_egress.py` (new)

**Depends on**
- (none)

**Produces**
- IF-0-EGRESS-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/tools/handlers.py`
- evidence paths: `tests/test_feedback_egress.py`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- missing or malformed evidence routes to `blocker_class=contract_bug` (non-human).

### Phase 5 — Document and prove the model (SEAL)

**Objective**
State the trust model in SECURITY.md exactly as implemented, and prove the four
boundaries hold together against an adversarial end-to-end suite.

**Exit criteria**
- [ ] EC-SEAL-1 — SECURITY.md describes the implemented model with no claim the tests do not prove; each claim cites the test that proves it.
- [ ] EC-SEAL-2 — an adversarial suite drives the four review reproductions end to end and each fails closed, run against the real handlers rather than mocks.
- [ ] EC-SEAL-5 — the **composition** cases fail closed too, not only the four original reproductions: an unapproved overlay that adds a server (EC-CONSENT-6), and an approval record shipped inside the checkout (EC-TRUST-5). Both are seams between phases that each pass their own criteria, which is exactly what a per-phase suite cannot catch.
- [ ] EC-SEAL-3 — every refusal path prints the exact `pmcp` command that would grant the action, asserted for each gate.
- [ ] EC-SEAL-4 — a fresh operator with no trust store and no project files sees unchanged behaviour for manifest-backed servers.

**Scope notes**
Decompose into 2 lanes with disjoint files: **lane A** owns SECURITY.md and the
CHANGELOG narrative, **lane B** owns the adversarial end-to-end suite. Lane B is the
one that can fail late, so start it as soon as PKGID and CONSENT publish their gates
rather than waiting for this phase to open.

**Non-goals**
New gates. This phase proves and documents; it does not add behaviour.

**Key files**
- `SECURITY.md`
- `CHANGELOG.md`
- `tests/test_trust_boundaries_e2e.py` (new)

**Depends on**
- CONSENT
- PKGID
- EGRESS

**Produces**
- IF-0-SEAL-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `SECURITY.md`
- evidence paths: `tests/test_trust_boundaries_e2e.py`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- missing or malformed evidence routes to `blocker_class=contract_bug` (non-human).

## Top Interface-Freeze Gates

- IF-0-TRUST-1 — the provenance/approval record `(absolute_path, content_sha256, scope, decision, recorded_at)`; `is_approved(path, content: bytes) -> bool` (**hashes the bytes the caller is about to apply**, closing the check-then-use window), `record(path, content, scope, decision)`, `revoke(path)`. The freeze also fixes **store residency and write authority**: user-scoped, outside any repository, never agent-writable. Frozen before CONSENT and PKGID start — a downstream lane that has to guess residency cannot start.
- IF-0-TRUST-2 — the package identity tuple `(registry, name, resolved_version, integrity)` and the resolver that produces it without executing the package.
- IF-0-CONSENT-1 — the project-source gate decision surface: given a candidate source path and scope, the single call every loader uses to decide whether to apply it.
- IF-0-PKGID-1 — the provisioning gate: given a server config and a resolved package identity, the single call `provision` uses to decide whether an install may spawn.
- IF-0-EGRESS-1 — the outbound-action gate: the credential and consent predicate `submit_feedback` consults before any network call, and the preview payload shape returned when it refuses.
- IF-0-SEAL-1 — the documented trust model in SECURITY.md, each claim bound to a proving test.

## Phase Dependency DAG

```
  TRUST ─┬─> CONSENT ─┐
         │            ├─> SEAL
         └─> PKGID ───┤
                      │
  EGRESS ─────────────┘   (root; parallel with TRUST from day 1)
```

- `CONSENT` and `PKGID` both depend only on `TRUST` and can be **planned** concurrently, but they are **not file-disjoint**: both write `src/pmcp/policy/policy.py` (CONSENT lane C, PKGID lane B). Execute those two lanes serially against that file, or land one phase's policy lane before opening the other's. **WAS WRONG (rev 1):** this line claimed they "share no files", contradicting both phases' own Key files.
- `EGRESS` shares no ancestor with `TRUST` — it can start immediately, in parallel with everything.
- Critical path: `TRUST → PKGID → SEAL` (PKGID is the largest phase).

## Execution Notes

- Plan in DAG order: `/claude-plan-phase TRUST` first. Once TRUST merges, run
  `/claude-plan-phase CONSENT` and `/claude-plan-phase PKGID` **concurrently**.
  `/claude-plan-phase EGRESS` can be planned at any time, including now.
- Execute with `/claude-execute-phase <alias>` per phase. `CONSENT` and `PKGID` are
  parallel-safe; `EGRESS` is parallel with all of them.
- `SEAL` opens only after `CONSENT`, `PKGID` and `EGRESS` have merged, but its lane B
  (adversarial suite) should be written against the gates as they land.
- Single-writer hazards to serialise: `src/pmcp/tools/handlers.py` is written by
  PKGID lane A and both EGRESS lanes; `src/pmcp/policy/policy.py` by CONSENT lane C
  and PKGID lane B. Do not run those lanes against the same file concurrently.
- **CONSENT and PKGID may be planned concurrently, but must execute serially.**
  They are sibling branches off TRUST with no freeze between them, so the DAG
  permits parallel execution — but their lane plans declare four shared writers:
  `src/pmcp/policy/policy.py` (CONSENT SL-4 and PKGID SL-2 both own it),
  plus `.claude/docs-catalog.json`, `CHANGELOG.md`, and this roadmap file from
  the two docs lanes. Each plan is internally disjoint; the collision is only
  visible pairwise across the two phases, which no single plan's own
  ownership check can see. **Execute CONSENT first, merge it, then execute PKGID** —
  the order is fixed, not free. PKGID's package predicate is tri-state and CONSENT
  owns the composition that must wrap it, so the integration criterion (a project
  `"allowed"` package cannot override a user `"denied"` package) is only provable
  once CONSENT has landed; it is therefore owned by PKGID, the second phase, which
  is the only place both halves exist.
- Every phase ships a CHANGELOG entry; `SECURITY.md` is written once, in SEAL, to
  avoid four partial descriptions of one model.

## Verification

```bash
# Each phase's own suite
uv run pytest -q tests/test_trust_store.py
uv run pytest -q tests/test_project_source_consent.py
uv run pytest -q tests/test_package_identity_gate.py
uv run pytest -q tests/test_feedback_egress.py

# The roadmap's end-to-end proof: the four review reproductions must fail closed
uv run pytest -q tests/test_trust_boundaries_e2e.py

# No regression for an operator with no trust store and no project files
uv run pytest -q tests/                 # compare counts to the pre-roadmap baseline
uv run ruff check src/ tests/ scripts/ && uv run ruff format --check src/ tests/ scripts/
uv run mypy src/
uv run python scripts/check_workflows.py --base-ref origin/main
```

Host note: `/tmp/package.json` makes ~107 npm-identity tests fail on some hosts
(`tests/conftest.py`); compare the same command from the same directory before and
after, never against a clean-machine expectation.
