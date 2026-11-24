# President scope ruling: Consiliency/pmcp#476 (PowerShell runtime-evaluation residual) for Consiliency/pmcp#475

**NO FINAL PANEL APPROVAL.** This ruling settles only the scope question in Consiliency/pmcp#476. It does not approve merging Consiliency/pmcp#475, it does not certify any implementation, and it does not stand in for any missing or incomplete vendor seat.

## Identity and provenance

- **President:** Anthropic Claude Opus 5.5 (`claude-opus-5-5`), native in the T3 Claude Code harness. The owner authorized this president ruling (memory `president-adjudicates-long-boards`; HANDOFF 2026-10-14 §4).
- **No subagents and no model substitution.**
- **Checkout:** prepared T3 worktree `/home/viperjuice/.t3/worktrees/pmcp/t3-5aad3181`.
  - It was clean before and after (`git status --porcelain` was empty).
  - I fetched `origin wip/475-f4-f5-root` and `origin fix/472-guard-d-bash-grammar`, then detached at the implementation.
- **Implementation ruled against:** `b324cee56843bbca2e5f81ef78c5ec4423bcb1cb`. That is `origin/wip/475-f4-f5-root`, and `7af323e` is its ancestor.
- **PR head:** `7af323ecbcbfab9800b807f8fe2ced7c0e1ce781`, unchanged. It does not contain the `7af323e..b324cee` fixes.
- **Commits in the diff I read:**
  - `1c69b1c` refuses static HTTP message and handler API references (C12).
  - `4cbc544` updates the evidence doc.
  - `4509f26` preserves PowerShell token boundaries and reads grouped native options.
  - `b324cee` keeps grouped native readings separate from cmdlet aliases.
- **dev0 was static only.** I did not import or run the guard or parser, and ran no tests or probes. I changed no shared tooling, edited no source, and did nothing on GitHub (no push, label, comment, merge or watch).
- **What I read:**
  - #472 (body, including the "PowerShell web cmdlets" flag-or-record route) and #476 (narrowed body, updated 2026-11-04T04:50:57Z).
  - The memories `president-adjudicates-long-boards` and `defer-timing-window-races`, and HANDOFF §4.
  - The `t3-panel-7af323e` reports: `claude/`, `codex/` and `grok/review.md`.
  - The evidence doc at `b324cee`: §PowerShell web cmdlets, §Out of reach (AC1), and the residual and C12 paragraphs.
  - The full diff `7af323e..b324cee`.

## The authority I am applying

- **#472's PowerShell route.** For `Invoke-WebRequest`/`Invoke-RestMethod`/`iwr`/`irm` writes (and `curl` as a Windows PowerShell 5.1 alias), #472 says "either flag these forms (fail closed) … or record them as an AC1 residual, with one mutant per decision that only that rule can kill".
  - Its stated rationale is that these writes do not block G. Any such write also needs a registry credential, and guard (c) withholds that credential from every job except `image.yml:promote-main`.
  - So the route allows a recorded residual. It does not say where the line falls. That choice is this ruling.
- **The owner bar (HANDOFF §4).** I block only on three things:
  - a leak of a secret or rejected value;
  - an accept-where-it-should-refuse;
  - a process, thread or fd that outlives pmcp in normal operation.
  A residual this ruling records correctly is not an accept-where-it-should-refuse, because #472 lets it be recorded. A static spelling the guard can recognize without evaluating anything gets no such protection.
- **#476's own narrowing.**
  - Static defects (C12, P-BOUNDARY, P-GROUP and the listed lexical classes) are excluded.
  - An omitted fixed literal request API is not automatically residual.
  I keep both of these unchanged.

## The test that decides each item

I accept an item as AC1 residual only if **both** of these hold:

1. **The step's source text alone does not decide it.** That covers values from outside the step's text (environment, step outputs, files, network, the pipeline, profiles, modules), and values that flow through variables or objects between statements. This matches the Bash AC1 text: "commands assembled at runtime (`eval "$X"`, an unquoted `$OPTS` …, aliases and functions)" and "an unknown runtime value alone".
2. **No cheap rule exists that refuses it without evaluating it** and stays narrow enough not to refuse ordinary steps. The guard already uses such rules: it refuses any `-Method` expression, it refuses `Invoke-Expression`/`iex` by name, and it joins literal `+` chains.

An item that fails either test is **not** residual. It must be fixed (fail closed) or ruled on individually.

## Per-item rulings

### 1. Unknown variables and variable indirection — **ACCEPTED as AC1 residual**

**In scope:**
- invocation names, arguments, types or members that come from a variable, parameter, environment value, step output, file, pipeline or network value (`& $cmd -Method Put`, `& $sb`);
- the same names when the variable was assigned from an in-step literal in an earlier statement.

**Rationale:**
- Following a value across statements is data-flow evaluation.
- The Bash guard records the same class as AC1 ("an unknown runtime value alone still remains AC1", evidence doc ~:505; and the Out-of-reach bullet).
- #472 permits recording it.

**Qualifications:**
- Literal web-cmdlet, type or namespace names that appear anywhere in the step text keep their existing refusals. This acceptance does not exempt any text the guard already refuses.
- A cheap tail rule is advisory and does not block: refuse `&`/`.` applied to any non-literal operand when the tail has a write-shaped `-Method`/`-CustomMethod`/splat. The committed workflows contain no PowerShell `&`-invocation of this kind, so the false-positive cost is low. The rule required under item 5 covers part of this anyway.

### 2. External state — **ACCEPTED as AC1 residual**

**In scope:** state created **outside the step's own text**:
- PowerShell profiles;
- `$PSDefaultParameterValues` and preference variables set by the runner image, an earlier step or an imported module;
- aliases, functions and modules defined or imported from outside the step, such as the runner image, PSGallery modules or files written by earlier steps.

**Rationale:** the step text cannot show this state. This is the PowerShell counterpart of the Bash AC1 entries for `.curlrc`/`wgetrc`, `-K`/`--config`, aliases and functions.

**Qualifications:**
- In-step `$PSDefaultParameterValues` assignments stay refused. That includes scoped and braced spellings, which are already refused.
- In-step `Set-Alias`/`New-Alias`/`sal`/`nal` with a literal web-cmdlet target is **not** covered by this acceptance. It is a fixed literal construct in the step. Fix it or rule on it individually.
- Function bodies written in the step are already scanned as text, so they need no residual.

### 3. Reflection — **ACCEPTED only where the target is not source-literal**

**In scope:** `Invoke`/`InvokeMember`/`GetMethod(...)`/`MethodInfo.Invoke`/`Activator` and similar, but only where the object, type or member being reflected on comes from a variable, an external object or an expression covered by item 1 or 2.

**Not covered:**
- **Reflection whose type or member name is source-literal** (a quoted string, a literal `+` chain, or a literal `-f`/`-join`; see items 5 and 6) and names a request-capable type or member. Under the #476 clarification this is a fixed literal API. It must reach the existing type and namespace reference refusals. Where the name is outside the refused families, fix it or rule on it individually.
- **Fixed evaluation entry points:** `[scriptblock]::Create`, `$ExecutionContext.InvokeCommand.NewScriptBlock`/`InvokeScript`, `Add-Type` with source text, and child `pwsh`/`powershell -Command`/`-EncodedCommand`. These are fixed literal APIs that play the same role as the already-refused `Invoke-Expression`/`iex`. This acceptance does not cover them: fix each or rule on it individually.

  I have not measured the current guard on these; I list them because the boundary does not reach them, not as certified findings.

**Rationale:** this keeps #476's line between evaluation and list membership. Without that line, "reflection" would swallow every omitted literal API, the circular reading that Codex C12 and Claude rejected.

### 4. Indirect objects — **ACCEPTED only for objects of unknown or external origin**

**In scope:** a method called on an object obtained from an imported module, external code, a pipeline value, or a variable whose type is not decided by a literal constructor in the step. This is #476's third example ("unknown `$client` method after the object/type was supplied by an imported module").

**Not covered:** objects created in the step by a literal constructor or type reference of a request-capable API. That is the C12 class: `[System.Net.Http.HttpMessageInvoker]::new(...)` then `.SendAsync(...)`, or `New-Object` with a literal type name. Such objects must be refused. `1c69b1c` implements that for the `System.Net.Http` family. I do not certify that fix here; see below.

### 5. Literal `-f` (format operator) — **REJECTED as residual**

**Ruling:** `& ('Invoke-{0}' -f 'RestMethod') -Method Put -Uri $u` and its equivalents must be refused (fail closed) in #475 or a follow-up that lands **before** merge. They may not be recorded as AC1.

**Rationale:**
1. Every operand is in the step text at the point of use, inside one expression. Nothing crosses a statement and nothing is external. This is the same information class as the literal `+` chains that `_PWSH_LITERAL_CHAIN` already joins. Grok's objection is correct.
2. Refusing it does not need a PowerShell evaluator. The residual's stated justification, that this is program evaluation, holds only if refusal would need evaluation, and here it does not.
3. The committed workflows contain no PowerShell `-f`/`-join`/`[string]::` use (grep of `.github/workflows/`, static), so a conservative rule costs essentially nothing in false positives.
4. Accepting it would let #476's sentence "This includes expression evaluation even when its operands happen to be literal" stand. That sentence is **rejected**.

**Concrete scope requirement.** Any one of these satisfies it:
- **(a) Name position.** Where the operand of an `&`/`.` invocation operator, or a command-object expression, contains a `-f` operator (any letter case), refuse when the tail meets the existing cmdlet write policy (a non-GET/HEAD `-Method`, `-CustomMethod`/`-CM` or abbreviations, or a splat).
  - Unconditional refusal of such invocations is also acceptable.
  - Document GET/HEAD twins either as unflagged or as disclosed false positives.
- **(b) Type position.** Where a `-f` result is used as a type or member name (`New-Object`/`-TypeName`, `[type]` conversion or `-as`, `[Type]::GetType(...)`, reflection member-name arguments), refuse unconditionally, or apply the existing type/namespace reference refusals to the fully literal formatted result.
- **(c) Literal evaluation (optional).** The implementer may instead evaluate a **defined literal subgrammar**: a quoted format string with only `{n}` placeholders and `{{`/`}}` escapes, and quoted literal operands. Then apply the existing name, type and namespace policies to the result. Anything outside that subgrammar (alignment or format specifiers, non-literal operands) must refuse in the positions in (a) and (b), not pass.
- **Static .NET equivalents.** `[string]::Format`, `[string]::Join`, `[string]::Concat` and `[System.String]::…` are fixed literal APIs (#476 clarification). They are covered by the same requirement, or each needs an individual ruling.
- **Evidence:**
  - the #476 example, and Grok's measured text, at the workflow entry;
  - a GET/HEAD twin with its documented result;
  - a type-position case;
  - an independent negative control that only this rule kills, per #472's "one mutant per decision".

### 6. Literal `-join` — **REJECTED as residual**

**Ruling:** this is the same as item 5. `& (-join ('Invoke-', 'RestMethod')) -Method Put -Uri $u` and binary `('Invoke','RestMethod') -join '-'`, built from literal operands, must be refused (fail closed). They may not be recorded as AC1.

**Rationale:** the same as item 5. The array operands and the delimiter are literal at the point of use.

**Concrete requirement:**
- the same positions as (a) and (b) above, for both unary `-join` and binary `-join <literal delimiter>`;
- the same evidence and negative-control requirement, with a separate negative control for the `-join` rule.

The optional evaluation path (c) must cover only literal array elements and a literal delimiter, and must refuse otherwise.

### Scriptblocks (named in #476)

**Not needed as a residual for literal blocks.** A literal `& { … }` body is part of the step text and is already scanned. A scriptblock that comes from a variable or from outside the step falls under item 1 or 2 (accepted). A scriptblock built from a string falls under item 3's evaluation entry points (not covered).

## Overall disposition

- **Accepted:** the #476 runtime-evaluation boundary, narrowed to items 1–4 as qualified above.
- **Rejected:** the clause that puts literal-operand expression evaluation (`-f`, `-join`) in the residual. This needs a fix.
- **Unchanged:** C12, P-BOUNDARY, P-GROUP and every other static defect stay outside the residual, as #476 already states. This ruling waives none of them.

### Conditions for the acceptance to take effect

1. **Evidence doc and PR body.** Replace "format/join operators" in the residual list (evidence doc ~:627-629, and the "Dynamically constructed command names" sentence at ~:103) with the exact accepted classes 1–4 and their exclusions. Cite Consiliency/pmcp#476, Consiliency/pmcp#472 and this ruling with its implementation SHA. Keep "not a complete PowerShell evaluator", and make no claim that arbitrary PowerShell or .NET writes fail closed. List the conservative false positives (HTTP API family references, `"$(…)"` and here-strings on recognized calls, continued-comment refusals, and the URL path containing `WebRequest`).
2. **Mutants.** Every fail-closed rule added for C12, P-BOUNDARY, P-GROUP and items 5 and 6 has an independent negative control that only that rule kills. The recorded residual classes have no rule to mutate; they are satisfied by being named, with examples, in the evidence doc.
3. **Dependence on guard (c).** This acceptance relies on #472's statement that guard (c) withholds registry credentials from every job except `image.yml:promote-main`. If guard (c), the token defaults or the repository secrets change so that another job could hold a registry or package write credential, this ruling lapses and must be redone. It also changes nothing in #472's requirement that #472 be done before `PMCP_IMAGE_PUBLISH` is ever set to `enabled`.
4. **Scope of the ruling.** It is made against implementation `b324cee`. It carries to a later implementation or PR head only if the residual text is not widened beyond items 1–4. Consolidating `b324cee` (or its successor) onto the PR head is still required. The PR head `7af323e` itself has none of these fixes.

## Items outside this ruling that remain open (not certified and not waived)

- **C12 (Codex):** fix in `1c69b1c`. Root reports 1289 guards passing, the C12 control killed, and mypy and workflow checks clean. I have not verified this myself, and it is not certified by me.
- **P-BOUNDARY and P-GROUP (Grok):** fixes in `4509f26` and `b324cee`, with tests for the measured texts and read controls. Validation for `b324cee` (guard run and negative controls) is **pending**. This ruling certifies neither fix.
- **Gemini seat:** `t3-panel-7af323e/gemini/review.md` **exists on disk** (mtime 2026-11-04 00:02:17 -0500, verdict DISAGREE). The brief describes Gemini as outstanding, so root should reconcile it. That report's blocking findings:
  - GM-NEW-1: Bash `((` after compound prefixes;
  - GM-NEW-2: array subscripts that contain `}` or `]`;
  - GM-NEW-3a–d: PowerShell `:` and `,` token boundaries, `${…}` braced variables, quoted path- or provider-qualified command names, and the workflow entry checking only `_joined` text for PowerShell;
  - GM-PROC-1.

  All of these are static or entry-point classes, **outside** this residual. My static reading of `7af323e..b324cee` shows:
  - no change to the Bash lexer;
  - the PowerShell boundary set is `(){}[];|`, without `:` or `,`;
  - no change to the `image_publication_invariants` PowerShell call.

  So these look unaddressed at `b324cee`. That is an observation from reading the code, not a measurement. Each must be fixed or ruled on individually.
- **Coverage and format:** the single-file coverage command fails (0%, exit 1) and the whole-repository format check fails on two unchanged diagnostics files. Both are disclosed failures and **not waived**.
- **Panel:** no complete four-vendor panel exists. The Claude seat is incomplete; Codex, Grok and Gemini all say DISAGREE. A new complete panel on the consolidated head, completed validation, owner acknowledgement and exact-head CI are all still required.

## Limitations

- This ruling is static and evidential only. I ran no guard code, tests, probes or PowerShell. The PowerShell semantics I rely on come from the seats' source citations and are not live-verified, and no `pwsh` exists on the validation hosts.
- I read no CI checks, PR review state or labels for this ruling.
- The "low false-positive cost" claim rests on a grep of the committed `.github/workflows/` tree at `b324cee` only.
- The examples in item 3 (evaluation entry points) and item 2 (in-step aliases) mark where this acceptance stops. They are not measured findings.

**NO FINAL PANEL APPROVAL.** This is a scope ruling only.

PR https://github.com/Consiliency/pmcp/pull/475, head 7af323ecbcbfab9800b807f8fe2ced7c0e1ce781 (implementation b324cee56843bbca2e5f81ef78c5ec4423bcb1cb), checks and reviews not read; scope ruling only.
