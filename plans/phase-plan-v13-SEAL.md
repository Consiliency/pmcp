---
phase_loop_plan_version: 1
phase: SEAL
roadmap: specs/phase-plans-v13.md
roadmap_sha256: 7f8362022aa1ad953a9d2faf9ec842633ed14aaebc924385f7da1beb6d73df72
---

# PHASE-5-SEAL: Document and prove the model

## Context

SEAL is the last phase of the v13 trust-boundary roadmap (see Consiliency/pmcp#230).
TRUST, CONSENT, PKGID and EGRESS are all merged; this worktree is based on `d455a1f`.
Every line reference below was re-verified against that tree, and every behavioural
claim was run rather than read — four phases of drift have moved the line numbers the
amendment blocks cite, and two of the things those blocks hand to SEAL turned out to be
already fixed or already false.

**What this phase adds: one behaviour change, named and bounded, and nothing else.**
SEAL writes `SECURITY.md`, one script under `scripts/`, seven test files, the
documentation set, and — by operator decision after the plan panel — one narrow fix to
the install-spawn credential strip in `src/pmcp/manifest/installer.py` and
`src/pmcp/tools/handlers.py` (SL-0, Context finding 1). `## Verification` asserts the
`src/` diff is **exactly those two files**, rather than empty, because "the phase proves
and documents" is a property a reviewer should be able to check rather than trust, and an
authorised exception that is named is safer than a blanket prohibition nobody can verify
was kept.

**Why the exception was taken, since the non-goal is explicit.** The strip defect is a
live credential leak at an existing boundary: an install child inherits another server's
PMCP-stored credential whenever the gateway's project root and its working directory
differ. EC-SEAL-5 is the criterion that reaches it. This phase's own escalation rule
already anticipated the case — **if a reproduction does not fail closed, a criterion
outranks a non-goal** — and that rule still governs everything else: if any other SL-2 or
SL-3 reproduction fails to fail closed, the lane stops and reports rather than adjusting
the test until it passes.

### The four review reproductions, and where a real handler reaches each gate

The roadmap's EC-SEAL-2 says "run against the real handlers rather than mocks". PKGID
amendment 1 and EGRESS amendment 1 are the same lesson twice: **enumerate the doors, not
the function you were looking at.** PKGID shipped a gate-level test that supplied an
identity production never supplies; EGRESS's red run reached a live `gh issue create` on
the development host precisely because it drove the real path. Both call sites below were
read out of the tree, not taken from the amendments.

| Finding | Gate | Production call sites the suite must drive |
|---|---|---|
| **S-01** | `evaluate_provision` (`src/pmcp/provision_gate.py:447`) | the lifecycle resolver (`src/pmcp/tools/handlers.py:3522`, covering `connect_server`, `restart_server`, `update_server`), `provision` (`:4467`), and `register_discovered_server`'s preview (`:5947`) |
| **S-03** | `read_and_gate` (`src/pmcp/project_consent.py:258`) | the manifest overlay walk-up (`src/pmcp/manifest/loader.py:839`) and project `.mcp.json` (`src/pmcp/config/loader.py:376`) |
| **S-11** | `read_and_gate` | project policy discovery (`src/pmcp/policy/policy.py:203`) |
| **S-04** | `evaluate_feedback_egress` (`src/pmcp/feedback_egress.py`) | `gateway.submit_feedback` |

Four gates, five consent/provision entry points and one egress handler. A suite that
drives `evaluate_provision` directly proves nothing this roadmap has not already proved
three times; the node ids frozen below name the handler, never the predicate.

### What the earlier phases deferred to SEAL — every item, verified, with its decision

The seven items below were found by grepping the four amendment blocks for `SEAL` and
then checking each against the code. Two of them are **no longer true as written**. The
decision column is this plan's answer to the lead's question: document it, fix it, or
file it.

| # | Deferral | Verified status in `d455a1f` | SEAL's decision |
|---|---|---|---|
| 1 | The reserved `"denied"` decision value (TRUST amendment 1) | True. `DECISIONS = frozenset({APPROVED, DENIED})` (`src/pmcp/trust_store.py:46-52`); `run_trust` dispatches `approve`, `list`, `revoke`, `approve-package`, `list-packages`, `revoke-package` and nothing writes `"denied"` (`src/pmcp/cli.py:2646-2652`). `package_approvals.py:51-55` reserves the same value on the same terms. | **Document.** TRUST said explicitly: either a later phase adds the verb, or SEAL documents the value as reserved. No phase needed it, absence already refuses, and adding a verb whose records no caller reads is a behaviour change SEAL has no criterion for. It becomes a `limitation` claim in the ledger, beside the guarantee that cites TRUST's own falsifier for it — R17 below. |
| 2 | The unquoted remediation string (CONSENT amendment 10a) | **STALE — already fixed, in CONSENT's own merge.** `project_consent._operator_safe` (`:118`) is escape-then-`shlex.quote`, applied at `:189`, and `_approval_path` handles the harder case the quoting alone cannot (a non-printable target renders identically to a different printable file, so the unresolved source is named instead). Run in this worktree: a path with a space renders `'/path/with a space/.mcp.json'`. `git log -S shlex.quote -- src/pmcp/project_consent.py` returns only `9979f23`, the CONSENT merge. | **Document, and prove.** Nothing to fix; the amendment describes the frozen string, not the shipped one. The quoting rule across all four gates is stated in EC-SEAL-3's audit and asserted by two named tests. Reported upward as an amendment defect. |
| 3 | `set_startup_policy` silently revokes an approval (CONSENT amendment 10b) | True. `set_startup_policy` (`src/pmcp/config/loader.py:694`) rewrites the selected source through `_atomic_write_json` (`:683`, called at `:772`), consults no trust store, and its `StartupPolicyPreview` carries no notice. Approval is keyed on content, so the operator's own record is invalidated. | **Document and file.** No SEAL criterion demands it: it is an operator-initiated write, not a refusal path, so EC-SEAL-3 does not reach it, and adding a diagnostic is behaviour. The operator **is** told, one step late — the next startup's consent refusal names `pmcp trust approve <path>`, which EC-SEAL-3's audit proves. A `limitation` claim states the trap, and SL-docs files the issue. |
| 4 | Operator approvals never bind integrity (PKGID amendment 8) | True, and narrower than the amendment says. `approve_package` stores `identity.integrity or None` (`src/pmcp/package_approvals.py:289`), and the **only** caller in `src/` is the CLI, which constructs `PackageIdentity(..., integrity=None)` (`src/pmcp/cli.py:2610`). `is_package_approved` compares digests only when both sides carry one (`:326-331`), so the comparison is inert for every record the shipped writer creates. | **Document.** Binding integrity means resolving the package online at approval time — a network call in an offline CLI verb, a new refusal when the registry disagrees, and a migration for every existing record. That is a gate, and SEAL's non-goal names gates. It becomes the ledger's sharpest `limitation`: the pin is `name@version`, and the bytes behind it are whatever the registry serves. SL-docs files it as the successor work. |
| 5 | `auth_connect` for a server name in neither table (EGRESS residual after F1) | True, and the exploit path is narrower than the amendment implies. `from_discovered` is false for an unknown name, so only `env_var_allowed(env_var, None)` applies (`src/pmcp/validation.py:176`) and any credential-shaped name is admitted — `NPM_CONFIG__AUTH` among them. But `_write_secret` runs **before** the `os.environ` write (`src/pmcp/tools/handlers.py:4996`), so the value is always in a PMCP store, and `sanitized_subprocess_env` strips managed keys from every child (`src/pmcp/env_store.py:328-358`). No spawned server inherits it; the gateway process does. Names that redirect npm's *resolution* rather than its auth (`npm_config_registry`) are not credential-shaped and are refused. | **Document.** The residual is a credential the gateway holds and no child receives. Closing it means an allowlist for unknown server names, which is a gate. A `limitation` claim states exactly what survives. **But see the new finding below** — the strip had a hole of its own, and that one is FIXED here by SL-0, which is why this row can say plainly that no spawned server inherits it. |
| 6 | Two EGRESS limits (EGRESS amendment 4) | Both true. (a) No per-socket timeout is a total request bound, so an abandoned worker holds one of anyio's default thread-limiter tokens until it returns; it is forbidden to send by then, so it can occupy but not act. (b) A store file this process never read, written and removed out of band, is invisible to all three provenance sources. | **Document, both.** (a) is a resource-exhaustion residual reachable only by a peer that stalls below its own socket threshold; a concurrency guard is behaviour. (b) requires an out-of-band writer on the same machine and is not reachable from any gateway tool. Two `limitation` claims. Neither is filed: EGRESS already recorded both as known limits, and the ledger is now where they live. |
| 7 | The closeout collection requests (CONSENT 5, PKGID 11(e), EGRESS 6(d)) | Answered below, and **two of the three amendment lists are themselves short**. | **Fix, in the closeout.** SEAL's `canonical_spec_update` decision is the only one of the five that can correct the roadmap, so the census below goes into `specs/phase-plans-v13.md` and into the roadmap's `## Verification` block. |

### The evidence census — enumerated from the tree, then checked against the amendments

Built with `git log --diff-filter=A --name-only aa2de50..d455a1f -- tests/` and
attributed by each file's adding commit, which is the direction that finds an
undercount; reading the amendments and looking for those files cannot.

| Phase | Merge | Test files the phase actually added | Amendment's count |
|---|---|---|---|
| TRUST | `9ae65f0` | `tests/test_trust_store.py`, `tests/test_trust_cli.py`, `tests/test_package_identity.py` | **none recorded — the roadmap's evidence path names one file, and no TRUST amendment records the gap** |
| CONSENT | `9979f23` | `tests/test_project_consent_gate.py`, `tests/test_project_source_consent_manifest.py`, `tests/test_project_source_consent_config.py`, `tests/test_project_source_consent_policy.py` | 4 — correct (amendment 5) |
| PKGID | `5c7e0a1` | `tests/test_package_identity_gate.py`, `tests/test_package_approvals.py`, `tests/test_policy_package_identifiers.py`, `tests/test_install_argv_logging.py`, `tests/test_pkgid_panel_fixes.py`, `tests/test_pkgid_spawn_logging.py`, `tests/test_pkgid_manifest_npx_selectors.py` | 7 — correct (amendment 11(e)) |
| EGRESS | `d455a1f` | `tests/test_feedback_egress.py`, `tests/test_feedback_egress_gate.py`, `tests/test_feedback_provenance.py`, `tests/test_feedback_submission_flag.py`, `tests/test_egress_panel_fixes.py` | **4 — short by one** (amendment 6(d) omits `tests/test_egress_panel_fixes.py`) |

**Nineteen files, not the four the roadmap's Key files name.** Two new undercounts:
TRUST never recorded one at all, and EGRESS's amendment — the block that records the
undercount pattern for the third time — is itself short by the file its own panel-fix
pass added. That is the fourth recurrence, and it is why this phase does not accept a
hand-maintained list: `scripts/check_security_claims.py` carries the census as a frozen
constant and **refuses a `SECURITY.md` that does not cite every file in it** (rule R16).
A future v13-era test file added without a claim fails the check.

`tests/conftest.py` is not an evidence path. It is a shared seam — CONSENT SL-1 wrote it,
PKGID's repair pass wrote it again — and it carries the isolation every suite in this
phase inherits: `isolate_trust_store` (`:62`, autouse HOME redirect, approves nothing),
`_no_live_npm_registry` (`:168`, autouse, fails any test whose identity lookup opens the
real registry) and `fake_npm_registry` (`:195`). `tests/test_env_leak_229.py` predates
v13 (`87a01e4`, #229) and is likewise not v13 evidence, though EGRESS's SL-1 runs it.

### Three findings this planning pass made, none of them deferred to us

1. **`build_install_child_env` is the only `sanitized_subprocess_env` caller that does
   not pass the gateway's project root.** `src/pmcp/manifest/installer.py:618` calls
   `sanitized_subprocess_env(own_env)` with no `project`, so
   `managed_secret_keys(None)` resolves the project store by walking up from
   `Path.cwd()` (`src/pmcp/env_store.py:286-300`, `resolve_project_root` at `:26-35`).
   Every other caller passes it: `src/pmcp/client/manager.py:2364`,
   `src/pmcp/tools/handlers.py:5440` and `:5548`. `_write_secret` writes through
   `self._project_root` (`handlers.py:3137`), and `pmcp serve --project` sets that root
   explicitly (`src/pmcp/cli.py:2378`). **Under `pmcp --project X` run from a different
   working directory, a project-scope credential is written to `X/.env.pmcp` and is not
   stripped from an install spawn.** This is EGRESS's
   `test_the_gate_is_asked_about_the_project_root_the_secret_writer_uses` finding at a
   second site, and it is the reason deferral 5's residual could not be documented as
   "no spawned server inherits it" without qualification. **FIXED IN THIS PHASE, by
   operator decision after the plan panel** — the first revision filed it, on the
   reasoning that SEAL writes no `src/`. The operator overruled that: this is a live
   credential leak at an existing boundary, EC-SEAL-5 is the criterion that reaches it,
   and a documentation phase may not write a leak down and walk past it. **SL-0** owns
   the change (two files, one threaded argument) and the tests that drive the real child
   environment with the working directory and the project root deliberately different;
   SL-3's two strip tests are rewritten to assert the fixed behaviour. The ledger then
   states the guarantee at the width the fix makes true — the strip resolves the project
   store from the root the gateway was given — with no limitation left to write for it.
2. **`SECURITY.md:201` points vulnerability reports at `ViperJuice/pmcp`**, a repository
   this project does not own — the same drift EGRESS corrected for `submit_feedback` and
   #247 tracks for the npm User-Agent. SEAL is the sole writer of `SECURITY.md`, so this
   occurrence is in charter and SL-5 fixes it, asserted against the packaged
   distribution metadata rather than a literal. `src/pmcp/manifest/version_checker.py:24`
   stays with #247.
3. **EC-SEAL-3 is not literally satisfiable, and should not be weakened.** Read out of
   the tree, several refusals name no `pmcp` verb because none exists:
   `provision_gate._denied_remedy` and `_undetermined_remedy` say to edit the operator's
   policy file; `_unresolvable_remedy` names the gateway tool
   `gateway.register_discovered_server`; `_unpinned_remedy` says to set the pin in
   `.mcp.json`; `feedback_egress._store_paths_remedy` names two store paths and a shell
   export, deliberately, because `pmcp secrets` ships `set`, `sync` and `check` only.
   Inventing verbs for these is a behaviour change and a new operator surface. What SEAL
   proves instead is the strongest true statement, and it is stronger than the literal
   one in the way that matters: **every refusal carries a non-empty remedy; every remedy
   is runnable as printed; every `pmcp` command any remedy names is a verb the CLI
   actually dispatches; and the set of remedies that name no `pmcp` verb is enumerated
   and asserted exhaustively**, so a new one cannot appear unnoticed. **The operator chose
   amendment over new behaviour after the plan panel**: SL-docs amends EC-SEAL-3's text
   in `specs/phase-plans-v13.md` to what is provable, under the same id, and this plan's
   acceptance criterion carries the amended wording with the original recorded beside it.
   SEAL's `canonical_spec_update` closeout is the only one of the five that may make that
   edit, which is why it is possible here and was not in any earlier phase.

### Standing issues

**#247** (npm User-Agent names `ViperJuice/pmcp`) and **#248** (`write_env_file`
truncates before writing) both **stay filed**. Neither is a v13 trust-boundary property:
#247 labels outbound requests and #248 is durability of the operator's own credentials,
and EGRESS's amendment already records that its gate fails closed if a store read raises.
Neither is cited by any claim, and R16's census is over test files, not issues, so
neither blocks the ledger. The `SECURITY.md` half of #247's drift is fixed here, as
finding 2 above states, because SEAL owns that file.

## Interface Freeze Gates

- [ ] **IF-0-SEAL-1 — the claim ledger: the falsifiability contract between `SECURITY.md`
  and the test suite, and the parser that enforces it.**

  EC-SEAL-1 reads "no claim the tests do not prove; each claim cites the test that proves
  it". That is a contract, not a writing task, and it is worthless unless a reviewer can
  run something that goes red when a claim loses its proof. This gate freezes the exact
  format, who parses it, and every way it fails.

  **Scope: a delimited region, and why that is the criterion operationalised rather than
  weakened.** `SECURITY.md` is 230 lines today and describes auth modes, rate limiting,
  TLS posture and ten years of protocol history. Requiring a proving test for the
  sentence "PMCP does not terminate TLS" is unsatisfiable, and a criterion nobody can
  meet gets met by pretending. The contract therefore binds **one region**, which holds
  the whole v13 trust model and nothing else; SL-5 separately sweeps the legacy prose for
  statements v13 has falsified, and two named tests pin the two that matter.

  **The region.** Exactly one occurrence each, in this order:

  ```
  <!-- TRUST-MODEL-CLAIMS: BEGIN -->
  <!-- TRUST-MODEL-CLAIMS: END -->
  ```

  Inside it, exactly four kinds of line are legal: a heading (`###`/`####`), a blank
  line, a **claim block**, and the **ledger**. Nothing else — no loose paragraph, no
  nested list, no code fence. A prose paragraph is the smuggling route for an unproven
  claim, so the format has no room for one.

  **A claim block** is a single markdown list item beginning `- ` and containing at least
  one **claim marker**, written `[C-nn]` with `nn` two digits. A block whose ledger row
  has Kind `limitation` MUST begin `- **Limitation.** `; a block whose row is a
  `guarantee` MUST NOT.

  **The ledger** is one table, delimited by exactly one occurrence each of:

  ```
  <!-- CLAIM-LEDGER: BEGIN -->
  | Claim | Kind | Proof |
  |---|---|---|
  | C-01 | guarantee | `tests/test_trust_boundaries_e2e.py::test_s01_provision_refuses_an_arbitrary_package_under_an_allowlisted_name` |
  | C-02 | limitation | — |
  <!-- CLAIM-LEDGER: END -->
  ```

  Kind is `guarantee` or `limitation`, **and there is no third value.** A "context" or
  "informational" kind is the smuggling route a reviewer cannot see: a descriptive
  statement about the model ("the store lives outside any repository") is a guarantee and
  needs a falsifier. A `limitation` states something PMCP does **not** do or cannot
  establish.

  **A limitation's Proof cell is either `—` or a characterization, and the second form is
  better.** Written `characterizes: ` followed by one or more node ids, it names the tests
  that pin **how far the limitation currently extends** — not a guarantee that it will.
  The first revision of this gate forbade citations in a limitation row outright, on the
  reasoning that a citation is what distinguishes a guarantee. That was the wrong guard.
  The risk is a guarantee **disguised** as a limitation, and what guards against it is
  R15's mandatory `**Limitation.**` label, which makes the sentence read as a negative to
  any reader, plus R19, which stops the `characterizes:` form being used to dodge R5.
  Forbidding the citation did not address that risk and cost something real: an uncited
  limitation rots invisibly. Several of this phase's limitations have genuine
  characterization tests — the two EGRESS limits, the `auth_connect` residual for a server
  name in neither table — and citing them means that **if the limitation is silently
  closed, the characterization test fails and the document must be updated**. That is the
  same argument as R17, in the one direction a limitation can be falsified.
  `--node-ids` emits characterization ids alongside guarantee ids, so R8, R9 and `--run`
  cover them identically. R16 and R17 deliberately do **not**: both require a
  **`guarantee`** row, so a phase's evidence cannot be discharged by a limitation.

  **The parser** is `scripts/check_security_claims.py` (SL-1), a standalone script with
  three modes: default `check`; `--node-ids`, printing the cited union one per line; and
  `--run`, which does `check` and then executes that union under pytest, failing if any
  test fails. It is exercised from `tests/test_security_claims.py` (SL-5) against the
  shipped file and from `tests/test_security_claims_parser.py` (SL-1) against synthetic
  fixtures — one per rule below, because EGRESS amendment 5's lesson is that **a
  structural check is code, and a check nobody has run is a check nobody has tested.**

  **What makes it fail. Every rule has a named failure and a fixture.**

  | Rule | Failure | What it catches |
  |---|---|---|
  | R1 | `region_missing` | either region delimiter absent, duplicated, or out of order |
  | R2 | `ledger_missing` | ledger delimiters absent or duplicated, or the table is not the three-column form |
  | R3 | `bad_kind` | a Kind outside `{guarantee, limitation}` |
  | R4 | `bad_claim_ids` | ids not unique, or not the contiguous sequence `C-01`…`C-NN` in table order |
  | R5 | `guarantee_without_proof` | a `guarantee` row citing no node id — **the core rule** |
  | R6 | `limitation_with_bare_proof` | a `limitation` row whose Proof is neither exactly `—` nor a `characterizes:` list of node ids |
  | R7 | `malformed_node_id` | a Proof entry not matching an inline-code `` `tests/<path>.py::test_<name>` `` |
  | R8 | `unresolvable_node_id` | the file does not exist, or the name is not a module-level `def`/`async def` in it, read with `ast.parse` — a method inside a class is rejected, because this repo's acceptance commands address module-level node ids and a class changes the id |
  | R9 | `uncollectable_node_id` | one `pytest --collect-only -q` over the union does not report an id — the gap between "a function of that name exists" and "pytest will run it" |
  | R10 | `orphan_claim` | a ledger row no claim block cites |
  | R11 | `undeclared_marker` | a marker in the region with no ledger row |
  | R12 | `unmarked_sentence` | a sentence in a claim block carrying no marker — **"no un-cited normative sentence sneaks in"** |
  | R13 | `illegal_line` | a line in the region that is not a heading, a blank, a claim block, or the ledger |
  | R14 | `ambiguous_abbreviation` | `e.g.`, `i.e.`, `cf.`, `etc.`, `vs.`, or a digit-period-digit run outside an inline code span — these are what make R12's sentence split total rather than heuristic |
  | R15 | `limitation_not_labelled` | a `limitation` block not starting `**Limitation.**`, or a `guarantee` block that does |
  | R16 | `uncited_evidence_file` | a file in the frozen v13 census (the nineteen above) that no `guarantee` row cites |
  | R17 | `uncited_unproven_test` | one of the eleven node ids that no merged phase's acceptance criteria run (ten in CONSENT, one in TRUST) that no `guarantee` row cites |
  | R18 | `v13_term_outside_the_region` | a line outside the region carrying a term from the frozen v13 lexicon and not on the frozen allowlist |
  | R19 | `characterization_in_a_guarantee` | a `guarantee` row whose Proof uses the `characterizes:` form, which would dodge R5 |

  **R12's sentence rule, in full, because a heuristic here would be evadable by
  rephrasing.** Masking runs in exactly this order: (1) inline code spans; (2) claim
  markers, replaced by a sentinel that is not a sentence terminator and is remembered;
  (3) markdown links, replaced by their link text. Markers are masked **before** links so
  `[C-07]` is not read as a bracket. The remaining text of each block is split on
  `(?<=[.!?])\s+` and at the block end, and every non-empty resulting sentence must carry
  a sentinel. R14 is what makes that split exact: with `e.g.` and bare decimals forbidden,
  there is no abbreviation the splitter can mistake for a terminator.

  The consequence is deliberate and is the whole falsifiability contract: **a rationale
  sentence repeats its own claim's marker**, which is the author stating that the cited
  tests cover that sentence too. An author who cannot say that must split the claim and
  cite a second test. There is no way to write a sentence in the region that no test is
  answerable for.

  **R17 is how the roadmap's recorded "unproven requirement" defect is finally closed,
  and it costs nothing to add.** CONSENT amendment 1 found ten lane-owned tests that no
  acceptance criterion runs — among them `read_and_gate`'s one-read guard, which *is*
  IF-0-CONSENT-1's TOCTOU defence, the two fail-closed cases, and the redaction-widening
  rule's only falsifier — and the same checker reports one more in TRUST,
  `test_a_denied_record_is_not_approved`, the falsifier for the very behaviour TRUST
  amendment 1 turns on. All eleven exist and pass; what was missing was the requirement
  that they be proven, and neither phase's acceptance criteria can be edited now without
  rewriting a merged plan. Every one of the eleven is a guarantee about the model this
  phase documents, so citing each from a `guarantee` row makes it a required proof:
  delete it and R8 fails, break it and the suite fails. The eleven are a frozen constant
  beside the census, derived from
  `python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md` over the merged
  plans and recorded with that provenance.

  **R18 is what stops the contract being evaded by writing the claim somewhere else.**
  The region binds the v13 model; nothing stops an author stating a model property in the
  legacy prose above it, where no marker is required. R18 closes that: no line **outside**
  the region may carry a term from a frozen v13 lexicon — `trust store`, `pmcp trust`,
  `approval`, `approve`, `revoke`, `consent`, `.pmcp/manifest.yaml`, `.mcp.json`,
  `.mcp-gateway-policy.yaml`, `package identity`, `packages.denylist`,
  `packages.allowlist`, `npx`, `provision`, `discovered server`,
  `register_discovered_server`, `PMCP_FEEDBACK_TOKEN`, `submit_feedback`, `integrity`,
  `provenance`, `narrowing` — matched case-insensitively at word boundaries. A model
  sentence outside the region is therefore a test failure, and the remedy is to move it in.

  **It ships with a frozen allowlist, because a flat ban is unusable.** Some
  region-external prose legitimately names a term without asserting a v13 property: the
  URL-mode elicitation bullet names `gateway.auth_connect` about OAuth flows, not about
  consent. The allowlist is a constant beside the lexicon, each entry carrying the reason
  it is exempt, so adding to it is a deliberate and reviewable act rather than a silent
  one. **SL-5's sweep is contracted as work, not as a habit**: classify every
  region-external hit as *moved into the region* or *allowlisted with a stated reason*,
  and record the enumeration. The #229 project-`.env` bullet is expected to move, because
  it asserts a credential-provenance property that this roadmap both extends and, through
  SL-0, changes.

  **The residual, stated rather than implied.** A sentence that asserts a v13-model
  property while using **none** of the lexicon terms is not detectable structurally, and
  one such sentence exists today: "Only configure servers you trust", in the
  subprocess-spawning bullet, which now understates the posture and names nothing.
  `test_the_subprocess_spawning_limitation_names_the_v13_gates` pins that one by name. For
  anything added later the lexicon is a strong filter — the consent model cannot be
  described without naming approval or consent — but it is a filter, not a proof, and a
  pre-existing paraphrase can only be found by reading. SL-5's one-time enumeration is
  that reading, and this plan says so rather than claiming the sweep is mechanical.

  **How liveness is reached without a new CI job.** R8 and R9 prove every citation
  resolves to a real, collectable, module-level test **inside this repository's suite**;
  CI already runs `uv run pytest tests/`, so every cited test is executed on every
  change. A claim whose test is deleted or renamed fails R8/R9; a claim whose test starts
  failing fails the suite. `--run` exists for a reviewer who wants the union alone, and
  `## Verification` uses it.

  **What this gate does not promise.** That a passing test still *means* what its claim
  says is a human judgement no parser makes: a test can be weakened in place. R8/R9 bind
  the citation, the suite binds the result, and the review of a diff that edits both a
  claim and its test is where the remaining risk lives. Stated rather than implied.

## Lane Index & Dependencies

SL-0 — The install-spawn credential strip (the phase's one behaviour change)
  Depends on: (none)
  Blocks: SL-3, SL-5
  Parallel-safe: yes

SL-1 — The claim ledger contract and its checker
  Depends on: (none)
  Blocks: SL-5
  Parallel-safe: yes

SL-2 — The four review reproductions, end to end
  Depends on: (none)
  Blocks: SL-5
  Parallel-safe: yes

SL-3 — Composition seams between phases
  Depends on: SL-0
  Blocks: SL-5
  Parallel-safe: yes

SL-4 — Refusal remedies and the fresh-operator baseline
  Depends on: (none)
  Blocks: SL-5
  Parallel-safe: yes

SL-5 — `SECURITY.md` and its binding
  Depends on: SL-0, SL-1, SL-2, SL-3, SL-4
  Blocks: SL-6
  Parallel-safe: no (sole writer of `SECURITY.md`)

SL-6 — Documentation & spec reconciliation (the `SL-docs` lane below; PKGID's and
  EGRESS's plans index their docs lanes the same way, and its task ids are `SL-docs.N`)
  Depends on: SL-5
  Blocks: (none)
  Parallel-safe: no (terminal)

**SL-0 is a deliberate exception to the no-new-behaviour non-goal, taken by the
operator.** This phase's Execution Notes already anticipated it: a criterion outranks a
non-goal, and a live credential leak at an existing boundary is not something a
documentation phase may write down and walk past. It is confined to one lane, two files
and one threaded argument, and `## Verification` names the exact diff rather than
asserting `src/` is untouched. Every other lane remains test- and documentation-only.

**Why this decomposition, and not the roadmap's two.** The roadmap suggests lane A
(SECURITY.md and the CHANGELOG narrative) and lane B (the adversarial suite). Lane B as
one lane is four unrelated bodies of proof in one file — the reproductions, the
composition seams, the remedy audit and the fresh-operator baseline — and they share no
code, only a subject. Splitting them buys real parallelism across four genuinely
independent suites, and it buys something more useful: **each exit criterion has exactly
one owner**, so a lane that runs out of time cannot quietly borrow coverage from another
criterion's file. Lane A is split too, because the claim format has to exist before
`SECURITY.md` is written. A docs lane that invents a format and a checker lane that
parses a different one is the two-registries-drifting defect
`scripts/check_plan_consistency.py` was written for.

**Why SL-1 is not a "preamble" in EGRESS's sense.** EGRESS's SL-1 published an API four
lanes coded against. SL-1 here has exactly one consumer, SL-5, and SL-2/SL-3/SL-4 need
nothing from it. Making it a shared preamble everything waits on would be lane shape
driving the schedule for nothing. `tests/conftest.py` already supplies the isolation a
shared harness would have carried — the autouse HOME redirect, the autouse npm-registry
guard and `fake_npm_registry` — and the repo's precedent (EGRESS SL-1's note, PKGID
amendment 6) is that each suite declares any further fixtures locally rather than writing
CONSENT SL-1's file. What is left to share is a twenty-line gateway builder, which is not
worth a lane and not worth a dependency edge.

**SL-5 is not blocked on the other lanes for drafting, only for verification.** Every
node id SL-5 cites is frozen in this document, in the lane tables below; SL-5 derives its
citations from the plan, not from the tree. It can write `SECURITY.md` in full against
those names the moment SL-1 publishes the format, and R8/R9 will resolve once the files
land. Four waves: `{SL-0, SL-1, SL-2, SL-4}` → `SL-3` → `SL-5` → `SL-docs`; SL-3 waits
only on SL-0, because two of its eleven tests assert the strip's post-fix behaviour.

## Lanes

### SL-0 — The install-spawn credential strip

- **Scope**: The one behaviour this phase changes: an install spawn must be stripped of the credentials PMCP's stores hold for the project root **the gateway was given**, not for whatever directory it happens to be running in.
- **Owned files**: `src/pmcp/manifest/installer.py`, `src/pmcp/tools/handlers.py`, `tests/test_install_child_env_project_root.py`
- **Interfaces provided**: `build_install_child_env(server_config, project_root: Path | None = None)`, with the same optional parameter threaded through `JobManager.start_install`, `install_server` and `verify_installation`
- **Interfaces consumed**: `sanitized_subprocess_env` and `managed_secret_keys` (`src/pmcp/env_store.py`, merged)
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-0.1 | test | — | `tests/test_install_child_env_project_root.py` | **exactly these names**: `test_an_install_spawn_does_not_inherit_a_project_scoped_credential_when_the_cwd_differs`, `test_the_production_install_path_passes_the_gateways_project_root`, `test_a_project_scoped_credential_is_stripped_when_the_cwd_is_the_project_root`, `test_the_helper_falls_back_to_the_working_directory_walk_when_given_no_root`, `test_no_production_call_site_omits_the_project_root` | `uv run pytest -q tests/test_install_child_env_project_root.py` |
| SL-0.2 | impl | SL-0.1 | `src/pmcp/manifest/installer.py`, `src/pmcp/tools/handlers.py` | — | — |
| SL-0.3 | verify | SL-0.2 | `src/pmcp/manifest/installer.py`, `src/pmcp/tools/handlers.py` | all SL-0 tests | `uv run pytest -q tests/test_install_child_env_project_root.py tests/test_credential_child_env.py tests/test_manifest.py tests/test_install_argv_logging.py tests/test_manifest_provision.py tests/test_pkgid_panel_fixes.py tests/test_package_identity_gate.py tests/test_phase4_e2e.py tests/test_tools.py && uv run mypy src/` |

**The defect, measured rather than asserted.** `build_install_child_env`
(`src/pmcp/manifest/installer.py:589`) ends in `sanitized_subprocess_env(own_env)`
(`:618`) with no `project` argument, so `managed_secret_keys(None)` resolves the project
store by walking up from `Path.cwd()` (`src/pmcp/env_store.py:286-300`,
`resolve_project_root` at `:26-35`). **It is the only such caller**:
`src/pmcp/client/manager.py:2364`, `src/pmcp/tools/handlers.py:5440` and `:5548` all pass
`self._project_root`. `_write_secret` writes a project-scope credential through
`self._project_root` (`handlers.py:3137`), and `pmcp serve --project X` sets that root
explicitly (`src/pmcp/cli.py:2378`). Run from a different working directory, the write and
the strip therefore look in two different places, and the install spawn inherits a
credential written for another server. This is EGRESS's
`test_the_gate_is_asked_about_the_project_root_the_secret_writer_uses` finding at a second
site, and the reason it is fixed here rather than filed is that it is a live credential
leak at an existing boundary, which EC-SEAL-5 reaches.

**The change is genuinely narrow, and the call chain was walked to confirm it.**
`build_install_child_env` has three call sites, all in `installer.py`: `:216` inside
`JobManager.start_install` (`:167`), `:691` inside `install_server` (`:649`) and `:742`
inside `verify_installation` (`:719`). Of those three enclosing functions **only
`start_install` has a production caller** — `handlers.py:4675` — which is consistent with
PKGID amendment 5's record that `verify_installation` has none. So the live path is one
argument threaded through one function. All three are given the parameter anyway, so no
call site of the helper is left defaulting. Two source files, roughly six lines.

**Why the parameter is optional rather than required, which is a real trade-off and not
laziness.** The house rule PKGID and EGRESS both state is that a parameter a caller may
omit acquires the caller's default, and here the default *is* the defect, so keyword-only
and required would be the stricter design. Measured before choosing: **55 call sites
across eight test files** would then have to change — `tests/test_manifest.py` (28),
`tests/test_install_argv_logging.py` (12), `tests/test_tools.py` (2),
`tests/test_manifest_provision.py` (2), `tests/test_pkgid_panel_fixes.py` (2),
`tests/test_phase4_e2e.py` (1), `tests/test_package_identity_gate.py` (1) and
`tests/test_credential_child_env.py` (1) — three of which are merged-phase **evidence
files** this phase's closeout collects. A documentation-and-proof phase rewriting 55 call
sites inside another phase's evidence is disproportionate, and it is exactly the blast
radius CONSENT amendment 2 says to measure rather than discover. The hazard is closed
structurally instead: `test_no_production_call_site_omits_the_project_root` reads `src/`
and asserts that every production call of `build_install_child_env` and of
`sanitized_subprocess_env` supplies a project argument. **That structural check is a node
id an acceptance criterion runs**, not a `## Verification` line alone — EGRESS amendment
5's lesson is that a check nobody has run is a check nobody has tested, and a check that
lives only in a plan's Verification block is one nobody is required to run.

**The tests inspect the real child environment, not the helper in isolation.**
`test_an_install_spawn_does_not_inherit_a_project_scoped_credential_when_the_cwd_differs`
builds a gateway with an explicit `project_root`, `monkeypatch.chdir`s somewhere else,
writes a project-scope credential through `env_store.set_env_value` into the gateway's
root, drives the install path with the spawn recorded, and asserts the recorded child
environment does not carry that key.
`test_the_production_install_path_passes_the_gateways_project_root` drives
`gateway.provision` and asserts `start_install` received `self._project_root` — the half a
helper test cannot see, and the half that was wrong.
`test_a_project_scoped_credential_is_stripped_when_the_cwd_is_the_project_root` pins the
case that already worked, so the fix cannot trade one direction for the other, and
`test_the_helper_falls_back_to_the_working_directory_walk_when_given_no_root` pins what
the optional default means, so the structural check above has a behavioural partner.

**No CHANGELOG entry is written by this lane.** SL-docs owns `CHANGELOG.md` and records
this change there, because "no behaviour change without a CHANGELOG entry" is a
cross-cutting principle and this is the only behaviour change in the phase.

### SL-1 — The claim ledger contract and its checker

- **Scope**: The parser that makes EC-SEAL-1 mechanically checkable, and one fixture per failure rule so the checker itself is falsified rather than trusted.
- **Owned files**: `scripts/check_security_claims.py`, `tests/test_security_claims_parser.py`
- **Interfaces provided**: `scripts/check_security_claims.py` with modes `check`, `--node-ids`, `--run`; failure names R1-R19 as frozen in IF-0-SEAL-1; the frozen v13 evidence census, unproven-test list, v13 lexicon and region-external allowlist
- **Interfaces consumed**: (none in this phase)
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/test_security_claims_parser.py` | **exactly these names** — one per frozen rule R1-R19, one for the accepted characterization form, the positive case, and the two modes: `test_a_wellformed_ledger_passes_every_rule`, `test_a_missing_claim_region_is_reported`, `test_a_missing_or_duplicated_ledger_is_reported`, `test_a_kind_outside_the_two_value_vocabulary_is_reported`, `test_a_guarantee_without_a_proof_is_reported`, `test_a_proof_entry_that_is_not_a_node_id_is_reported`, `test_a_limitation_carrying_an_uncharacterized_proof_is_reported`, `test_a_limitation_may_cite_a_characterization_and_nothing_else`, `test_a_guarantee_may_not_cite_a_characterization`, `test_a_limitation_block_must_be_labelled_as_one`, `test_a_node_id_naming_a_missing_file_is_reported`, `test_a_node_id_naming_a_missing_function_is_reported`, `test_a_node_id_naming_a_method_inside_a_class_is_reported`, `test_an_uncollectable_node_id_is_reported`, `test_a_claim_marker_with_no_ledger_row_is_reported`, `test_a_ledger_row_that_no_block_cites_is_reported`, `test_a_sentence_without_a_claim_marker_is_reported`, `test_a_second_sentence_in_a_block_must_carry_its_own_marker`, `test_a_line_that_is_neither_heading_nor_claim_block_is_reported`, `test_an_abbreviation_that_breaks_the_sentence_split_is_reported`, `test_duplicate_and_non_sequential_claim_ids_are_reported`, `test_every_v13_evidence_file_must_be_cited`, `test_a_previously_unproven_test_must_be_cited`, `test_a_v13_model_term_outside_the_region_is_reported`, `test_the_node_ids_subcommand_prints_the_cited_union`, `test_the_run_subcommand_fails_when_a_cited_test_fails` | `uv run pytest -q tests/test_security_claims_parser.py` |
| SL-1.2 | impl | SL-1.1 | `scripts/check_security_claims.py` | — | — |
| SL-1.3 | verify | SL-1.2 | `scripts/check_security_claims.py` | all SL-1 tests | `uv run pytest -q tests/test_security_claims_parser.py && uv run ruff check scripts/ && uv run ruff format --check scripts/` |

**Rule-to-test map, written down because the first revision of this plan got it wrong.**
The plan panel's blocking finding was that IF-0-SEAL-1 froze seventeen rules while SL-1's
task table listed twenty tests covering only fourteen of them: **R2 `ledger_missing`, R3
`bad_kind` and R7 `malformed_node_id` had no test at all**, and R3 is the rule the freeze
itself names as the smuggling route for an unproven claim. A lane briefed from the table
would have TDD'd a checker that never rejects a third Kind, and EC-SEAL-1 would have
passed while `SECURITY.md` carried unproven claims. That is exactly the two-registries
drift `scripts/check_plan_consistency.py` exists for, occurring inside the plan written to
prevent it — the checker compares lane tables against acceptance criteria and cannot see a
freeze. **This table is the third registry, and a lane must reconcile all three.**

| Rule | Test that falsifies it |
|---|---|
| — (positive) | `test_a_wellformed_ledger_passes_every_rule` |
| R1 | `test_a_missing_claim_region_is_reported` |
| R2 | `test_a_missing_or_duplicated_ledger_is_reported` |
| R3 | `test_a_kind_outside_the_two_value_vocabulary_is_reported` |
| R4 | `test_duplicate_and_non_sequential_claim_ids_are_reported` |
| R5 | `test_a_guarantee_without_a_proof_is_reported` |
| R6 | `test_a_limitation_carrying_an_uncharacterized_proof_is_reported` (rejected form) and `test_a_limitation_may_cite_a_characterization_and_nothing_else` (accepted form) |
| R7 | `test_a_proof_entry_that_is_not_a_node_id_is_reported` |
| R8 | `test_a_node_id_naming_a_missing_file_is_reported`, `test_a_node_id_naming_a_missing_function_is_reported`, `test_a_node_id_naming_a_method_inside_a_class_is_reported` |
| R9 | `test_an_uncollectable_node_id_is_reported` |
| R10 | `test_a_ledger_row_that_no_block_cites_is_reported` |
| R11 | `test_a_claim_marker_with_no_ledger_row_is_reported` |
| R12 | `test_a_sentence_without_a_claim_marker_is_reported`, `test_a_second_sentence_in_a_block_must_carry_its_own_marker` |
| R13 | `test_a_line_that_is_neither_heading_nor_claim_block_is_reported` |
| R14 | `test_an_abbreviation_that_breaks_the_sentence_split_is_reported` |
| R15 | `test_a_limitation_block_must_be_labelled_as_one` |
| R16 | `test_every_v13_evidence_file_must_be_cited` |
| R17 | `test_a_previously_unproven_test_must_be_cited` |
| R18 | `test_a_v13_model_term_outside_the_region_is_reported` |
| R19 | `test_a_guarantee_may_not_cite_a_characterization` |
| `--node-ids` | `test_the_node_ids_subcommand_prints_the_cited_union` |
| `--run` | `test_the_run_subcommand_fails_when_a_cited_test_fails` |

Twenty-six tests, nineteen rules, every rule with at least one owner and no test without a
rule. **A lane that adds a rule must add its row here, its test to the task table, and its
node id to EC-SEAL-1 in the same change**; a rule with no row is the defect above.

**Every fixture is a synthetic `SECURITY.md` in `tmp_path`, never the real one.** The
real file is SL-5's subject; a parser test that reads it would go red for SL-5's reasons
and green for the parser's, which is the failure `test_a_wellformed_ledger_passes_every_rule`
exists to keep separate. `test_the_run_subcommand_fails_when_a_cited_test_fails` writes a
one-line failing test into `tmp_path` alongside its fixture and runs the script there
with `-p no:cacheprovider`, so the nested pytest cannot touch this repository's cache.

**The census is a constant in the script, with the commits that produced it in a
comment.** Nineteen paths, from the Context table. It is the one piece of the checker
that is not derivable from `SECURITY.md`, and R16 is the rule that makes "describes the
implemented model" mean something: a ledger citing three of the nineteen files documents
a third of the model and would otherwise pass every other rule.

**`--node-ids` prints only what a `guarantee` row cites**, sorted and deduplicated, one
per line, so `## Verification`'s union run and `--run` operate on the same set. A
`limitation` contributes nothing by construction (R6).

### SL-2 — The four review reproductions, end to end

- **Scope**: S-01, S-03, S-11 and S-04, each driven through the production handler at every door the merged phases found, each asserted to fail closed.
- **Owned files**: `tests/test_trust_boundaries_e2e.py`
- **Interfaces provided**: (none; a test file)
- **Interfaces consumed**: `evaluate_provision` (`src/pmcp/provision_gate.py`, PKGID SL-1, merged) through its handler call sites; `read_and_gate` (`src/pmcp/project_consent.py`, CONSENT SL-1, merged) through its three loaders; `evaluate_feedback_egress` (`src/pmcp/feedback_egress.py`, EGRESS SL-2, merged) through `gateway.submit_feedback`; `fake_npm_registry` and the autouse guards in `tests/conftest.py`
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/test_trust_boundaries_e2e.py` | **exactly these names**: `test_s01_provision_refuses_an_arbitrary_package_under_an_allowlisted_name`, `test_s01_connect_server_refuses_the_same_registration`, `test_s01_restart_server_refuses_the_same_registration`, `test_s01_update_server_refuses_a_discovered_server_before_any_probe`, `test_s01_the_refusal_names_the_package_and_the_approve_command`, `test_s01_a_package_manager_variable_is_refused_at_registration`, `test_s01_auth_connect_refuses_a_package_manager_variable_for_a_discovered_server`, `test_s03_an_unapproved_overlay_does_not_replace_a_shipped_command`, `test_s03_an_unapproved_project_mcp_json_is_not_applied`, `test_s03_an_approved_source_edited_afterwards_is_refused_again`, `test_s03_the_parsed_bytes_are_the_gated_bytes_at_every_loader`, `test_s11_an_unapproved_project_policy_is_not_read_at_all`, `test_s11_an_approved_project_policy_cannot_widen_the_user_policy`, `test_s11_an_approved_project_policy_cannot_drop_the_default_redaction_patterns`, `test_s04_an_ambient_github_token_submits_nothing_and_spawns_no_gh`, `test_s04_a_checkout_supplied_token_or_repository_is_refused`, `test_s04_no_egress_door_remains_in_the_handler`, `test_no_reproduction_in_this_suite_reached_the_network` | `uv run pytest -q tests/test_trust_boundaries_e2e.py` |
| SL-2.2 | verify | SL-2.1 | `tests/test_trust_boundaries_e2e.py` | all SL-2 tests | `uv run pytest -q tests/test_trust_boundaries_e2e.py && uv run pytest -q tests/test_package_identity_gate.py tests/test_project_consent_gate.py tests/test_feedback_egress.py` |

**Handler-level, and the plan says why rather than leaving it to judgement.** PKGID
amendment 1: `provision` was gated and three lifecycle tools were not, because `npx -y`
fetches and runs when it *spawns*. EGRESS amendment 1: the criteria named three egress
doors and there were four. A test that calls `evaluate_provision` directly cannot see
either defect. Every node id above goes through `gateway.register_discovered_server`,
`gateway.provision`, `gateway.connect_server`, `gateway.restart_server`,
`gateway.update_server`, `gateway.auth_connect` or `gateway.submit_feedback`, or through
a loader reading a real file on disk.

**The four S-01 doors are four tests on purpose.** One parametrised test over the tools
would report one failure for a regression at any door and would let a reviewer read
"S-01 passes" while three doors were untested; four ids make the acceptance command name
each one.

**S-03's one-read rule is required here, which is CONSENT amendment 3's explicit
request.** Its criteria were structurally blind to a gate-then-reread regression: in
every required test the bytes on disk and the bytes that were gated were identical, so a
caller that re-opens the file observes nothing different, and the test that proves the
property was in no acceptance criterion at all.
`test_s03_the_parsed_bytes_are_the_gated_bytes_at_every_loader` closes that for all three
loaders at once, by making the file's bytes change between the gate and the parse and
asserting the gated bytes are what was applied.

**This file declares its own GitHub guard.** `tests/conftest.py` covers the npm registry
autouse; nothing covers `feedback_egress`'s opener. Model it on `_no_live_npm_registry`
(`tests/conftest.py:168`) and on EGRESS's own `_no_live_github`: replace the opener with
a recorder that raises, collect attempts, and assert the list is empty at teardown.
`test_no_reproduction_in_this_suite_reached_the_network` makes that assertion a node id
an acceptance command runs, rather than a fixture side effect nobody is required to
prove. EGRESS's red run reached a live `gh issue create --repo ViperJuice/pmcp` on the
development host; this suite drives the same handler.

**Mutation proof is contracted, not optional.** Before SL-2 reports done, each of the
four findings must be shown red under one named mutation of the merged gate: S-01 by
making `evaluate_provision` return `allowed=True` for `source="discovered"`; S-03 and
S-11 by making `read_and_gate` return the file's bytes with `allowed=True`; S-04 by
restoring `or os.environ.get("GITHUB_TOKEN")` to the token lookup. Mutations are run in
a lane-unique scratch directory and the working tree is asserted equal to `HEAD` before
and after — PKGID amendment 7, verbatim.

### SL-3 — Composition seams between phases

- **Scope**: EC-SEAL-5. The cases that pass every per-phase criterion and fail at the seam between two of them, plus the strip behaviour finding 1 above uncovered.
- **Owned files**: `tests/test_trust_boundaries_composition.py`
- **Interfaces provided**: (none; a test file)
- **Interfaces consumed**: `build_install_child_env`'s threaded project root (SL-0); `read_and_gate` and the overlay walk-up (`src/pmcp/manifest/loader.py`, CONSENT SL-2, merged); `evaluate_provision` and `_manifest_package_names` (`src/pmcp/provision_gate.py`, PKGID SL-1, merged); `trust_store_path`'s residency check (`src/pmcp/trust_store.py`, TRUST SL-1, merged)
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-3.1 | test | SL-0 | `tests/test_trust_boundaries_composition.py` | **exactly these names**: `test_an_unapproved_overlay_that_adds_a_server_is_not_applied`, `test_an_added_server_is_not_manifest_backed_at_provision`, `test_an_added_server_is_not_manifest_backed_at_connect`, `test_a_trust_store_shipped_inside_the_checkout_is_refused`, `test_a_checkout_resident_store_is_refused_through_a_symlink`, `test_an_approval_in_a_checkout_resident_store_grants_nothing`, `test_a_denylisted_package_outranks_an_operator_approval`, `test_an_approved_pin_still_refuses_an_unpinned_argv`, `test_an_install_spawn_strips_the_credentials_pmcp_stores_hold`, `test_the_install_spawn_strip_uses_the_gateways_project_root_not_the_working_directory`, `test_no_composition_case_in_this_suite_reached_the_network` | `uv run pytest -q tests/test_trust_boundaries_composition.py` |
| SL-3.2 | verify | SL-3.1 | `tests/test_trust_boundaries_composition.py` | all SL-3 tests | `uv run pytest -q tests/test_trust_boundaries_composition.py && uv run pytest -q tests/test_trust_store.py tests/test_project_source_consent_manifest.py tests/test_package_approvals.py` |

**The two seams the roadmap names, and why a per-phase suite cannot see them.**
EC-CONSENT-6's add path slips between two phases that each look complete: CONSENT refuses
*replacement*, PKGID's default-deny exempts *manifest-backed* servers, and an unapproved
overlay that adds a new server would execute through the gap. Proving it needs an
unapproved overlay **and** a provisioning attempt in one test — neither phase's file has
both. EC-TRUST-5 is the same shape from the other side: TRUST refuses a checkout-resident
store, CONSENT keys approval on content, and the composition is a repository that ships
its own approval record and expects it to be believed. Three tests drive the store
residency check directly, through a symlinked path, and through an approval recorded in
such a store, because `trust_store_path` raises (`src/pmcp/trust_store.py:96-100`) and a
caller that swallows the raise would read the refusal as "no record".

**Two more seams, from PKGID's own decision order.**
`test_a_denylisted_package_outranks_an_operator_approval` composes the policy lane with
the approvals lane: rule 1 must outrank rule 2, or a denylist entry silently never fires
for exactly the packages an operator trusted.
`test_an_approved_pin_still_refuses_an_unpinned_argv` is PKGID amendment 3's finding —
the unpinned-argv deny must stay ahead of every allow, or an approved server whose argv
is a bare `npx -y pkg` re-resolves `latest` at spawn.

**The strip pair, rewritten after the operator moved the fix into this phase.** Both now
assert SL-0's post-fix behaviour rather than characterising a defect:
`test_an_install_spawn_strips_the_credentials_pmcp_stores_hold` asserts the general
property, and `test_the_install_spawn_strip_uses_the_gateways_project_root_not_the_working_directory`
asserts the *mechanism* — that the strip resolves the project store from the root the
gateway was given rather than from the working directory, which is the half that was
wrong. They stay in this file, and stay two tests, because a `SECURITY.md` guarantee is
stated at the width of the mechanism: with SL-0 landed that width is the gateway's project
root, and there is no limitation left to write for it. SL-3 makes no source change of its
own — `src/pmcp/manifest/installer.py` is SL-0's file, and `## Verification` fails the
phase if any other `src/` path appears in the diff.

### SL-4 — Refusal remedies and the fresh-operator baseline

- **Scope**: EC-SEAL-3 and EC-SEAL-4. An exhaustive audit of every gate's refusal vocabulary, and the proof that an operator who has none of this sees none of it.
- **Owned files**: `tests/test_refusal_remedies.py`, `tests/test_fresh_operator_baseline.py`
- **Interfaces provided**: (none; two test files)
- **Interfaces consumed**: `ConsentReason` (`src/pmcp/project_consent.py:60`, CONSENT SL-1, merged); `PROVISION_REASONS` (`src/pmcp/provision_gate.py:63-73`, PKGID SL-1, merged); `FeedbackEgressReason` (`src/pmcp/feedback_egress.py:120-130`, EGRESS SL-2, merged); the CLI's argparse tree (`src/pmcp/cli.py`, TRUST SL-1 and PKGID SL-1, merged)
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-4.1 | test | — | `tests/test_refusal_remedies.py` | **exactly these names**: `test_every_consent_refusal_reason_is_driven_by_this_audit`, `test_every_provision_refusal_reason_is_driven_by_this_audit`, `test_every_feedback_refusal_reason_is_driven_by_this_audit`, `test_every_refusal_carries_a_non_empty_remedy`, `test_every_pmcp_command_in_a_remedy_names_a_verb_the_cli_dispatches`, `test_every_remedy_is_runnable_as_printed`, `test_a_path_containing_a_space_yields_a_runnable_approve_command`, `test_a_path_carrying_shell_metacharacters_is_rendered_inert`, `test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy`, `test_a_provision_refusal_reaches_the_agent_facing_message_with_its_remedy`, `test_a_feedback_refusal_reaches_the_tool_output_with_its_remedy`, `test_a_trust_cli_refusal_names_the_command_that_would_grant_it`, `test_the_remedies_that_name_no_pmcp_verb_are_exactly_the_enumerated_set`, `test_no_remedy_audit_case_reached_the_network` | `uv run pytest -q tests/test_refusal_remedies.py` |
| SL-4.2 | test | — | `tests/test_fresh_operator_baseline.py` | **exactly these names**: `test_a_fresh_home_holds_no_trust_store_and_no_package_approvals`, `test_a_manifest_backed_server_provisions_with_no_trust_store`, `test_a_manifest_backed_server_connects_with_no_trust_store`, `test_a_manifest_backed_server_restarts_with_no_trust_store`, `test_no_project_file_means_no_consent_gate_is_consulted`, `test_a_fresh_operator_sees_no_new_startup_warning`, `test_every_shipped_manifest_entry_is_determinable_without_a_denylist` | `uv run pytest -q tests/test_fresh_operator_baseline.py` |
| SL-4.3 | verify | SL-4.1, SL-4.2 | `tests/test_refusal_remedies.py`, `tests/test_fresh_operator_baseline.py` | all SL-4 tests | `uv run pytest -q tests/test_refusal_remedies.py tests/test_fresh_operator_baseline.py && uv run pytest -q tests/test_trust_cli.py tests/test_cli.py` |

**Exhaustiveness is the point, and it is what makes the audit outlive this phase.** The
first three node ids each read a **closed vocabulary out of the source** —
`ConsentReason`'s `Literal` members, `PROVISION_REASONS`, `FeedbackEgressReason`'s
`Literal` members — and assert that the audit's own case table covers every member with
no member left over. A reason added later fails the audit on the day it is added, which
is the only version of "every refusal path" that survives a future phase.

**How EC-SEAL-3's literal wording is handled, without weakening the criterion.** Finding
3 in Context enumerates the five remedies that name no `pmcp` verb, because none exists
for them. `test_every_pmcp_command_in_a_remedy_names_a_verb_the_cli_dispatches` takes
every `pmcp …` substring any remedy contains and resolves it against the real argparse
tree, so a remedy naming a verb that does not exist is a failure — EGRESS's plan records
that `pmcp secrets rm` was nearly named for exactly this reason.
`test_the_remedies_that_name_no_pmcp_verb_are_exactly_the_enumerated_set` pins the
enumerated exceptions by reason id, so the set can shrink (a later phase adds a verb) or
be argued about, but cannot grow silently. That is stronger than the criterion's literal
reading, which a single unverifiable string would satisfy.

**The quoting rule, stated once for all four gates.** Every operator-facing line that
interpolates a repository- or agent-controlled value passes it through the two-step
render: escape every non-printable character, then `shlex.quote` **unconditionally**.
CONSENT's `_operator_safe` (`src/pmcp/project_consent.py:118`) and PKGID's exported
`operator_safe` (`src/pmcp/provision_gate.py:99`) both implement it and EGRESS imports
the latter, so the rule is already uniform; `test_a_path_containing_a_space_yields_a_runnable_approve_command`
and `test_a_path_carrying_shell_metacharacters_is_rendered_inert` are what make it a
required property rather than three independent coincidences.
`test_every_remedy_is_runnable_as_printed` is the general form: every remedy that
contains a command must survive `shlex.split` and round-trip the value it names.

**The three "reaches the operator" tests are the difference between a predicate and a
refusal.** A gate can return a perfect remedy that no handler prints. Each of the three
drives the real surface — one WARNING record for the consent gate (exactly one, naming
the remediation), the agent-facing `message` for a lifecycle refusal
(`src/pmcp/tools/handlers.py:249`), and `SubmitFeedbackOutput.message` for the egress
gate — and asserts the remedy string is present in it.

**EC-SEAL-4 is a baseline, so it must be measured against a genuinely empty home.**
`tests/conftest.py:62` gives every test a fresh `HOME` and **approves nothing**, which is
exactly the fresh operator. `test_a_fresh_home_holds_no_trust_store_and_no_package_approvals`
asserts the premise before the other six rely on it — an autouse fixture that silently
stopped redirecting would otherwise make all six vacuous.
`test_every_shipped_manifest_entry_is_determinable_without_a_denylist` is PKGID amendment
9's G1 property restated as a fresh-operator one: with no `packages.denylist` in force,
nothing an operator ships can be refused as undetermined.

### SL-5 — `SECURITY.md` and its binding

- **Scope**: The trust model, written once, every sentence bound to a test or labelled a limitation; plus the legacy sweep for prose v13 has falsified.
- **Owned files**: `SECURITY.md`, `tests/test_security_claims.py`
- **Interfaces provided**: the claim region and ledger, as the frozen evidence set for the spec closeout
- **Interfaces consumed**: `scripts/check_security_claims.py` (SL-1); the frozen node ids of SL-2, SL-3 and SL-4
- **Parallel-safe**: no (sole writer of `SECURITY.md`)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-5.1 | test | SL-1 | `tests/test_security_claims.py` | **exactly these names**: `test_the_shipped_security_md_passes_the_claim_checker`, `test_every_cited_node_id_exists_and_collects`, `test_every_v13_evidence_file_is_cited_by_a_guarantee`, `test_every_limitation_is_labelled_and_cites_only_characterizations`, `test_no_v13_model_term_appears_outside_the_claim_region`, `test_no_guarantee_cites_a_test_outside_the_repository_suite`, `test_the_security_policy_names_the_packaged_repository`, `test_the_subprocess_spawning_limitation_names_the_v13_gates` | `uv run pytest -q tests/test_security_claims.py` |
| SL-5.2 | impl | SL-5.1, SL-2, SL-3, SL-4 | `SECURITY.md` | — | — |
| SL-5.3 | verify | SL-5.2 | `SECURITY.md`, `tests/test_security_claims.py` | all SL-5 tests | `uv run pytest -q tests/test_security_claims.py && uv run python scripts/check_security_claims.py --run` |

**The region's content, sectioned so a reader can find the model rather than the
criterion.** Four subsections under one `### The v13 trust model` heading: *Identity, not
labels* (package identity, the pin, what the pin does and does not bind); *Consent for
repository-supplied configuration* (the three project sources, content-keyed approval,
narrowing-only policy, the store's residency); *Outbound actions* (the deleted `gh` door,
the dedicated token, preview-first, the provenance registry); and *Limitations*, which
holds every `limitation` block. A limitation may appear in an earlier subsection when it
belongs beside its guarantee — R15's label is what makes it legible there.

**The limitations this phase is required to write, from the deferral table.** The
reserved `"denied"` value with no writer; `set_startup_policy` invalidating an approval
it does not mention; `approve-package` recording no integrity, so the pin binds
`name@version` and not the bytes; `auth_connect` admitting a credential-shaped override
for a name in neither table, and what the strip does and does not remove; the abandoned
worker that can occupy a thread-limiter token without acting; and the out-of-band
credential store no provenance source can see. The install spawn's project-root
resolution is **no longer** on this list: SL-0 fixes it, so it becomes a guarantee rather
than a limitation.
**None of these is optional.** EC-SEAL-1 is "no claim the tests do not prove", and a
model written without them would claim, by omission, more than the tests prove.

**Eleven citations are mandatory, and the checker refuses without them (R17).** They are
the lane-owned tests that no merged phase's acceptance criteria run: from CONSENT,
`test_read_and_gate_opens_the_path_exactly_once`, `test_an_unrecorded_path_is_refused`,
`test_a_store_error_is_refused_not_raised`,
`test_an_unreadable_source_is_refused_not_raised`,
`test_read_and_gate_returns_none_bytes_when_refused`,
`test_remediation_is_the_absolute_path_trust_approve_command`,
`test_log_refusal_emits_one_warning_naming_the_remediation`,
`test_approved_overlay_is_applied`, `test_approved_project_mcp_json_is_applied` and
`test_project_redaction_patterns_extend_rather_than_replace_defaults`; from TRUST,
`test_a_denied_record_is_not_approved`. Each is a guarantee about the model — the
one-read defence, default deny, the two fail-closed cases, a refusal handing back no
bytes, the remediation being actionable and logged exactly once, the gate not being
"deny everything", and the redaction-widening rule. Citing them is what makes them
required, which is the closure CONSENT amendment 1 asked a future phase for and could
not perform itself.

**The legacy sweep is contracted work with a test behind it, not a habit.** R18 makes it
mechanical for anything the frozen v13 lexicon can see: no line outside the region may
carry a lexicon term unless it is on the checker's frozen allowlist, and
`test_no_v13_model_term_appears_outside_the_claim_region` fails while one does. SL-5 must
**classify every region-external hit** as either moved into the region or allowlisted with
a stated reason, and record that enumeration in its completion report — the allowlist is a
reviewed constant, not a silence. The #229 project-`.env` bullet (`SECURITY.md:136-147`)
is expected to move rather than be allowlisted: it asserts a credential-provenance
property this roadmap extends and SL-0 changes. **The residual is stated in IF-0-SEAL-1
and repeated here because it is SL-5's to carry**: a sentence asserting a v13-model
property using none of the lexicon terms is not detectable structurally, one such sentence
exists today, and finding any other is a matter of reading the file once rather than of
running a check.

**Two further legacy tests.** Outside the region,
`test_the_security_policy_names_the_packaged_repository` asserts no `ViperJuice`
occurrence survives in the file and that the advisory URL's owner matches the packaged
distribution metadata — the drift check, not a literal, following EC-EGRESS-3's
precedent. `test_the_subprocess_spawning_limitation_names_the_v13_gates` pins the one
legacy bullet v13 has materially changed: "A malicious MCP server config entry could
cause PMCP to spawn arbitrary executables. Only configure servers you trust." is now an
understatement of the posture, because a project config entry is gated and a discovered
package is bound to an approved identity. Rewriting it without a claim marker would leave
the file asserting something weaker than the tests prove — which EC-SEAL-1 forbids in
both directions.

**`test_no_guarantee_cites_a_test_outside_the_repository_suite`** exists because the
liveness argument depends on it: every cited node id is executed by CI only if it lives
under `tests/`. A citation into a script, a doctest, or a path outside the suite would
resolve for R8/R9 and never run.

### SL-docs — Documentation & spec reconciliation

- **Scope**: The CHANGELOG narrative, the docs catalog, the README pointer, the roadmap amendment and re-pin, and the two issues this phase files.
- **Owned files**: `CHANGELOG.md`, `.claude/docs-catalog.json`, `README.md`, `specs/phase-plans-v13.md`, and the `roadmap_sha256` line of all five `plans/phase-plan-v13-*.md`
- **Interfaces provided**: (none)
- **Interfaces consumed**: the shipped `SECURITY.md` (SL-5)
- **Parallel-safe**: no (terminal)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-docs.1 | impl | SL-5 | `CHANGELOG.md`, `README.md`, `.claude/docs-catalog.json` | — | — |
| SL-docs.2 | impl | SL-docs.1 | `specs/phase-plans-v13.md` | — | — |
| SL-docs.3 | verify | SL-docs.2 | `plans/phase-plan-v13-*.md` | — | `python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md` |

**SL-docs.2 is the `canonical_spec_update` half, and it is the one edit no other phase
could make.** It writes the SEAL post-execution amendment block, corrects the roadmap's
`## Verification` to run the real evidence set rather than the four files it names now,
and records the two undercounts this plan found (TRUST's three files, EGRESS's fifth).
See `## Spec Closeout Plan` for what that obliges.

**SL-docs.3 exists because SL-docs.2 changes the roadmap's bytes**, which makes the
`roadmap_sha256` pin in **all five** v13 plans stale — every one currently pins
`7f83620…`. `check_plan_consistency.py` reports each as `[BLOCKING]`. Re-pin all five in
the same commit as the roadmap edit, and run the checker over the glob, not over this
plan alone. No workflow under `.github/` invokes the checker, so this is the only place
the staleness is caught.

**One issue to file**, naming its reproduction: `set_startup_policy` invalidating a trust
record without saying so (deferral 3). Reference Consiliency/pmcp#230 as the origin. The
`build_install_child_env` mismatch was the second; it is **fixed by SL-0** rather than
filed, by operator decision, so SL-docs records it in `CHANGELOG.md` instead — the only
behaviour change in the phase, and the cross-cutting principle is that no behaviour
changes without an entry. The entry must say what an operator running
`pmcp serve --project X` from another directory was exposed to, and what now happens.

## Execution Notes

- **Parallelism.** Four waves: `{SL-0, SL-1, SL-2, SL-4}` → `SL-3` → `SL-5` → `SL-docs`.
  The four wave-1 lanes share no file and need nothing from each other; SL-3 waits on
  SL-0 alone, because two of its tests assert the strip's post-fix behaviour. SL-5 may
  draft `SECURITY.md` as soon as SL-1's format lands, because every node id it cites is
  frozen in this document — but it may not report done until every other lane has merged,
  since R8 and R9 resolve against the tree.
- **Single-writer regions.** `SECURITY.md`, owner SL-5, sole writer.
  `scripts/check_security_claims.py`, owner SL-1. `src/pmcp/manifest/installer.py` and
  `src/pmcp/tools/handlers.py`, owner SL-0, **the only lane that writes `src/` at all**.
  Each test file has exactly one owning lane. `## Verification` asserts the `src/` diff is
  **exactly those two files** rather than empty — the phase's no-new-behaviour non-goal
  made checkable, with its one authorised exception named rather than blanket-permitted.
  No other v13 phase writes either file while SEAL runs, since all four are merged.
- **The contracted exception to "no new gates", and the one already taken.** SL-0 is that
  exception, authorised by the operator: the strip defect is a live credential leak at an
  existing boundary, EC-SEAL-5 is the criterion that reaches it, and the rule below is
  what anticipated it. The rule still stands for everything else. If any SL-2 or SL-3
  reproduction **does not** fail closed, the lane stops and reports rather than adjusting
  the test until it passes. EC-SEAL-2 and EC-SEAL-5 require the reproductions to fail closed, and a
  criterion outranks a non-goal: the fix is then in charter, is scoped to the gate that
  let it through, ships with its own CHANGELOG entry, and is escalated before it is
  written. A test weakened to match a hole is the failure mode this phase exists to
  prevent.
- **Cross-phase collision: SL-docs is not exclusively ours.** `CHANGELOG.md`,
  `.claude/docs-catalog.json`, `README.md` and `specs/phase-plans-v13.md` are the same
  four files CONSENT's, PKGID's and EGRESS's docs lanes own. SEAL is the last phase, so
  no live collision exists — but this is the phase that **edits** the roadmap rather than
  appending to it, so any other v13 work opened alongside SL-docs will conflict and must
  be serialised against it.
- **Lane-unique scratchpads (PKGID amendment 7, verbatim requirement).** Every subagent in
  a session shares one scratchpad directory, and two lanes' scripts have already collided
  there once, briefly capturing a live mutant in a commit. **Lanes must use lane-unique
  scratch subdirectories, and must assert the working tree matches `HEAD` before and after
  any mutation run.** SL-2's contracted mutation proofs make this binding for this phase
  specifically, not advisory.
- **Stale-base guidance** (copy verbatim): lane teammates in isolated worktrees do not see
  sibling merges automatically. If SL-5 finds its base is pre-SL-1, pre-SL-2, pre-SL-3 or
  pre-SL-4, it MUST stop and report rather than commit — the orchestrator re-spawns or
  rebases. A silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree
  produces commits that destroy peer-lane work on a `--no-ff` merge.
- **A fresh worktree must run `uv sync --all-extras -p 3.10` before any `uv run`**, or
  `uv run pytest` silently uses the system interpreter's pytest and the baseline counts
  mean nothing.
- **Expected out-of-lane fallout: bounded, and measured rather than hoped for.** SL-0 is
  the only lane that writes `src/`, and its threaded parameter is **optional with a
  `None` default**, so all 55 existing call sites keep their current behaviour and no
  existing assertion moves. The eight suites that call into that chain are named in
  SL-0.3's verify command and must be run there rather than discovered later. Every other
  lane is test- and documentation-only, so it can move nothing at all. `grep -rln SECURITY.md tests/ scripts/` returns nothing — the only reference is a
  comment in `.github/workflows/test.yml` — so the rewrite breaks no golden. If the repair
  pass finds a failure outside this phase's own files, something changed outside this plan
  and it should be reported, not repaired.
- **Expected add/add conflicts**: none. Eight new files, no lane stubs another's, and there
  is no package `__init__` to re-export from.
- **Trust the document over any brief, and report disagreements.** Node ids here were
  authored in the acceptance criteria first and the lane tables derived from them;
  generate a lane's list with `python3 scripts/check_plan_consistency.py --brief SL-2
  plans/phase-plan-v13-SEAL.md` rather than retyping it. Five lane briefs across two
  phases each dropped at least one node id when hand-transcribed, and one of the dropped
  ids was a security rule's only falsifier.

## Acceptance Criteria

- [ ] EC-SEAL-1 — SECURITY.md describes the implemented model with no claim the tests do not prove; each claim cites the test that proves it. Proven by `uv run pytest -q tests/test_security_claims_parser.py::test_a_wellformed_ledger_passes_every_rule tests/test_security_claims_parser.py::test_a_missing_claim_region_is_reported tests/test_security_claims_parser.py::test_a_missing_or_duplicated_ledger_is_reported tests/test_security_claims_parser.py::test_a_kind_outside_the_two_value_vocabulary_is_reported tests/test_security_claims_parser.py::test_a_guarantee_without_a_proof_is_reported tests/test_security_claims_parser.py::test_a_proof_entry_that_is_not_a_node_id_is_reported tests/test_security_claims_parser.py::test_a_limitation_carrying_an_uncharacterized_proof_is_reported tests/test_security_claims_parser.py::test_a_limitation_may_cite_a_characterization_and_nothing_else tests/test_security_claims_parser.py::test_a_guarantee_may_not_cite_a_characterization tests/test_security_claims_parser.py::test_a_limitation_block_must_be_labelled_as_one tests/test_security_claims_parser.py::test_a_node_id_naming_a_missing_file_is_reported tests/test_security_claims_parser.py::test_a_node_id_naming_a_missing_function_is_reported tests/test_security_claims_parser.py::test_a_node_id_naming_a_method_inside_a_class_is_reported tests/test_security_claims_parser.py::test_an_uncollectable_node_id_is_reported tests/test_security_claims_parser.py::test_a_claim_marker_with_no_ledger_row_is_reported tests/test_security_claims_parser.py::test_a_ledger_row_that_no_block_cites_is_reported tests/test_security_claims_parser.py::test_a_sentence_without_a_claim_marker_is_reported tests/test_security_claims_parser.py::test_a_second_sentence_in_a_block_must_carry_its_own_marker tests/test_security_claims_parser.py::test_a_line_that_is_neither_heading_nor_claim_block_is_reported tests/test_security_claims_parser.py::test_an_abbreviation_that_breaks_the_sentence_split_is_reported tests/test_security_claims_parser.py::test_duplicate_and_non_sequential_claim_ids_are_reported tests/test_security_claims_parser.py::test_every_v13_evidence_file_must_be_cited tests/test_security_claims_parser.py::test_a_previously_unproven_test_must_be_cited tests/test_security_claims_parser.py::test_a_v13_model_term_outside_the_region_is_reported tests/test_security_claims_parser.py::test_the_node_ids_subcommand_prints_the_cited_union tests/test_security_claims_parser.py::test_the_run_subcommand_fails_when_a_cited_test_fails tests/test_security_claims.py::test_the_shipped_security_md_passes_the_claim_checker tests/test_security_claims.py::test_every_cited_node_id_exists_and_collects tests/test_security_claims.py::test_every_v13_evidence_file_is_cited_by_a_guarantee tests/test_security_claims.py::test_every_limitation_is_labelled_and_cites_only_characterizations tests/test_security_claims.py::test_no_v13_model_term_appears_outside_the_claim_region tests/test_security_claims.py::test_no_guarantee_cites_a_test_outside_the_repository_suite tests/test_security_claims.py::test_the_security_policy_names_the_packaged_repository tests/test_security_claims.py::test_the_subprocess_spawning_limitation_names_the_v13_gates`, falsified at the structure layer by nineteen doctored `SECURITY.md` fixtures in `tmp_path`, one per frozen rule R1-R19, each asserting the checker names that rule and exits non-zero — a ledger whose delimiters are missing or duplicated, a third Kind value outside `{guarantee, limitation}` (the smuggling route IF-0-SEAL-1 names, and the rule whose absence from this criterion's first revision was the panel's blocking finding), a Proof entry that is not a node id at all, a guarantee row with an empty Proof cell, a limitation row carrying a bare uncharacterized one, a guarantee row using the `characterizes:` form to dodge the proof rule, a v13 lexicon term written outside the region and not on the frozen allowlist, a limitation block missing its `**Limitation.**` label, a marker with no ledger row and a ledger row no block cites, a claim block whose second sentence carries no marker, a loose prose paragraph inside the region, an `e.g.` that would split the sentence stream wrongly, duplicate and non-sequential ids, a census file no guarantee cites, and one of the eleven node ids no merged phase's criteria run left uncited — R17, which is what makes CONSENT amendment 1's ten unproven security properties and TRUST's one required at last, since neither merged plan's acceptance criteria can be edited now; falsified in the one direction the format must ALLOW by a limitation row whose Proof is a `characterizes:` list, which must pass, since an uncited limitation rots invisibly and a characterization is what fails when a limitation is silently closed; falsified at the resolvability layer by a citation naming a file that does not exist, a function that does not exist, and a method inside a class rather than a module-level `def`, each of which resolves for a regex and must not for `ast.parse`, plus one that exists in the file but does not collect; falsified at the liveness layer by `--run` against a fixture whose cited test fails, asserting a non-zero exit — the check that a claim's proof still passes, and the reason `--run` exists at all. Falsified at the shipped-file layer by running the checker over the real `SECURITY.md`, by asserting every one of the nineteen v13 evidence files is cited by at least one guarantee, and by asserting no guarantee cites a node id outside `tests/`, without which the liveness argument fails silently — CI runs `pytest tests/`, so only citations inside the suite are executed — and by asserting no v13 lexicon term survives outside the claim region in the shipped file, which is what stops a model claim being written where no marker is required. And at the legacy layer by asserting no `ViperJuice` occurrence survives in the file, with the advisory URL's owner compared against the packaged distribution metadata rather than a duplicated literal, and by asserting the subprocess-spawning bullet no longer reads as though nothing gates a project config entry. **Fails on `main`**: `SECURITY.md` contains no claim region, no ledger and no v13 trust model at all, `scripts/check_security_claims.py` does not exist, and `SECURITY.md:201` directs vulnerability reports to a repository this project does not own.
- [ ] EC-SEAL-2 — an adversarial suite drives the four review reproductions end to end and each fails closed, run against the real handlers rather than mocks. Proven by `uv run pytest -q tests/test_trust_boundaries_e2e.py::test_s01_provision_refuses_an_arbitrary_package_under_an_allowlisted_name tests/test_trust_boundaries_e2e.py::test_s01_connect_server_refuses_the_same_registration tests/test_trust_boundaries_e2e.py::test_s01_restart_server_refuses_the_same_registration tests/test_trust_boundaries_e2e.py::test_s01_update_server_refuses_a_discovered_server_before_any_probe tests/test_trust_boundaries_e2e.py::test_s01_the_refusal_names_the_package_and_the_approve_command tests/test_trust_boundaries_e2e.py::test_s01_a_package_manager_variable_is_refused_at_registration tests/test_trust_boundaries_e2e.py::test_s01_auth_connect_refuses_a_package_manager_variable_for_a_discovered_server tests/test_trust_boundaries_e2e.py::test_s03_an_unapproved_overlay_does_not_replace_a_shipped_command tests/test_trust_boundaries_e2e.py::test_s03_an_unapproved_project_mcp_json_is_not_applied tests/test_trust_boundaries_e2e.py::test_s03_an_approved_source_edited_afterwards_is_refused_again tests/test_trust_boundaries_e2e.py::test_s03_the_parsed_bytes_are_the_gated_bytes_at_every_loader tests/test_trust_boundaries_e2e.py::test_s11_an_unapproved_project_policy_is_not_read_at_all tests/test_trust_boundaries_e2e.py::test_s11_an_approved_project_policy_cannot_widen_the_user_policy tests/test_trust_boundaries_e2e.py::test_s11_an_approved_project_policy_cannot_drop_the_default_redaction_patterns tests/test_trust_boundaries_e2e.py::test_s04_an_ambient_github_token_submits_nothing_and_spawns_no_gh tests/test_trust_boundaries_e2e.py::test_s04_a_checkout_supplied_token_or_repository_is_refused tests/test_trust_boundaries_e2e.py::test_s04_no_egress_door_remains_in_the_handler tests/test_trust_boundaries_e2e.py::test_no_reproduction_in_this_suite_reached_the_network`, falsified for S-01 by registering `internal-approved-tool` with package `totally-arbitrary-evil-package` through the real `gateway.register_discovered_server` against `fake_npm_registry`, then driving `gateway.provision`, `gateway.connect_server`, `gateway.restart_server` and `gateway.update_server` in turn and asserting each refuses with no spawn recorded — four separate node ids because a parametrised case reports one failure for a regression at any door and lets a reviewer read "S-01 passes" while three doors are untested, which is exactly PKGID amendment 1's defect — and by asserting the refusal text names the package and the `pmcp trust approve-package name@version` command; falsified further at the identity layer by driving the F1 reproduction through the real tools, declaring `npm_config_registry` at registration and passing `NPM_CONFIG__AUTH` to `gateway.auth_connect` for a discovered server, asserting both are refused, because an agent-supplied field that reaches a spawn's environment is part of the package's identity rather than metadata about it; falsified for S-03 by writing a real `.pmcp/manifest.yaml` and a real project `.mcp.json` into a checkout with no approval recorded and asserting the shipped command is used and the added configuration is not applied, then recording an approval, editing the file, and asserting the approval no longer holds; falsified for the one-read rule — CONSENT amendment 3's explicit request, and the property its own criteria were structurally blind to — by changing the file's bytes between the gate and the parse and asserting the bytes that were applied are the bytes that were gated, at all three loaders; falsified for S-11 by an unapproved project policy that is not read at all, an approved one that allows a server the user policy denies (which must stay denied), and an approved one that omits `DEFAULT_REDACTION_PATTERNS` (which must not drop them) — the redaction widening being an S-11-class widening whose only falsifier shipped in no acceptance criterion at all; falsified for S-04 by setting `GITHUB_TOKEN` and `GH_TOKEN`, enabling the submission flag, calling with `confirm_submission=true`, and asserting no request was attempted by any route, no `gh` process was spawned, and the handler's own source contains neither `"gh"` nor `create_subprocess_exec`; and falsified at the isolation layer by asserting no test in the suite reached the network, as a node id an acceptance command runs rather than a fixture side effect nobody is required to prove — EGRESS's red run reached a live `gh issue create` on the development host against this same handler. **Red-run contract**: each of the four findings must additionally be shown red under one named mutation of the merged gate before the lane reports done, run in a lane-unique scratch directory with the working tree asserted equal to `HEAD` before and after. **Fails on `main`**: `tests/test_trust_boundaries_e2e.py` does not exist.
- [ ] EC-SEAL-5 — the **composition** cases fail closed too, not only the four original reproductions: an unapproved overlay that adds a server (EC-CONSENT-6), and an approval record shipped inside the checkout (EC-TRUST-5). Proven by `uv run pytest -q tests/test_trust_boundaries_composition.py::test_an_unapproved_overlay_that_adds_a_server_is_not_applied tests/test_trust_boundaries_composition.py::test_an_added_server_is_not_manifest_backed_at_provision tests/test_trust_boundaries_composition.py::test_an_added_server_is_not_manifest_backed_at_connect tests/test_trust_boundaries_composition.py::test_a_trust_store_shipped_inside_the_checkout_is_refused tests/test_trust_boundaries_composition.py::test_a_checkout_resident_store_is_refused_through_a_symlink tests/test_trust_boundaries_composition.py::test_an_approval_in_a_checkout_resident_store_grants_nothing tests/test_trust_boundaries_composition.py::test_a_denylisted_package_outranks_an_operator_approval tests/test_trust_boundaries_composition.py::test_an_approved_pin_still_refuses_an_unpinned_argv tests/test_trust_boundaries_composition.py::test_an_install_spawn_strips_the_credentials_pmcp_stores_hold tests/test_trust_boundaries_composition.py::test_the_install_spawn_strip_uses_the_gateways_project_root_not_the_working_directory tests/test_trust_boundaries_composition.py::test_no_composition_case_in_this_suite_reached_the_network tests/test_install_child_env_project_root.py::test_an_install_spawn_does_not_inherit_a_project_scoped_credential_when_the_cwd_differs tests/test_install_child_env_project_root.py::test_the_production_install_path_passes_the_gateways_project_root tests/test_install_child_env_project_root.py::test_a_project_scoped_credential_is_stripped_when_the_cwd_is_the_project_root tests/test_install_child_env_project_root.py::test_the_helper_falls_back_to_the_working_directory_walk_when_given_no_root tests/test_install_child_env_project_root.py::test_no_production_call_site_omits_the_project_root`, falsified for the add seam by an unapproved overlay that introduces a server name the shipped manifest does not carry, asserting it is not applied **and** — the half a per-phase suite cannot reach — that provisioning and connecting that name do not treat it as manifest-backed, since CONSENT refuses replacement, PKGID's default-deny exempts manifest-backed servers, and an unapproved package would otherwise execute through the gap between two phases that each pass their own criteria; falsified for the shipped-approval seam by placing the trust store inside the checkout directly and through a symlinked path, asserting the residency check refuses rather than answering, and by recording an approval into such a store and asserting it grants nothing — a caller that swallowed the raise would read the refusal as "no record", which is the same answer for the wrong reason; falsified for two seams inside PKGID's own decision order by asserting a `packages.denylist` entry outranks a recorded operator approval (or a narrow denylist silently never fires for exactly the packages an operator trusted) and that an approved pin still refuses an unpinned `npx -y pkg` argv (or an approved server re-resolves `latest` at spawn, which reopens S-01 for trusted servers); and falsified for the credential strip at the seam between EGRESS's provenance boundary and PKGID's spawn boundary, which is where planning found a live leak: `build_install_child_env` (`src/pmcp/manifest/installer.py:618`) is the only `sanitized_subprocess_env` caller that omits the project root, so under `pmcp serve --project X` run from another working directory a project-scoped credential written to `X/.env.pmcp` was not stripped and the install child inherited another server's secret. **The operator authorised fixing it in this phase rather than filing it**, so the falsification is of the FIXED behaviour and not of a characterization: build a gateway with an explicit project root, `chdir` elsewhere, write a project-scope credential through `env_store.set_env_value` into the gateway's root, drive the install path with the spawn recorded, and assert the recorded **child environment** does not carry that key — the helper called in isolation cannot see this, which is why the defect survived PKGID; assert the production path reaches `start_install` with `self._project_root`, which is the half that was actually wrong; assert the already-working case (working directory equal to the project root) still strips, so the fix trades no direction for another; assert what the optional default means, so the structural rule below has a behavioural partner; and assert **structurally, as a node id rather than as a Verification grep**, that no production call of `build_install_child_env` or of `sanitized_subprocess_env` omits a project argument, which is what makes an optional parameter safe when making it required would have rewritten 55 call sites across three merged phases' evidence files. **Fails on `main`**: `tests/test_trust_boundaries_composition.py` and `tests/test_install_child_env_project_root.py` do not exist, and the strip defect reproduces.
- [ ] EC-SEAL-3 — every refusal carries a remedy that is runnable as printed; every `pmcp` command a remedy names is a verb the CLI dispatches; and the refusals for which no `pmcp` verb exists are enumerated exhaustively from each gate's closed reason vocabulary, so that set can shrink but never grow silently. *(Amended wording, under the original id — the roadmap read "every refusal path prints the exact `pmcp` command that would grant the action, asserted for each gate", which is not satisfiable without inventing operator verbs; the operator chose amendment over new behaviour, and SL-docs makes the edit under SEAL's `canonical_spec_update` closeout. The proof below is unchanged from this plan's first revision.)* Proven by `uv run pytest -q tests/test_refusal_remedies.py::test_every_consent_refusal_reason_is_driven_by_this_audit tests/test_refusal_remedies.py::test_every_provision_refusal_reason_is_driven_by_this_audit tests/test_refusal_remedies.py::test_every_feedback_refusal_reason_is_driven_by_this_audit tests/test_refusal_remedies.py::test_every_refusal_carries_a_non_empty_remedy tests/test_refusal_remedies.py::test_every_pmcp_command_in_a_remedy_names_a_verb_the_cli_dispatches tests/test_refusal_remedies.py::test_every_remedy_is_runnable_as_printed tests/test_refusal_remedies.py::test_a_path_containing_a_space_yields_a_runnable_approve_command tests/test_refusal_remedies.py::test_a_path_carrying_shell_metacharacters_is_rendered_inert tests/test_refusal_remedies.py::test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy tests/test_refusal_remedies.py::test_a_provision_refusal_reaches_the_agent_facing_message_with_its_remedy tests/test_refusal_remedies.py::test_a_feedback_refusal_reaches_the_tool_output_with_its_remedy tests/test_refusal_remedies.py::test_a_trust_cli_refusal_names_the_command_that_would_grant_it tests/test_refusal_remedies.py::test_the_remedies_that_name_no_pmcp_verb_are_exactly_the_enumerated_set tests/test_refusal_remedies.py::test_no_remedy_audit_case_reached_the_network`, falsified at the exhaustiveness layer by reading each gate's **closed reason vocabulary out of the source** — `ConsentReason`'s `Literal` members, `PROVISION_REASONS`, and `FeedbackEgressReason`'s `Literal` members — and asserting the audit's case table covers every member with none left over, so a reason added by a later phase fails this audit on the day it is added rather than shipping with no remedy; falsified at the content layer by asserting every refusal carries a non-empty remedy, that every `pmcp …` substring any remedy contains resolves against the real argparse tree to a verb the CLI dispatches (a remedy naming a command that does not exist is the defect EGRESS avoided by declining to write `pmcp secrets rm`), and that every remedy containing a command survives `shlex.split` and round-trips the value it names; falsified at the quoting layer by a project path containing a space, which must yield a runnable `pmcp trust approve '/path/with a space/.mcp.json'`, and by a path carrying control characters and shell metacharacters, which must render inert under the escape-then-quote rule all four gates share — the rule CONSENT amendment 10a deferred here and which the shipped code already implements, so this criterion pins it rather than introducing it; falsified at the delivery layer, which is the difference between a predicate and a refusal, by driving each gate through its real surface and asserting the remedy reaches the operator: exactly one WARNING record naming the remediation for the consent gate, the agent-facing `message` for a lifecycle refusal, `SubmitFeedbackOutput.message` for the egress gate, and the printed output of a refused `pmcp trust` invocation; and falsified at the honesty layer by `test_the_remedies_that_name_no_pmcp_verb_are_exactly_the_enumerated_set`, which pins by reason id the five refusals for which no `pmcp` verb exists — a `packages.denylist` entry and an undetermined npx argv (both remedied by editing the operator's policy file), an unresolvable identity (remedied through a gateway tool), an unpinned argv (remedied in `.mcp.json` or by re-registering), and a planted credential (remedied by two store paths and a shell export) — so the set can shrink when a later phase adds a verb but cannot grow silently. **The original wording is not satisfiable and was not weakened to suit**: what is proven here is strictly stronger than a single unverifiable string would be, and the roadmap text is corrected to match the proof rather than the proof trimmed to match the text. Building the missing verbs was the alternative, and it was rejected as new behaviour in a phase whose charter is to document and prove. **Fails on `main`**: `tests/test_refusal_remedies.py` does not exist.
- [ ] EC-SEAL-4 — a fresh operator with no trust store and no project files sees unchanged behaviour for manifest-backed servers. Proven by `uv run pytest -q tests/test_fresh_operator_baseline.py::test_a_fresh_home_holds_no_trust_store_and_no_package_approvals tests/test_fresh_operator_baseline.py::test_a_manifest_backed_server_provisions_with_no_trust_store tests/test_fresh_operator_baseline.py::test_a_manifest_backed_server_connects_with_no_trust_store tests/test_fresh_operator_baseline.py::test_a_manifest_backed_server_restarts_with_no_trust_store tests/test_fresh_operator_baseline.py::test_no_project_file_means_no_consent_gate_is_consulted tests/test_fresh_operator_baseline.py::test_a_fresh_operator_sees_no_new_startup_warning tests/test_fresh_operator_baseline.py::test_every_shipped_manifest_entry_is_determinable_without_a_denylist`, falsified by asserting the premise first — that the isolated home holds no trust store file and no package-approvals file, so the other six cannot be vacuously green if the autouse redirect ever stops working — then driving `gateway.provision`, `gateway.connect_server` and `gateway.restart_server` for a manifest-backed server under that empty home and asserting each proceeds, because assumption 5 says a default-deny that silently breaks a working setup is a failed phase; falsified for the consent side by running from a directory containing no `.pmcp/manifest.yaml`, no `.mcp.json` and no `.mcp-gateway-policy.yaml` and asserting the consent gate is never consulted and no warning is emitted, since a gate that fires with nothing to gate is a behaviour change an existing operator would feel; and falsified for PKGID's one documented exception to "a manifest-backed server is unaffected" by asserting that with no `packages.denylist` in force, every shipped manifest entry's install argv is determinable, so nothing an operator ships is refused as undetermined on a default configuration. **Fails on `main`**: `tests/test_fresh_operator_baseline.py` does not exist.

## Verification

```bash
uv sync --all-extras -p 3.10      # a fresh worktree's `uv run` otherwise picks SYSTEM pytest

uv run pytest -q tests/test_security_claims_parser.py tests/test_security_claims.py
uv run pytest -q tests/test_trust_boundaries_e2e.py tests/test_trust_boundaries_composition.py
uv run pytest -q tests/test_refusal_remedies.py tests/test_fresh_operator_baseline.py
uv run pytest -q tests/test_install_child_env_project_root.py tests/test_credential_child_env.py

# The whole v13 evidence set, which is what SEAL's closeout collects and what the
# roadmap's own Verification block should have named. Nineteen files from four merged
# phases plus this phase's seven.
uv run pytest -q tests/test_trust_store.py tests/test_trust_cli.py tests/test_package_identity.py \
                tests/test_project_consent_gate.py tests/test_project_source_consent_manifest.py \
                tests/test_project_source_consent_config.py tests/test_project_source_consent_policy.py \
                tests/test_package_identity_gate.py tests/test_package_approvals.py \
                tests/test_policy_package_identifiers.py tests/test_install_argv_logging.py \
                tests/test_pkgid_panel_fixes.py tests/test_pkgid_spawn_logging.py \
                tests/test_pkgid_manifest_npx_selectors.py tests/test_feedback_egress.py \
                tests/test_feedback_egress_gate.py tests/test_feedback_provenance.py \
                tests/test_feedback_submission_flag.py tests/test_egress_panel_fixes.py

uv run pytest -q tests/                 # compare counts to the pre-phase baseline, same dir
uv run ruff check src/ tests/ scripts/ && uv run ruff format --check src/ tests/ scripts/
uv run mypy src/

# EC-SEAL-1: the claim binding, as a reviewer runs it. `check` is structure and
# resolvability; `--run` additionally executes every cited test and fails if one fails.
uv run python scripts/check_security_claims.py
uv run python scripts/check_security_claims.py --run
uv run python scripts/check_security_claims.py --node-ids | sort > /dev/null   # parses

# Every cited node id lives in the suite CI already runs, which is the whole liveness
# argument. A citation outside tests/ would resolve and never execute.
uv run python scripts/check_security_claims.py --node-ids | grep -cv '^tests/'
# ^ MUST print 0.

# SEAL's non-goal, made checkable, with its one authorised exception named rather than
# blanket-permitted.
git diff --name-only origin/main..HEAD -- src/ | sort
# ^ MUST be EXACTLY these two lines, SL-0's:
#     src/pmcp/manifest/installer.py
#     src/pmcp/tools/handlers.py
#   Any third path is a lane making an unauthorised behaviour change; if a reproduction
#   genuinely failed to fail closed, that is an escalation (Execution Notes), not a
#   silent commit. An EMPTY result is also a failure: it means SL-0 did not land.

# SL-0's hazard, closed structurally because the threaded parameter is optional. Both
# checks are also asserted as a node id -- test_no_production_call_site_omits_the_project_root
# -- which is what makes them required rather than merely written down here.
rg -n 'build_install_child_env\(' src/
# ^ Every CALL must pass a project-root argument; only the def in installer.py may not.
rg -n 'sanitized_subprocess_env\(' src/
# ^ Every CALL must pass a project argument. The def and the docstring mentions in
#   env_store.py are the exceptions; a bare call anywhere else re-opens the leak SL-0
#   closed, because resolve_project_root then walks up from the working directory.

# The claim region exists, is unique, and is the only thing the checker binds.
grep -c 'TRUST-MODEL-CLAIMS: BEGIN' SECURITY.md   # MUST be 1
grep -c 'TRUST-MODEL-CLAIMS: END' SECURITY.md     # MUST be 1
grep -c 'CLAIM-LEDGER: BEGIN' SECURITY.md         # MUST be 1
grep -c 'CLAIM-LEDGER: END' SECURITY.md           # MUST be 1

# The advisory destination is this project's. #247 keeps the version_checker.py
# occurrence; SECURITY.md is SEAL's file, so this one is closed here.
grep -n 'ViperJuice' SECURITY.md
# ^ MUST be empty. Today it returns exactly one hit, SECURITY.md:201.

# Every phase's evidence is cited. A v13 test file added later with no claim fails R16,
# which is the mechanism, not this grep; the grep is what a reviewer reads.
uv run python scripts/check_security_claims.py --node-ids | cut -d: -f1 | sort -u
# ^ MUST list all nineteen merged-phase evidence files, plus the five suites this phase
#   adds that prove model behaviour (the four boundary suites and SL-0's child-env one).
#   `tests/test_security_claims_parser.py` and `tests/test_security_claims.py` are about
#   the checker, not the model, and are the two files a claim has no reason to cite.

# The plan's own two registries agree, and the roadmap pin is fresh in all five plans.
# SL-docs edits the roadmap, so run the GLOB, not this file alone.
python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md
```

Host note: `/tmp/package.json` makes ~107 npm-identity tests fail on some hosts
(`tests/conftest.py`); compare the same command from the same directory before and after,
never against a clean-machine expectation. On a worktree volume, a `node_modules`
directory at the volume root has the same effect for the same reason.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `SECURITY.md`
- evidence paths: `tests/test_trust_boundaries_e2e.py`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- missing or malformed evidence routes to `blocker_class=contract_bug` (non-human).
- **What `canonical_spec_update` obliges, and why it is different from the other four
  phases.** TRUST, CONSENT, PKGID and EGRESS all declare `no_spec_delta`: each records
  what shipped in an amendment block and leaves the phase's original roadmap text
  unedited. SEAL declares `canonical_spec_update`, so its closeout does not merely append
  a record — **it updates the canonical spec to match what shipped.** Concretely, SL-docs
  owes three edits to `specs/phase-plans-v13.md` that no other phase was permitted to
  make:
  1. **The roadmap's `## Verification` block is corrected to run the real evidence set.**
     It runs four test files today, one of which — `tests/test_project_source_consent.py`
     — has never existed and was never creatable (CONSENT amendment 5). EGRESS amendment
     6(d) asks for this explicitly. The corrected block is the set named by the commands
     in `## Verification` above: nineteen merged-phase files plus this phase's seven.
  2. **The census is recorded, with its two new undercounts.** TRUST added three test
     files and no amendment records the gap; EGRESS added five and its own amendment names
     four, omitting `tests/test_egress_panel_fixes.py`. That is the fourth recurrence of
     one defect, and recording it in the canonical spec — rather than in a fifth
     amendment that a sixth phase would have to read — is what this decision is for.
     Phase 5's **Key files** entry should be read as naming `SECURITY.md`, `CHANGELOG.md`,
     `scripts/check_security_claims.py`, `src/pmcp/manifest/installer.py`, `src/pmcp/tools/handlers.py` and the seven test files this phase adds.
  3. **EC-SEAL-3's text is amended, under its own id**, to what is provable: every
     refusal carries a runnable remedy; every `pmcp` command a remedy prints resolves to a
     verb the CLI dispatches; and the refusals naming no verb are enumerated exhaustively
     from each gate's closed reason vocabulary. The original — "every refusal path prints
     the exact `pmcp` command that would grant the action" — is not satisfiable without
     inventing operator verbs for a policy-file edit, an unresolvable identity, an
     unpinned argv and a planted credential. **The operator chose amendment over new
     behaviour**, and this is the only closeout in the roadmap permitted to make it. The
     block must record the original wording, why it is unsatisfiable, and that choice.
  4. **The SEAL post-execution amendment block is written**, in the form the four merged
     phases use: what shipped, what this roadmap's SEAL text is contradicted by, and the
     items handed forward. It must record at minimum the EC-SEAL-3 amendment above; that
     CONSENT amendment 10a was already resolved in CONSENT's own merge; that this phase's
     Scope notes prescribe two lanes where seven were needed; the SL-0 behaviour change
     with the non-goal exception it took and why; and the one issue filed.
- **The pin obligation that follows.** Editing `specs/phase-plans-v13.md` changes its
  digest, so all five `plans/phase-plan-v13-*.md` files' `roadmap_sha256` go stale in the
  same commit. Re-pin all five, and run
  `python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md` over the glob. No
  workflow under `.github/` invokes that checker, so nothing else will catch it.

## Execution Policy

- default: effort=medium
- SL-0: effort=high, reason=the phase's only behaviour change and its only `src/` writer; narrow in lines and wide in consequence, and the obvious helper-level test is the one that missed the defect in the first place
- SL-1: effort=high, reason=this is the phase's contract rather than a helper; nineteen rules with a sentence-splitting discipline that has to be exact rather than heuristic, and a checker nobody has run is a checker nobody has tested
- SL-2: effort=high, reason=four reproductions through seven real handlers, with a contracted mutation proof per finding, against gates whose two hardest defects were both "the door you were not looking at"
- SL-3: effort=high, reason=the seams no per-phase suite can see, plus a characterisation test for a live mismatch the lane must assert and must not fix
- SL-4: effort=medium, reason=three closed vocabularies driven exhaustively through real surfaces; mechanical once the case table is right, and the case table is what the exhaustiveness tests pin
- SL-5: effort=high, reason=sole writer of the file the phase is named for, and the only lane where a sentence that overclaims is a shipped defect rather than a failed test
- SL-docs: effort=medium, reason=the only `canonical_spec_update` closeout in the roadmap, three spec edits, five pin updates and two issues
