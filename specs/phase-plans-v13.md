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
- [ ] EC-TRUST-5 — the store lives **outside any repository** (user scope, e.g. `~/.config/pmcp/`), and a store path inside the current checkout is refused. A repository that ships its own approval record must not be believed; without this, absence-is-not-assent is decorative. **Corrected under SEAL's `canonical_spec_update` closeout (2026-09-17):** the original read "refused at startup", but the residency guard raises per read of the store (`src/pmcp/trust_store.py:155`), so a checkout-resident store is genuinely refused yet the refusal surfaces through the consent gate's "not approved / run `pmcp trust approve`" message rather than as a distinct startup abort; and after SL-7 (Consiliency/pmcp#251) the checkout it is judged against is the served project root ∪ the launch checkout, not `cwd` alone. See the SEAL post-execution amendments, item 5.
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
between them (item 9); nothing below reopens a contract, and no criterion had to
be weakened. What follows is what the plan and this roadmap got wrong about the
*work*, and the findings a later phase inherits whether or not it reads them.
**Items 1, 2 and 3 are the ones PKGID and SEAL pay for if they are not read**,
and item 1 was found last, after the rest of this block was written — it is the
one to read first.

1. **TEN lane-owned tests are proven by NO acceptance criterion — this phase's
   security properties are contracted but never required.** Found while building
   a brief generator, after the rest of this block was written; it subsumes and
   sharpens item 3. `scripts/check_plan_consistency.py` reports
   `lane-contracted: 41   EC-proved node ids: 31` and names the gap: ten tests
   appear in a lane table, so a lane is contracted to write them, and in zero EC
   proving commands, so **phase close never runs them**. Delete all ten and the
   phase still closes green. They are not incidental tests:

   | Lane | Test | The rule it is the proof of |
   |---|---|---|
   | SL-1.1 | `test_read_and_gate_opens_the_path_exactly_once` | IF-0-CONSENT-1's one-read rule — the entire TOCTOU defence |
   | SL-1.1 | `test_an_unrecorded_path_is_refused` | default deny |
   | SL-1.1 | `test_a_store_error_is_refused_not_raised` | fail-closed on a broken store |
   | SL-1.1 | `test_an_unreadable_source_is_refused_not_raised` | fail-closed on an unreadable source |
   | SL-1.1 | `test_read_and_gate_returns_none_bytes_when_refused` | a refusal hands back no bytes to parse |
   | SL-1.1 | `test_remediation_is_the_absolute_path_trust_approve_command` | the refusal is actionable |
   | SL-1.1 | `test_log_refusal_emits_one_warning_naming_the_remediation` | exactly one WARNING, naming it |
   | SL-2.1 | `test_approved_overlay_is_applied` | the gate is not simply "deny everything" |
   | SL-3.1 | `test_approved_project_mcp_json_is_applied` | same, for `.mcp.json` |
   | SL-4.1 | `test_project_redaction_patterns_extend_rather_than_replace_defaults` | the **only** falsifier for "a project file must not drop `DEFAULT_REDACTION_PATTERNS`" |

   Two of those deserve naming twice. `read_and_gate` reading the path exactly once
   *is* IF-0-CONSENT-1 — item 3 below says the criteria are "structurally blind"
   to a gate-then-reread regression, and the literal truth is narrower and worse:
   the test that proves the one-read rule is simply in no EC at all. And the word
   "redaction" does not appear anywhere in this phase's Acceptance Criteria section
   (measured: zero occurrences), so the redaction-widening rule — an S-11-class
   widening, the exact thing this phase exists to stop — ships with its sole
   falsifier unrequired.

   **Credit where it is due: all four lanes wrote all ten anyway**, by following
   their lane tables, and mutation-proved them. Verified here: every one exists and
   passes (`10 passed`). **The code is right; what is missing is the requirement
   that it be proven.** That is precisely why this is worth recording rather than
   quietly fixing — a future phase that reads only the ECs, as a close-out or a
   generated brief does, would ship these same rules unproven and never know.

   **The consequence for how we work.** A lane-owned test that no EC proves is an
   **unproven requirement**, not a stylistic nit, and the checker's `[warn]` line
   must be read that way — it had been treated as benign. Every EC should name the
   tests that prove its rule, and any lane-owned test outside that set should be
   justified in the plan or promoted into a criterion. **This is not a CONSENT
   quirk**: the same checker reports one such test in `plans/phase-plan-v13-TRUST.md`
   (`test_a_denied_record_is_not_approved` — the proof that a `"denied"` record
   refuses, which is the very behaviour TRUST amendment 1 above turns on, and TRUST
   is already merged) and two in `plans/phase-plan-v13-PKGID.md`
   (`test_parse_package_spec_splits_a_scoped_name_from_its_version`,
   `test_evaluate_package_policy_matches_a_scoped_name_glob` — both still fixable,
   because PKGID has not executed). Close PKGID's two before it starts.

2. **EC-CONSENT-5's own defect note undercounts the damage, and the phase learned
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
   sixteenth was item 4 below wearing a disguise.
   **The rule this phase arrived at, and which the next phase should plan for up
   front: the owning lane repairs its OWN suite, and all cross-cutting fallout is
   repaired in ONE single-writer pass after the lanes merge.** Not out of
   tidiness — a file like `tests/test_tools.py` can break from more than one
   lane's gate, so concurrent lanes editing it lose each other's updates, and the
   interaction set is by construction invisible to any single lane working in an
   isolated worktree. A phase that introduces a gate in front of an
   already-widely-fixtured source must budget that pass as work, not treat it as
   an overrun.

3. **The acceptance criteria are structurally blind to a gate-then-reread (TOCTOU)
   regression — the one defect the phase exists to prevent.** Read item 1 first:
   it states the sharper, more literal version of this — the test proving the
   one-read rule is in no acceptance criterion at all. What follows is why that
   test cannot be substituted for by the ones that *are* required.
   IF-0-CONSENT-1's
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

4. **Phase assembly created an import cycle that no lane and no test run could
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

5. **The evidence path `tests/test_project_source_consent.py` does not exist and
   was never creatable.** A single file cannot be disjointly owned by three
   concurrent lanes. Phase 2's **Key files** entry and its **spec closeout
   policy → evidence paths** entry should both be read as naming these four:
   `tests/test_project_consent_gate.py` (the gate itself, SL-1),
   `tests/test_project_source_consent_manifest.py` (SL-2),
   `tests/test_project_source_consent_config.py` (SL-3) and
   `tests/test_project_source_consent_policy.py` (SL-4). SEAL's closeout should
   collect all four.

6. **The phase decomposed into four implementation lanes, not the three this
   roadmap's Scope notes prescribe** (five with SL-docs). The Scope notes assign
   one loader per lane and assume the three consume IF-0-TRUST-1 directly. They
   cannot: IF-0-CONSENT-1's "the single call every loader uses" is itself a new
   file all three loaders import, so it is a lane — SL-1, `src/pmcp/project_consent.py`
   — and the only DAG root. Lanes A/B/C became SL-2/SL-3/SL-4 and open together
   once SL-1 lands. A roadmap that names a shared interface as a phase output
   should count it as a lane.

7. **Two IF-0-TRUST-1 assumptions this phase stated up front proved wrong, and
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

8. **EC-CONSENT-2 undercounts the project `.mcp.json` read sites: there are five,
   not four.** The plan groups them as consumers of `_iter_config_source_paths`;
   `registry_allow_private_from_config` is not one — it builds its own candidate
   list and appends the project `.mcp.json` directly. An implementer working from
   the count leaves that reader fail-open, which is the `allowPrivateRegistry`
   flag, set by an unapproved repository file. SL-3 routed all five through a
   single helper, `_gate_project_config`, so the omission is structurally
   impossible rather than merely tested for. **Prefer one choke point to N gates**
   wherever a phase gates a source with more than one reader.

9. **Accepted deviation from IF-0-CONSENT-1: `reason` is a `Literal`, not a bare
   `str`.** `ConsentReason = Literal["approved", "no_record", "content_changed",
   "unreadable"]`. `Literal` is a subtype of `str`, so every downstream `reason:
   str` annotation and comparison still type-checks (verified). What it buys,
   precisely: mypy rejects constructing a `ConsentDecision` with a reason outside
   the vocabulary under this repo's current settings. What it does **not** buy:
   catching a typo'd `==` comparison, which needs `--strict-equality`, and this
   repo does not enable it. Recorded so the next reader does not overstate the
   guarantee.

10. **Two UX findings handed to SEAL, both out of scope here.** (a) The frozen
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

11. **`plans/phase-plan-v13-CONSENT.md`'s Context line references have gone stale**
    as a result of this phase's own work: `_load_overlay_file` no longer opens the
    path, so `manifest/loader.py:696`, `:801` and `:806-809` no longer point where
    the plan says (`servers.update(overlay_servers)` is now at `:848`). Expected
    for a merged phase plan, recorded because the plan is still the document a
    reviewer reads to check the criteria.

12. **Five lane briefs, five factual errors — a plan-authoring lesson, in the
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

### Post-execution amendments — PKGID (2026-09-15)

Recorded by SL-docs after SL-1, SL-2 and SL-3 landed, were merged, and a
single-writer repair pass ran over the assembled branch. It was then revised after
the phase panel's five findings (F1-F5) were fixed in one further single-writer pass
(`03cd186`, merged at `a60accf`), and again after three narrow panel rounds on
the manifest reader (item 12; last fix `8f65da0`). Both freeze gates shipped with
the signatures they declared, and S-01's reproduction fails closed at every door found (EC-PKGID-1). But
the gate turned out to have **two doors, not one** (item 1), the frozen decision
order was wrong until a lane read it before the implementation started (item 3), and
the panel reproduced a way to run **different bytes under an approved pin** (item 8).
Each gap in item 9 is marked resolved or open. Line references are to `8f65da0`.
**Items 1, 8 and 9 are the ones EGRESS and SEAL pay for if they are not read.**

1. **The lifecycle door: gating `provision` did not gate every install spawn.**
   This roadmap's IF-0-PKGID-1 ("the single call `provision` uses") and the plan's
   Context item 2 both assumed that one gate in front of `start_install` covered
   every spawn that installs, because `start_install` has exactly one production
   callsite (`src/pmcp/tools/handlers.py:4651`). It did not. `npx -y` fetches and
   runs the package *when it spawns*, so every path that spawns a discovered config
   is an install path. `connect_server`, `restart_server` and `update_server` all
   reach a discovered config through the lifecycle resolver
   (`_resolve_lifecycle_config`, `handlers.py:3370`, now a wrapper over
   `_resolve_lifecycle_target`, `:3383`; discovered lookup at `:3470-3473`), and
   before this phase that branch checked only the agent-chosen server name
   (`:3476`). `update_server` resolves there twice, before its probe (`:5261`) and
   in its post-probe recheck (`:5418`), and the probe itself spawns `npx -y
   <pkg>@latest --help` (`:5349`, spawned at `:3769`). So
   `register_discovered_server(name=<allowlisted>, package=<arbitrary>)` followed by
   `connect_server(name=<allowlisted>)` ran the package without `provision` ever
   being called. SL-1 found this; the operator approved gating that resolver's
   discovered branch. The gate runs after the name-policy denial and before the
   credential check (`:3501-3525`, credential check at `:3527`). **Disconnect is
   not gated** (`action != "disconnect"`, `:3501`): it spawns nothing, and refusing
   it would strand a server that is already running. As shipped by SL-1 the gate
   ran for the discovered lookup only; F2 (item 9) widened it to manifest hits,
   where only rule 1 can refuse. Proven by the four lifecycle tests the plan's
   EC-PKGID-1 names.
   **Lesson for EGRESS and SEAL: enumerate SPAWN sites, not install callsites.** A
   gate is placed in front of what executes, and "install" is a name for one
   function, not for the set of things that fetch and run code. SEAL's adversarial
   suite should drive the S-01 reproduction through all four tools, not only
   `provision`.

2. **`provision`'s `.mcp.json` ("configured") branch is not wired to the gate — an
   operator decision.** That branch (`handlers.py:4310`) returns through
   `ensure_connected` and never reaches `start_install`. `.mcp.json` servers also
   lazy-start on `invoke`, so a refusal only in `provision` would block nothing.
   And every non-npm configured server (uvx, node, docker) has no resolvable
   identity, so it would be refused as `unresolvable_identity`. Project-sourced
   `.mcp.json` is already consent-gated by CONSENT. The lifecycle resolver's
   configured branch (`:3402`) likewise returns before the gate. IF-0-PKGID-1's
   rules 3 and 4 for `source="configured"` are therefore implemented
   (`src/pmcp/provision_gate.py:411-421`) and tested **at the `evaluate_provision`
   level only**; no production caller passes `"configured"`. **A later phase that
   wires a configured spawn path must wire rules 3 and 4 with it**, rather than
   assume a configured server is already gated. The same holds for the package
   lists: no `.mcp.json` server is checked against `packages.denylist`.

3. **IF-0-PKGID-1's decision order was corrected BEFORE implementation.** As
   planned, the unpinned-argv deny sat after both allow rules (a recorded approval,
   a policy allow), which had already returned. So an approved or policy-allowed
   server whose argv was an unpinned `npx -y pkg` was allowed before the pin check
   could run. That reopens S-01 for exactly the servers an operator trusted: approve
   `pkg@1.2.3`, and the spawn still re-resolves `latest`. SL-2 found it while
   reading the freeze, before SL-1 (which implements the freeze literally) was
   dispatched, and the plan was corrected in `07e1898`. It is now rule 4, ahead of
   every allow (`provision_gate.py:420`, before `:424` and `:428`), and the freeze
   says in words that it must stay there. The gate checks the pin structurally:
   the first argument after the allowlisted leading flags must *be*
   `name@resolved_version`, in `args` and in every platform's install argv
   (`provision_gate.py:136-184`).

4. **SL-2's choices where the plan was silent.** (a) An allowlist miss is
   `"unspecified"`, not `"denied"` (`src/pmcp/policy/policy.py:379-395`). Only an
   explicit denylist match may outrank a recorded approval, so a narrow allowlist
   does not silently revoke approvals outside it. (b)
   `evaluate_package_policy(None)` is `"unspecified"` (`policy.py:420-421`): no
   identity names nothing a list could match, and what an unresolvable identity
   means is the gate's decision. Its consequence was item 9's first gap, now
   closed by a name-level predicate rather than by changing this answer. (c) A
   version-bearing policy entry (`pkg@1.2.3`) is **rejected when the policy loads**
   (`src/pmcp/types.py:1015`) rather than accepted. Globs match the name alone (so
   a publisher cannot satisfy `*-mcp` with version `1.0.0-mcp`), so such an entry
   would match nothing, and a denylist entry that silently never matches is a
   denial that fails open.

5. **SL-3's findings.**
   - **There was a second leaky log line.** The plan named only `start_install`'s
     INFO `<args redacted>` line (the Execution Notes call SL-3's one destructive
     change "one INFO log line"). But `install_server` also logged the full argv
     verbatim at INFO (`' '.join(install_cmd)`), including any credential it
     carried. Both are replaced (`src/pmcp/manifest/installer.py:204`, `:683`).
   - **The spawn sites never receive a `PackageIdentity`.** `start_install`,
     `install_server` and `verify_installation` take a `ServerConfig`, so the plan's
     "the element the resolved `PackageIdentity` names" had nothing to read. The
     package slot is identified structurally instead, as the first argument after
     the leading frozen flags (and `--registry`'s value), and shown only if SL-2's
     `parse_package_spec` and `is_valid_package_version` accept it as a pinned
     `name@version` (`installer.py:54-102`).
   - **`verify_installation` never names the package.** It spawns
     `command, *args[:1], "--help"` (`installer.py:729-733`), which for a pinned
     discovered server is `npx -y --help`. It has no production callers, and its
     behaviour is unchanged; it now logs that argv like the others (`:734`).
   - **The frozen flag literals are shown wherever they appear**, not only before
     the package (`installer.py:36`). They are fixed literals and carry nothing.
   - Measured against the shipped manifest: **none of its 412 platform install
     argvs render a package** (all show `<redacted>` in the slot), because manifest
     install commands are unpinned (`@playwright/mcp@latest`). Only a pinned
     discovered server's spawn log names what runs. See item 11(a).

6. **Registration now reaches the network, so the suite needed an isolation guard.**
   The repair pass added an autouse fixture that fails any test whose identity
   lookup opens the real npm registry (`tests/conftest.py:169`), plus a
   `fake_npm_registry` fixture (`:196`). **Three existing tests had silently
   started calling registry.npmjs.org** once registration resolved. They still
   passed, because `resolve_package_identity` turns a network error into a quiet
   `None`, so a refusal assertion could pass for the wrong reason offline. This is
   CONSENT amendment 2's rule working as intended: the five out-of-lane breakages
   were repaired in one single-writer pass after the lanes merged. It also means
   PKGID wrote `tests/conftest.py`, which CONSENT SL-1 owns. That was safe only
   because the two phases executed serially.

7. **Process: every subagent in a session shares one scratchpad directory.** Two
   parallel lanes' mutation-testing scripts collided there, and a lane commit
   briefly captured a live mutant, `return spec in args`, a substring check that
   reopens `["-p", "evil", "-y", "pkg@1.2.3"]` (`-p` installs a second package
   first). It was caught and the commit amended before merge, so history carries
   no trace of it. This item is recorded on the orchestrator's report, not
   verified from the tree. **Lanes must use lane-unique scratch subdirectories, and
   must assert the working tree matches `HEAD` before and after any mutation run.**
   A mutation harness that restores files by path is a writer, and two of them in
   one directory are two writers.

8. **F1 — an agent-declared env var ran different bytes under an approved pin.
   Found by the phase panel's native correctness seat, with a reproduced exploit;
   fixed in `03cd186`.** The pin binds a package to `name@version`, but npx decides
   *where* it fetches that version from its environment. The chain: register a
   package the operator had **already approved**, under a new server name, with
   `env_vars=["npm_config_registry"]`; call `auth_connect` with an attacker's
   registry URL; provision. The gate allowed it (the identity was approved and the
   argv pinned), `auth_connect` accepted the name because it was the server's own
   declared variable, and `build_install_child_env` injected the value into the
   pinned `npx -y name@version` spawn (`installer.py`'s `own_env[env_var]`). npx
   then fetched the approved name and version from the attacker's registry:
   different bytes than were approved. `auth_connect` also writes the value into the
   gateway's own `os.environ` (`handlers.py:4972`).
   **Why the existing check could not catch it:** `env_var_allowed` is a blocklist
   of code-loading names plus "the server's declared name is always allowed", and
   for a discovered server **the declared name is chosen by the agent**. A blocklist
   over agent-chosen names has to anticipate every variable any package manager
   reads, in any letter case, and `npm_config_registry` was not on it.
   **The fix is an allowlist for discovered servers**
   (`validation.discovered_env_var_allowed`, `src/pmcp/validation.py:207-225`): a
   name must be credential-shaped, must not be one `is_dangerous_env_var` refuses,
   and must not start, case-insensitively, with `NPM_CONFIG_`, `NODE_`,
   `COREPACK_`, `YARN_`, `PNPM_` or `BUN_` (`:197`), which excludes
   credential-shaped names such as `NPM_CONFIG__AUTH` and `NODE_AUTH_TOKEN` too.
   It is enforced at registration, before the registry is contacted
   (`handlers.py:5753-5771`), and in `auth_connect` for both the declared name and
   an explicit override (`:4925-4927`). Which rule applies is keyed on which lookup
   found the config (`from_discovered`, `:4866-4870`), never on a field of the
   config. Manifest servers keep `env_var_allowed`, so a shipped `POSTGRES_URL`
   still works. Proven by the ten F1 tests the plan now names in EC-PKGID-5.
   **Lesson for SEAL:** every agent-supplied field that reaches a spawn's
   environment is part of the package's identity, not metadata about it. SEAL's
   adversarial suite should include this reproduction.

9. **KNOWN GAPS — status after the panel fixes.**
   - **RESOLVED (F2): a `packages.denylist` now blocks a manifest-backed server.**
     Previously `provision` and the lifecycle resolver passed `identity=None` for
     a manifest lookup, `evaluate_package_policy(None)` was `"unspecified"`, and
     rule 1 fired only for a non-`None` identity, so a denylisted manifest package
     provisioned through rule 2. That contradicted IF-0-PKGID-1's own rationale and
     narrowed EC-PKGID-2 to discovered servers. Rule 1 now also reads, for a
     manifest lookup, the package names the trusted config spells out, with no
     network (`_manifest_package_names`, `provision_gate.py:253`; `_manifest_denial`,
     `:300`; applied at `:402-406`). As first shipped (F2) that was the `package`
     field and the positional package slot of `args` and of each install argv.
     Three narrow panel rounds then hardened the reader (item 12):
     - **G1 — npx package selectors.** `npx -y -p denied-pkg bin` put `-p` in the
       slot, which names nothing, so `denied-pkg` was never checked.
       `_npx_selected_specs` (`:197`) now reads every `-p X`, `--package X` and
       `--package=X`, any number of them, and `--`. An entry is **undetermined**
       when its npx argv holds an option pmcp cannot place (`--registry X`, `-c`,
       `--call=...`), a selector with no value or with a value starting with `-`,
       or a selected spec that does not parse (`github:x/y`, `./dir`). An
       undetermined entry is refused, with reason `denied`, **only when a
       `packages.denylist` is in force** on either policy side
       (`PolicyManager.has_package_denylist`, `policy.py:424`); with no denylist
       nothing changes. Measured: no shipped manifest entry is undetermined, and
       `test_no_shipped_manifest_entry_is_undetermined` asserts it. The refusal
       summary still reads "denied by policy", because the reason vocabulary is
       closed; the remedy (`_undetermined_remedy`, `:352`) says the packages could
       not be determined and names the argument.
     - **H1 — after a selector, the positional is the command.** `npx
       --package=allowed-pkg node server.js` runs `node` from the selected package
       and fetches nothing named `node`, but was recorded as a package, so it was
       refused under a denylist of `[node]`, and a command path made the entry
       undetermined. The positional is now recorded only when no selector preceded
       it; the same rule applies to the argument after `--`.
     - **G2, H2, J1 — executable spellings.** The reader recognises npx through
       `validation.normalized_executable_name` (`validation.py:102`): one leading
       `X:` drive prefix is stripped (J1: `C:npx.cmd` had normalized to `c:npx`;
       `C:` alone names nothing), the name is split by hand on both `/` and `\`
       (H2: `PureWindowsPath('//bin/npx').name` is `''`, so that POSIX spelling
       escaped the reader), lower-cased, and one `.exe`/`.cmd`/`.bat` removed.
       The discovered-server pin check `_is_npx` (`provision_gate.py:162`) is
       deliberately left strict: registration only ever writes `npx`, so a wider
       match there could only accept argv as pinned.
     Names are checked through
     `PolicyManager.evaluate_package_name_policy` (`policy.py:437`), which
     `evaluate_package_policy` now delegates to, so the two cannot drift. The
     lifecycle resolver consults the gate for manifest hits as well (`:3501`),
     still not for disconnect. Only 21 of the 107 shipped manifest entries carry a
     `package` field, which is why the argv slots are read too.
     **Trade-off, recorded:** a policy evaluation that raises now refuses
     `connect_server`, `restart_server` and `update_server` for a manifest server
     through rule 8. Before F2 the resolver never consulted the gate for a
     manifest hit, so that could not happen.
   - **RESOLVED (F4): `provision` asked for a credential before running the gate.**
     The gate now runs first (`handlers.py:4443`, credential check at `:4465`),
     matching the lifecycle resolver.
   - **OPEN, for SEAL: operator approvals never bind integrity.**
     `pmcp trust approve-package` is offline and records `integrity=None`
     (`src/pmcp/cli.py:2588`), and `is_package_approved` compares digests only when
     both the record and the identity carry one
     (`src/pmcp/package_approvals.py:326-331`). The digest check is therefore inert
     for every approval the only shipped writer creates. Item 8 shows why this
     matters: the pin is `name@version`, and the bytes behind that pin are what
     a registry says they are.
   - **OPEN, residual after F1: `auth_connect` for a server name in neither table.**
     With no declared variable, `env_var_allowed(env_var, None)` admits any
     credential-shaped override (`handlers.py:4925`), and credential-shaped
     includes `NPM_CONFIG__AUTH`. The value is stored and written into the
     gateway's `os.environ` (`:4972`). `sanitized_subprocess_env` strips
     PMCP-managed secrets from every child's environment, so no spawned server
     inherits it by that route; the gateway process itself still holds it. F1 did
     not change this path.
   - **OPEN, informational:** as with TRUST amendment 1, `package_approvals.py`
     reserves a `"denied"` decision (`:51-55`) that no shipped verb writes, and
     `revoke-package` deletes a record rather than denying it. Absence already
     refuses, so this is not a hole.

10. **The panel's `update_server` `@latest` probe claim did not reproduce, and was
    closed structurally anyway (F5).** The claim was that `update_server` on an
    approved discovered server would probe `npx -y <pkg>@latest --help`, running a
    version nobody approved. It did not reproduce: registration pins `args`, and
    `_detect_effective_version_pin` refuses a pinned argv before the probe is
    built. But that refusal was coincidental. Nothing tied pin detection to the
    discovered table, and its message called a discovered server "the manifest
    entry". `update_server` now refuses any server the discovered lookup produced,
    before any probe or restart, at both resolutions (`handlers.py:5285`, `:5425`;
    message at `:5193`), and tells the operator to re-register and approve the new
    version. The lookup is returned by `_resolve_lifecycle_target` rather than read
    off the config, because `manifest_server_to_config` stamps a discovered config
    `source="manifest"` too. Proven with pin detection stubbed to miss.

11. **Where this roadmap's PKGID text is contradicted by what shipped** (original
    text left unedited, as above).
    - (a) **EC-PKGID-4 says "the exact argv is logged at WARNING before every
      install spawn."** *Every install spawn* — **RESOLVED (F3).** Under item 1's
      definition, two spawns outside `installer.py` fetch and run a package. Both
      now log the same rendered argv at WARNING first: the client manager's stdio
      spawn when the executable is a package runner, `npx`/`npx.cmd`/`npx.exe`/
      `uvx`/`pnpx`/`bunx` under any Windows or POSIX spelling, matched by
      `normalized_executable_name` (G2, H2, J1; `src/pmcp/client/manager.py:73`,
      logged at `:2352-2359`, spawned at `:2368`), and `update_server`'s probe, unconditionally
      (`handlers.py:3768`). Other stdio executables log no warning, because a local
      binary installs nothing. *Exact* — **contradicted by design, still.** The log
      is a rendered argv, redacted except the executable, `-y`/`--yes`/`--quiet`,
      `--registry`'s name and a pinned `name@version`, because an argv can carry a
      credential. For manifest installs even the package is `<redacted>` (item 5).
      The criterion's wording should be read as "the rendered argv".
    - (b) **Scope notes assign "version pinning in `manifest/installer.py`" to lane
      C.** The pin shipped at registration in `handlers.py` (`:5829`, `:5855`),
      owned by SL-1, which is where the plan's EC-PKGID-5 decision put it. The lane
      split also differed: SL-2 owned `policy.py`, `types.py` and `validation.py`;
      SL-1 owned `handlers.py`, `cli.py` and the two new modules
      `provision_gate.py` and `package_approvals.py`. The panel-fix pass then wrote
      `policy.py` and `validation.py` (SL-2's) and `client/manager.py` (no lane's).
    - (c) **Scope notes say "Parallel-safe with CONSENT: no shared file."** Already
      contradicted by this roadmap's own DAG notes (`policy/policy.py` is written
      by both), and PKGID's repair pass also wrote `tests/conftest.py` (item 6).
      Serial execution, as the Execution Notes fix it, is what made both safe.
    - (d) **EC-PKGID-3's opt-in is "config flag or recorded approval."** There is
      no flag. The opt-ins shipped are a recorded approval or a `packages.allowlist`
      entry in the operator's policy. "A manifest-backed server is unaffected" also
      now has one exception: a `packages.denylist` entry naming its package (item 9).
    - (e) **Key files, evidence paths and the roadmap `## Verification` name only
      `tests/test_package_identity_gate.py`.** The criteria are proven across seven
      files: that one, `tests/test_package_approvals.py`,
      `tests/test_policy_package_identifiers.py`, `tests/test_install_argv_logging.py`,
      and the panel-fix files `tests/test_pkgid_panel_fixes.py`,
      `tests/test_pkgid_spawn_logging.py` and
      `tests/test_pkgid_manifest_npx_selectors.py`. Key files also omit
      `src/pmcp/provision_gate.py`, `src/pmcp/package_approvals.py`,
      `src/pmcp/cli.py`, `src/pmcp/types.py` and `src/pmcp/client/manager.py`. As
      with CONSENT amendment 5, SEAL's closeout should collect all seven test files.
    - (f) **Resolved, not contradicted.** The Scope-notes warning that the plan did
      not carry TRUST amendment 2 is stale: the plan's SL-1 carries it, and
      registration resolves in a worker thread (`anyio.to_thread.run_sync`) under a
      20 s handler bound (`handlers.py:204`, `:5780`). CONSENT amendment 1's request
      to close PKGID's unproven lane tests was met, and still holds after the panel
      fixes were added: `scripts/check_plan_consistency.py` reports
      `lane-contracted: 77   EC-proved node ids: 77` for this plan. And the TRUST
      plan's "CONSENT and PKGID do not write `cli.py`" note, which PKGID's plan
      asked SL-docs to correct, was already corrected in
      `plans/phase-plan-v13-TRUST.md` on 2026-09-08; PKGID did write it
      (`cli.py:801-824`, `:2565-2630`).

12. **Process: three narrow panel rounds on manifest-parsing corners, then a
    deliberate stop.** After F1-F5, three further panels, each narrowed to the
    changed code, ran against the manifest reader and the runner-name check. Each
    round's red-team finding was reproduced before it was fixed: round 2 found G1
    and G2 (`01a5589`, with `3e49f1b` splitting the Windows-spelling test per
    site), round 3 found H1 and H2 (`0ce2afe`), round 4 found J1 (`8f65da0`).
    **No seat in rounds 2-4 found a way for an agent to run an unapproved
    package**: every finding concerned how an operator-shipped manifest entry is
    read against a `packages.denylist`, or whether its spawn is logged. After the
    third narrow panel the operator chose to fix J1 and open the PR without a
    fourth. Recorded so a reader does not mistake the stop for a clean panel: the
    reader is a hand-written model of npx's argument grammar, and it fails closed
    only under a denylist. SEAL should treat further corners there as expected, not
    as a regression.

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

### Post-execution amendments — EGRESS (2026-09-16)

Recorded by SL-docs after SL-1, SL-2, SL-3 and SL-4 landed and were merged. All four
exit criteria shipped as written and none had to be weakened, and IF-0-EGRESS-1
shipped with the signatures it declared — plus one optional output field its own
first revision had promised not to add, because keeping that promise would have
forced the gateway to report a certain negative it does not have (item 6(e)). But
**the exit criteria named three egress doors and there were four** (item 1), and the
variable EC-EGRESS-1 makes authoritative was itself writable by a tool the agent
calls (item 2). Line references are to `6a50e16`. **Items 1, 2 and 4 are the ones
SEAL pays for if they are not read.**

1. **The criteria named three doors and there were four, and the fourth survives
   EC-EGRESS-1 as written.** EC-EGRESS-1 asks that "ambient `GITHUB_TOKEN` is never
   used … asserted by a test that sets `GITHUB_TOKEN` and requires no request to be
   attempted." On `main`, `submit_feedback` reached the outside from four places:
   two `urlopen` calls (the visibility probe and the POST), a `shutil.which("gh")` →
   `asyncio.create_subprocess_exec("gh", "issue", "create", …)` fallback, and the
   browser URL it hands the agent. **Deleting `GITHUB_TOKEN` from the token lookup
   satisfies the criterion literally and changes nothing for door 3**: with no API
   token the handler fell through to `gh`, which authenticates from the inherited
   environment and from `gh`'s own stored credentials, so the very operator the
   criterion protects would still have posted under their personal identity — and
   pmcp could not have told them which credential it used. Door 4 issues no request,
   but it renders the destination into a URL the agent is told to open, so an
   attacker-chosen value there is still a disclosure.
   This was found by reading the function before planning, and confirmed by
   execution: SL-4's red run against the pre-lane handler reached a live
   `gh issue create --repo ViperJuice/pmcp` on the development host, which is why
   that file's gh-door guard is autouse for every test in it rather than opt-in
   (`c0b18e5`); two further tests drive `attacker/evil` — introduced by a real
   `load_dotenv` over a checkout's `.env` — and a value carrying a control character
   and shell metacharacters into the same `repository` that door 3 rendered straight
   into its argv on `main` (`tests/test_feedback_egress.py:539`, `:599`). The path is now **deleted
   rather than gated** — a gate in front of an unattributable identity still posts
   under it — and asserted gone structurally as well as behaviourally, by reading the
   handler's own source for `"gh"` and `create_subprocess_exec`
   (`tests/test_feedback_egress.py:342-344`). `handlers.py` no longer imports
   `shutil`, `urlopen` or `urlencode`, and the file's only remaining spawn is PKGID's
   update probe (`handlers.py:3786`). The destination is resolved and validated
   **first**, before every other rule, so no unvalidated value reaches any output —
   a refusal included, and door 4's URL included.
   **Lesson for SEAL, and the second phase in a row to learn it.** PKGID amendment 1
   was "enumerate spawn sites, not install call sites". EGRESS's is the same shape:
   **enumerate the paths that reach the outside, not the function you were looking
   at.** A criterion that names a credential bounds a credential; it does not bound
   an act. SEAL's adversarial suite should drive the S-04 reproduction at every exit
   from the process, not at the one API call.

2. **The credential predicate's own input was agent-writable.** EC-EGRESS-1 makes
   `PMCP_FEEDBACK_TOKEN` authoritative, and `gateway.auth_connect` writes the
   gateway's own `os.environ` (`handlers.py:4993`). `PMCP_FEEDBACK_TOKEN` is
   credential-shaped, and `env_var_allowed` admits any credential-shaped name when a
   server declares none — so an agent could have stored one through that tool and had
   pmcp honour it as "the operator's". Driven through the real tool, not simulated
   (`tests/test_feedback_egress.py:487-500`).
   Closed by a durable, additive, process-global provenance registry —
   `record_pmcp_introduced_keys` / `pmcp_introduced_keys` (`env_store.py:164`,
   `:198`) — which the gate reads alongside `dotenv_sourced_keys()` and
   `managed_secret_keys_strict(project_root)` (`feedback_egress.py:192-198`). The
   write and its record sit in the same statement group with no `await` between
   (`handlers.py:4993-5000`), and no gateway tool can clear the registry:
   `reset_pmcp_introduced_keys` (`env_store.py:203`) is a test-only seam with no
   production caller.
   **Why store membership alone was not enough, which is the part worth carrying
   forward.** The obvious check — "is this key in one of PMCP's own credential
   stores?" — reads evidence that can disappear while the variable it proves
   persists. **The mechanism this phase asserted for that, through three plan
   revisions and three panel rounds, is wrong, and was corrected only after the
   implementation panel prompted a measurement.** The claim was that `read_env_file`
   returns `{}` for a path it cannot read, so any `set_env_value` rewrites the store
   without the earlier key. On python-dotenv 1.2.3 an unreadable store instead RAISES
   `PermissionError`, `set_env_value` raises before opening anything, and the file is
   left byte-intact — as do a directory, undecodable bytes, and a key or value the
   writer rejects. Every unreadable shape fails closed. What does drop an entry: an
   operator deleting the store; a write that fails partway, since `write_env_file`
   truncates before writing and is not atomic (reproduced under `RLIMIT_FSIZE`); and a
   second writer racing this one, which needs another process because `_write_secret`
   is synchronous with no await between its read and its write. **Lesson: a mechanism
   repeated by four reviews is still only as true as the one time someone ran it.** A check whose evidence can vanish while the thing it proves
   persists is not a check, and the vanishing is reachable from a tool the agent
   calls. The same chain works across a restart, which is why the startup store loads
   are recorded too (`cli.py:2969-2972`): a previous process's `auth_connect` writes
   the store, this process loads it into `os.environ`, and one unrelated
   `auth_connect` later all three sources would otherwise say "the operator exported
   this".

3. **The plan was reviewed three times before implementation, and each round found a
   real defect.** Worth recording because none of the three was visible in the
   criteria, and each would have shipped as a working, tested, wrong build.
   - **Round 1 (P1, P2).** P1: the first revision proved provenance by store
     membership, the proxy item 2 refutes. P2: `abandon_on_cancel=True` bounds the
     **answer and not the act** — the worker outlives the handler and its return
     value is discarded — so a handler timeout alone lets the issue be filed after
     the operator has been told nothing was submitted, and the manual submission
     through the returned URL then duplicates it. The same round corrected the frozen
     decision order (the submission flag must be reported before the confirmation,
     or the agent is taught that confirming is what authorises a post) and a bullet
     that claimed `ok=True` plus payload plus browser URL for *every* refusal, which
     contradicted five rows of the plan's own table.
   - **Round 2 (Q1, Q2(b)).** Q1: the startup-load window above, which the first
     revision had deferred to SEAL on the reasoning that removing a store entry is an
     operator action — refuted by the plan's own read-modify-write finding. Q2(b): with
     the worker's return value discarded, a POST that *succeeded* and whose visibility
     probe then stalled had no way to be reported as the success it was, so the
     handler needs a progress record it can read after abandoning, not just a timeout.
   - **Round 3 (R1, R2).** R1: a race in which the handler truthfully reports
     "nothing was sent" with a compose URL and the worker then sends. Fixed by making
     the dispatch decision and the give-up decision **the same atomic step**:
     `claim_dispatch(deadline)` is the last thing before the opener, `abandon()` is
     what the handler calls on timeout, and exactly one of them wins under one lock
     (`feedback_egress.py:387`, `:424`; `handlers.py:5153-5157`). A lock that merely
     guards a snapshot slot does not give you this. R2: an error-status classifier
     that treated every failure as proof of non-creation, so an intermediary's 502 —
     which can arrive *after* the request was forwarded and the issue created — handed
     back a compose URL. Fixed by an establishing-status **allowlist**
     (401/403/404/410/422 carrying `X-GitHub-Request-Id`, `feedback_egress.py:90`,
     `:94`);
     everything else is uncertain, not negative.

4. **Known limits, stated rather than hidden. Both are SEAL's to judge.**
   - **No per-socket timeout is a total request bound**, in the standard library or
     in `httpx`: the timeout restarts on every connect/send/recv, so a peer returning
     one byte just under the threshold holds a call open indefinitely. The plan's
     first revision claimed socket timeouts "summing under the handler bound" bounded
     the request; they do not. What ships instead bounds the **act** — nothing is
     dispatched that cannot finish inside the remaining budget — and publishes facts
     as they are learned. The residual is that an abandoned worker can outlive the
     handler: `abandon_on_cancel=True` (`handlers.py:5151`) does not cancel the
     thread, so it keeps one of anyio's default thread-limiter tokens until it
     returns. It is forbidden to send by then, so it cannot act; it can only occupy.
   - **A credential-store file this process never read, written and removed entirely
     out of band between this process's startup and the call, is invisible to all
     three provenance sources.** Nothing PMCP does can determine an environment
     variable's origin after the fact; the three registries work because PMCP is
     present at every moment it introduces one itself. This residual requires an
     out-of-band writer on the same machine and is not reachable from any gateway
     tool.

5. **Six corrections to this plan's own `## Verification` checks, across four checks,
   each found by the lane the check would have blocked.** Five of the six are one
   defect: **a grep that asserts a COUNT must match a call, not a name.** The
   `record_pmcp_introduced_keys` check passed *vacuously* at wave 1 with zero callers,
   because the defining module names it three times in a section comment and a
   docstring example (`02ef6fe`), and the first fix was still incomplete because
   without a trailing paren it counted an import line and a prose mention as well
   (`f0e24fb`). The `load_dotenv` check matched a `.. code-block:: python` example
   inside `env_store`'s own docstring. The `submit_feedback_issue` check was
   unsatisfiable twice over: "exactly one `handlers.py` use" cannot hold when the
   frozen call site names the bare symbol, and a paren-anchored pattern misses it too,
   because the call goes through `functools.partial`. The sixth is a different defect
   in the same block: the phase-boundary grep still listed `src/pmcp/types.py` after
   the freeze had *mandated* the one optional field there, so it would have flagged
   the phase's own interface as a boundary crossing (`6a50e16`, which records its
   three as the fourth, fifth and sixth). **A plan-authoring lesson, not an execution
   one**: a structural check is code, and a check nobody has run is a check nobody has
   tested.

6. **Where this roadmap's EGRESS text is contradicted by what shipped** (original
   text left unedited, as in the three amendment blocks above).
   - (a) **Scope notes: "Decompose into 2 lanes with disjoint concerns in one file."**
     Four implementation lanes shipped, five with SL-docs, and three of them are not
     in that file. The gate the phase turns on is a new module and its own lane
     (SL-2, `src/pmcp/feedback_egress.py`); the provenance names every other lane
     codes against are a preamble lane and the DAG's only root (SL-1,
     `src/pmcp/env_store.py` and `src/pmcp/types.py`); the operator flag, the CLI verb
     and the startup-load recording are a third (SL-3, `src/pmcp/config/guidance.py`
     and `src/pmcp/cli.py`); and only SL-4 writes `handlers.py`. This is CONSENT
     amendment 6's finding again: **a roadmap that names a shared interface as a
     phase output should count it as a lane.**
   - (b) **Scope notes: "Both touch `tools/handlers.py:4796-4992`."** Stale before
     execution began. `submit_feedback` was `:4995-5191`, and `:4796-4992` was the
     tail of `auth_connect` — a lane that trusted the citation would have edited the
     wrong function. `plans/phase-plan-v13-EGRESS.md`'s Context records the
     correction; the roadmap's line is left as written.
   - (c) **DAG notes and Execution Notes: EGRESS is "parallel with all of them."**
     True of the criteria, not of the files. EGRESS SL-4 writes `handlers.py`, which
     PKGID also writes; SL-3 writes `cli.py`, which PKGID also wrote; and SL-docs
     owns the same four documentation files as CONSENT's and PKGID's docs lanes —
     the pairwise collision this roadmap's own Execution Notes already record for
     CONSENT and PKGID, and which applies to EGRESS identically. EGRESS was executed
     after PKGID merged, which is what made it safe. The Execution Notes' "written by
     PKGID lane A and both EGRESS lanes" is right about the hazard and wrong about the
     count: `handlers.py` had exactly one EGRESS writer.
   - (d) **Key files, evidence paths and the `## Verification` block name one test
     file.** Key files name `src/pmcp/tools/handlers.py` and
     `tests/test_feedback_egress.py`; the spec-closeout evidence paths name that test
     file and `CHANGELOG.md`; `## Verification` runs `uv run pytest -q
     tests/test_feedback_egress.py` alone. The criteria are proven across **four**
     test files — that one plus `tests/test_feedback_egress_gate.py`,
     `tests/test_feedback_provenance.py` and
     `tests/test_feedback_submission_flag.py` — and **six** source files:
     `src/pmcp/feedback_egress.py`, `src/pmcp/env_store.py`, `src/pmcp/types.py`,
     `src/pmcp/config/guidance.py`, `src/pmcp/cli.py` and
     `src/pmcp/tools/handlers.py`. This is CONSENT amendment 5 and PKGID amendment
     11(e) for the third time. **SEAL's closeout should collect all four test files**,
     and the roadmap's `## Verification` block should run them.
   - (e) **The exit criteria have no room for an unknown.** EC-EGRESS-2 and
     EC-EGRESS-4 frame the outcome as submitted or not submitted. A POST whose bytes
     were sent and whose response never arrived is neither: the issue may exist, and
     reporting it as "not submitted" with a pre-filled compose URL is how the same
     issue gets filed twice. The phase therefore adds exactly one optional output
     field, `SubmitFeedbackOutput.submission_outcome` (`src/pmcp/types.py`), whose
     `dispatched_unconfirmed` value carries that state and whose `issue_url` is a
     **search** URL rather than a compose URL. Every existing field and every existing
     assertion is untouched; the field defaults to `None`.
   - (f) **`## Top Interface-Freeze Gates`: IF-0-EGRESS-1 is "the credential and
     consent predicate … and the preview payload shape returned when it refuses."**
     Accurate but incomplete in the way that matters: the gate also resolves and
     validates the **destination**, and item 1 is why it must do so first. A reader
     who takes the one-line summary as the gate's scope will not expect
     `untrusted_repository_override` or `invalid_repository` in its vocabulary.

7. **`SECURITY.md` is deliberately not updated by this phase**, as it was not by
   TRUST, CONSENT or PKGID. The roadmap's Execution Notes assign the trust-model
   write-up to SEAL, once, "to avoid four partial descriptions of one model", and
   IF-0-SEAL-1 binds each documented claim to a proving test. The operator-facing
   behaviour of this phase is in `CHANGELOG.md` and `README.md`; the model it belongs
   to is SEAL's to state.

### Phase 5 — Document and prove the model (SEAL)

**Objective**
State the trust model in SECURITY.md exactly as implemented, and prove the four
boundaries hold together against an adversarial end-to-end suite.

**Exit criteria**
- [ ] EC-SEAL-1 — SECURITY.md describes the implemented model with no claim the tests do not prove; each claim cites the test that proves it.
- [ ] EC-SEAL-2 — an adversarial suite drives the four review reproductions end to end and each fails closed, run against the real handlers rather than mocks.
- [ ] EC-SEAL-5 — the **composition** cases fail closed too, not only the four original reproductions: an unapproved overlay that adds a server (EC-CONSENT-6), and an approval record shipped inside the checkout (EC-TRUST-5). Both are seams between phases that each pass their own criteria, which is exactly what a per-phase suite cannot catch.
- [ ] EC-SEAL-3 — every refusal carries a remedy that is runnable as printed; every `pmcp` command a remedy names is a verb the CLI dispatches; and the refusals for which no `pmcp` verb exists are enumerated exhaustively from each gate's closed reason vocabulary, so that set can shrink but never grow silently. **Amended wording, under the original id, by SEAL's `canonical_spec_update` closeout (2026-09-17):** the original read "every refusal path prints the exact `pmcp` command that would grant the action, asserted for each gate", which is not satisfiable without inventing operator verbs for a policy-file edit, an unresolvable identity, an unpinned argv and a planted credential. The operator chose amendment over new behaviour, and what is proven is strictly stronger than a single unverifiable string. See the SEAL post-execution amendments, item 6.
- [ ] EC-SEAL-4 — a fresh operator with no trust store and no project files sees unchanged behaviour for manifest-backed servers.

**Scope notes**
Decompose into 2 lanes with disjoint files: **lane A** owns SECURITY.md and the
CHANGELOG narrative, **lane B** owns the adversarial end-to-end suite. Lane B is the
one that can fail late, so start it as soon as PKGID and CONSENT publish their gates
rather than waiting for this phase to open.

**Non-goals**
New gates. This phase proves and documents; it does not add behaviour.

**Key files** *(corrected under SEAL's `canonical_spec_update` closeout, 2026-09-17,
to the set that shipped; the plan named three)*
- `SECURITY.md`, and its checker `scripts/check_security_claims.py` (new)
- `CHANGELOG.md`, `README.md`, `.claude/docs-catalog.json`, `specs/phase-plans-v13.md`
- `src/pmcp/manifest/installer.py`, `src/pmcp/tools/handlers.py` (SL-0)
- `src/pmcp/env_store.py`, `src/pmcp/config/loader.py`, `src/pmcp/manifest/loader.py` (SL-6)
- `src/pmcp/trust_store.py`, `src/pmcp/cli.py` (SL-6 and SL-7)
- the nine test files enumerated in the SEAL post-execution amendments, item 6(b)

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

### Post-execution amendments — SEAL (2026-09-17)

Recorded by SL-docs after SL-0 through SL-7 landed and were merged. All five exit
criteria shipped, and neither freeze gate was reopened. This block is unlike the
four above in one way that matters: SEAL's spec-closeout decision is
`canonical_spec_update`, not `no_spec_delta`, so its closeout does not only append a
record — **it edits the canonical spec to match what shipped.** Four such edits were
owed and are made in this same commit: EC-TRUST-5's wording (item 5), EC-SEAL-3's
wording (item 6 and the criterion line itself), the SEAL **Key files** list, and the
`## Verification` block. Every line reference is to `85cee51`.

**Items 1, 4 and 5 are the ones a reader relying on this roadmap pays for if they
are not read.**

1. **SEAL found two live reopenings of this roadmap's own findings, and both
   existed only in the seam between two phases.** Each merged phase passed its own
   acceptance criteria; neither hole was reachable from any single phase's suite,
   which is exactly the class EC-SEAL-5's composition suite exists to catch and no
   per-phase suite could. The operator authorised fixing both in-phase rather than
   filing them, on the same rule SL-0 was taken under: **a reproduction that does
   not fail closed is a criterion outranking a non-goal.** Neither is new behaviour
   — each restores a boundary this roadmap already claims — so SEAL's "no new gates"
   non-goal is not breached; a genuine bypass of a shipped boundary is not a gate.
   - **#250 (SL-6): a checkout could redirect the gateway through
     `PMCP_MANIFEST_PATH`, `PMCP_CONFIG` or `PMCP_POLICY`.** All three were honoured
     unconditionally, on the premise that a set variable was the operator speaking.
     But `cli.load_startup_env` reads `.env` and `.env.pmcp` before arg parsing, so
     a repository could set any of the three through its own dotenv file and get an
     ungated manifest overlay, config or policy — S-03 and S-11 through the back
     door. Now each is honoured only when provenance shows the operator exported it:
     `env_key_is_operator_supplied` (`src/pmcp/env_store.py:286`) is consulted at the
     manifest door (`src/pmcp/manifest/loader.py:712`), the four config readers
     (`src/pmcp/config/loader.py:39`, called at `:341`, `:1035`, `:1094`, `:1141`)
     and the startup door for config and policy
     (`resolve_env_config_and_policy`, `src/pmcp/cli.py:2285`, called at `:2438`). A
     checkout-sourced value is skipped exactly as if unset and the skip is logged
     operator-safe (`describe_ignored_trust_env_var`, `src/pmcp/env_store.py:322`); an
     exported value still applies.
   - **#251 (SL-7): the trust-store residency guard keyed on the working directory.**
     A store resolving inside a checkout is refused so a repository cannot ship its
     own approval record — but the guard found the checkout by walking up from
     `Path.cwd()`, so `pmcp serve --project <checkout>` launched from elsewhere did
     not refuse a store planted inside the *served* checkout and would load its
     self-approved `.mcp.json`. The guard now keys on the served project root ∪ the
     launch checkout: `set_active_project_root` (`src/pmcp/trust_store.py:87`) is
     bound by `run_server` (`src/pmcp/cli.py:2430`) and `run_status` (`:1381`, cleared
     at `:1387`), and `_checkout_roots` (`:108`) unions it with the cwd walk — added
     to the walk, never replacing it, so a store resident in a second checkout the
     operator launches from stays refused too. `trust_store_path` (`:155`) raises for
     any of them.
   - **Consequence for the SEAL plan's own `## Verification`, which this block
     supersedes because that plan file is frozen.** `plans/phase-plan-v13-SEAL.md`
     asserts the `src/` diff is **exactly** `installer.py` and `handlers.py` (SL-0's
     two). SL-6 and SL-7 added five more, so the real v13-SEAL `src/` set is seven:
     `src/pmcp/manifest/installer.py` and `src/pmcp/tools/handlers.py` (SL-0),
     `src/pmcp/env_store.py`, `src/pmcp/config/loader.py` and
     `src/pmcp/manifest/loader.py` (SL-6), and `src/pmcp/trust_store.py` and
     `src/pmcp/cli.py` (SL-6 and SL-7). The plan's two-file assertion was correct for
     the phase as planned and is amended here, in the only closeout permitted to edit
     the roadmap, rather than in the plan.

2. **The claim-binding contract (EC-SEAL-1) is mechanical, not a review promise.**
   `scripts/check_security_claims.py` (SL-1) parses a delimited claim region and a
   ledger out of `SECURITY.md` and enforces twenty-two frozen rules: every asserting
   sentence carries a marker and every marker a ledger row; a guarantee cites a test
   that exists, resolves under `ast.parse` and — the rule the second plan panel added
   — is a **subset of what `pytest tests/` actually discovers**, so a proof CI would
   never run fails the checker rather than shipping green; a limitation is labelled
   and may cite only a `characterizes:` list. The checker runs in the normal CI
   suite. What it finally makes **required** is the eleven lane-owned tests no merged
   phase's acceptance criteria ran — the CONSENT-amendment-1 defect (ten in CONSENT,
   one in TRUST), including `read_and_gate`'s one-read TOCTOU guard and the
   redaction-widening rule's only falsifier: neither merged plan's criteria can be
   edited now, so citing each from a `guarantee` row (`SECURITY.md`, the C-08 to
   C-16 rows) is what makes it required — delete it and R8 fails, break it and CI's
   suite fails. The shipped ledger is 25 guarantees and 12 labelled limitations
   (`scripts/check_security_claims.py:92`, the frozen census; `:128`, the eleven
   previously-unproven node ids).

3. **The recurring measurement error, now named across the roadmap: a check or a
   plan measurement that counts one shape of occurrence and reports it as the
   total.** Two instances are verifiable in the tree and the amendments:
   - **EGRESS's `## Verification` greps counted a name, not a call.** EGRESS
     amendment 5 records six corrections to that phase's own checks, five of them
     one defect: a grep asserting a *count* matched a symbol in a section comment, a
     docstring code-block, an import line, or a `functools.partial` wrapper rather
     than a call site, so a check passed vacuously (zero real callers) or missed the
     one real one.
   - **SL-0's blast-radius measurement counted production call sites and missed the
     test surface.** The SEAL plan measured "55 existing call sites keep their
     current behaviour and no existing assertion moves" and threaded an optional
     parameter on that basis. Correct for production, but it did not count the arity
     assertion that pins the signature (`tests/test_package_identity_gate.py`) or the
     `JobManager` test doubles that re-declare `start_install`
     (`tests/test_pkgid_panel_fixes.py`, `tests/test_tools.py`,
     `tests/test_credential_child_env.py`); all four had to move (commits `72c6275`,
     `2245809`, `6f69e26`).
   - **The lesson, stated plainly:** the check that catches this class is running the
     thing, not writing a better grep. It is why EC-SEAL-1's R9 pins the cited union
     as a subset of what CI *discovers* rather than of what a pattern matches, and
     why EC-SEAL-2/5 drive the real handlers rather than the predicates.
   - **No cross-phase total is asserted here, on purpose.** The class recurred —
     EGRESS documented six corrections in one block, all "match the call, not the
     name"; SEAL added the distinct blast-radius instance above — but an earlier
     draft of this item carried a single roadmap-wide count and a "PKGID ×3"
     attribution that the PKGID amendment does not support: it records no such
     defect. An amendment about measurements reported without being verified must
     not itself ship an unverified count, so the count was dropped rather than
     guessed. That is the discipline the item is about.

4. **Open, filed, not fixed — each out of SEAL's scope, with why.** None is a
   bypass of a shipped boundary; each is a follow-up, a merged-phase file, or new
   behaviour the non-goal excludes. All are documented as `limitation` claims in
   `SECURITY.md`.
   - **#247** — the npm version-check User-Agent still names the old repository
     (`src/pmcp/manifest/version_checker.py:24`). SL-5 fixed the `SECURITY.md`
     occurrence; the source string is a merged-phase file and a `limitation`
     (`SECURITY.md`, C-34). The README `mcp-name` comment and `server.json` name are
     the same class — a registry identity validated as a pair — and are not this
     lane's to split.
   - **#248** — the approval-store write is not atomic and can truncate before it
     finishes (`limitation` C-35). A durable-write fix is behaviour.
   - **#252** — `pmcp trust approve` resolves the store from the working directory
     while `serve --project` now resolves it from the served root (SL-7), so the two
     can disagree (`limitation` C-36). Closing the asymmetry means changing the
     `approve` verb's resolution, which is behaviour.
   - **The standing limitations `SECURITY.md` documents**, each a `limitation` row
     rather than a fix because closing it is a gate or a network call SEAL's charter
     excludes: an operator approval binds no integrity digest (C-27); no per-socket
     timeout is a total request bound (C-30); the `"denied"` decision is reserved but
     no shipped verb writes it (C-26); `auth_connect` for a name in neither table
     admits a credential-shaped override the gateway holds but no child inherits
     (C-29); and `set_startup_policy` rewrites the selected source and silently
     invalidates a content-keyed approval, so the operator learns only at the next
     startup's consent refusal (C-28, filed as Consiliency/pmcp#253 — the SL-docs
     plan section directed this lane to file it; a documented `limitation` because
     adding a diagnostic to an operator-initiated write is behaviour SEAL's non-goal
     excludes).

5. **EC-TRUST-5's "refused at startup" wording is corrected here, and the criterion
   line is edited in place.** This is a `canonical_spec_update` edit into Phase 1's
   text, whose own amendment block states nothing was edited; it is made under SEAL's
   closeout authority and dated, and is the one kind of cross-phase edit this
   decision permits. The original read "a store path inside the current checkout is
   **refused at startup**". Two things are truer: the residency guard raises **per
   read** of the store (`trust_store_path`, `src/pmcp/trust_store.py:155`), not once
   at boot, so a checkout-resident store is genuinely refused but the refusal reaches
   the operator through the consent gate's "not approved / run `pmcp trust approve`"
   message rather than as a distinct startup abort; and after SL-7 the checkout it is
   judged against is the served project root ∪ the launch checkout, not `cwd` alone.
   The behaviour is a `limitation` in the ledger (`SECURITY.md`, C-33) beside the
   guarantee that the refusal itself holds (C-15).

6. **The three roadmap corrections this closeout owes, that no other phase could
   make.**
   - (a) **The `## Verification` block ran a test file that never existed.** It named
     `tests/test_project_source_consent.py`, which a single lane could never own and
     was never created (CONSENT amendment 5). Corrected in this commit to the real
     evidence set.
   - (b) **The closeout test-file census, recorded in full.** The nineteen v13
     evidence files SL-5's checker now enforces
     (`scripts/check_security_claims.py:92`) are: `test_trust_store.py`,
     `test_trust_cli.py`, `test_package_identity.py` (TRUST);
     `test_project_consent_gate.py`, `test_project_source_consent_manifest.py`,
     `test_project_source_consent_config.py`, `test_project_source_consent_policy.py`
     (CONSENT); `test_package_identity_gate.py`, `test_package_approvals.py`,
     `test_policy_package_identifiers.py`, `test_install_argv_logging.py`,
     `test_pkgid_panel_fixes.py`, `test_pkgid_spawn_logging.py`,
     `test_pkgid_manifest_npx_selectors.py` (PKGID); `test_feedback_egress.py`,
     `test_feedback_egress_gate.py`, `test_feedback_provenance.py`,
     `test_feedback_submission_flag.py`, `test_egress_panel_fixes.py` (EGRESS). SEAL
     added **nine** test files, not the seven the plan named before SL-6 and SL-7:
     the seven planned — `test_security_claims_parser.py` and `test_security_claims.py`
     (the checker's own tests, cited by nothing by design),
     `test_trust_boundaries_e2e.py`, `test_trust_boundaries_composition.py`,
     `test_refusal_remedies.py`, `test_fresh_operator_baseline.py`,
     `test_install_child_env_project_root.py` — plus `test_env_overlay_provenance.py`
     (SL-6, #250) and `test_trust_store_residency_root.py` (SL-7, #251).
     `tests/test_env_leak_229.py` is cited by the ledger but is deliberately **not**
     census: it predates v13 (Consiliency/pmcp#229). Phase 5's **Key files** entry is
     corrected in place to name the shipped set.
   - (c) **Two amendment-block undercounts SL-0 found while building the census.**
     TRUST's block records **no** evidence-path gap at all, though the phase added
     three test files where the roadmap's evidence path names one; and EGRESS
     amendment 6(d) — the block that records the undercount pattern for the third
     time — is itself short by one, omitting `tests/test_egress_panel_fixes.py` from
     its list of four. Recording both in the canonical spec, rather than in a fifth
     amendment a sixth phase would have to read, is what `canonical_spec_update` is
     for.

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
# Each phase's evidence suite -- the real set, corrected here under SEAL's
# canonical_spec_update closeout. This block previously named
# tests/test_project_source_consent.py, which never existed and was never
# creatable (CONSENT amendment 5). The v13 evidence set is the nineteen files from
# the four merged phases (the census SL-5's checker enforces) plus SEAL's own.
uv run pytest -q tests/test_trust_store.py tests/test_trust_cli.py \
                tests/test_package_identity.py tests/test_project_consent_gate.py \
                tests/test_project_source_consent_manifest.py \
                tests/test_project_source_consent_config.py \
                tests/test_project_source_consent_policy.py \
                tests/test_package_identity_gate.py tests/test_package_approvals.py \
                tests/test_policy_package_identifiers.py tests/test_install_argv_logging.py \
                tests/test_pkgid_panel_fixes.py tests/test_pkgid_spawn_logging.py \
                tests/test_pkgid_manifest_npx_selectors.py tests/test_feedback_egress.py \
                tests/test_feedback_egress_gate.py tests/test_feedback_provenance.py \
                tests/test_feedback_submission_flag.py tests/test_egress_panel_fixes.py

# SEAL's own suites: the four review reproductions and the composition seams that
# must fail closed, the refusal-remedy and fresh-operator audits, the two
# between-phase reopenings SEAL found and fixed (SL-6 #250, SL-7 #251), and the
# claim-ledger checker's own tests.
uv run pytest -q tests/test_trust_boundaries_e2e.py tests/test_trust_boundaries_composition.py \
                tests/test_refusal_remedies.py tests/test_fresh_operator_baseline.py \
                tests/test_install_child_env_project_root.py \
                tests/test_env_overlay_provenance.py tests/test_trust_store_residency_root.py \
                tests/test_security_claims_parser.py tests/test_security_claims.py

# No regression for an operator with no trust store and no project files
uv run pytest -q tests/                 # compare counts to the pre-roadmap baseline
uv run ruff check src/ tests/ scripts/ && uv run ruff format --check src/ tests/ scripts/
uv run mypy src/
uv run python scripts/check_workflows.py --base-ref origin/main

# EC-SEAL-1: the documented model is bound to its proofs, run as a reviewer runs it.
uv run python scripts/check_security_claims.py
uv run python scripts/check_security_claims.py --run

# The roadmap pin is fresh in all five v13 plans. SL-docs edits this file, so its
# digest changes and every plan's roadmap_sha256 is re-pinned in the same commit;
# no .github workflow runs this checker, so this is the only place a stale pin is
# caught.
python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md
```

Host note: `/tmp/package.json` makes ~107 npm-identity tests fail on some hosts
(`tests/conftest.py`); compare the same command from the same directory before and
after, never against a clean-machine expectation.
