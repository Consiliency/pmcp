# Detailed plan: record `tools/call` gate rejections in the scoped-advisor audit, without argument values

> **Revision 4 (2026-09-26), rebased on main `876fd33` (2026-09-27).**
> Consiliency/pmcp#296, the prerequisite for piece B (`extra="forbid"`) of
> Consiliency/pmcp#236. The change is **embedded, not described**: the five
> blocks under *Verbatim bodies* are `git apply` patches against
> `origin/main` @ `876fd33`, byte-identical to the verified code on the
> local-only branch `wip/296-code` @ `6ceff9a` (not pushed; rev 1 was
> `9ced94f`, rev 2 `e10304a`, rev 3 `e4e1bbb`, rev 4 `6ab3db9`). Proven by
> extracting them from this file, `git apply --check` on a clean `876fd33`
> tree, applying, and `cmp` against `wip/296-code` (see *Embedding proof*).
>
> **Base move.** Revisions 1–4 were written against `959d4d4`. Main moved to
> `876fd33` (PR 303: `auth.py`, `keyword_matcher.py`,
> `tests/test_keyword_matcher.py`, one CHANGELOG line). `origin/main` was
> merged into `wip/296-code` (merge commit `80d0f93`, no rebase; then
> `6ceff9a`, which writes the sweep's zero-width space and Cyrillic
> look-alikes as `\u` escapes so the test source and this plan are
> ASCII-visible — same strings, test-only). The one
> conflict was CHANGELOG `[Unreleased]` → `### Fixed`, resolved as: the
> Consiliency/pmcp#296 entry first, then main's new `sanitize_auth_diagnostic`
> entry, then the rewritten Consiliency/pmcp#236 entry (main had not changed
> that line). `git diff 6ab3db9 80d0f93` touches none of this plan's four
> other files, so `src/` and the tests are byte-identical to rev 4; the
> server, audit, test and README patches are byte-identical too, and only
> the CHANGELOG patch was regenerated. Measurements below name the tree they
> ran on: research, probes and mutants ran on `959d4d4`-based trees (the
> plan's code is unchanged since); the embedding proof, gates, test module and
> full suite were rerun on `876fd33`.
>
> **What rev 4 changes** (the rev 3 board's claude seat: PARTIALLY AGREE, no
> code defect, four fresh mutants survived — the third round in a row from one
> root, a hand-picked caller-value generator): **tests and plan text only; the
> other four patches are byte-identical to rev 3's.** The hand-picked caller
> values are replaced as the leak oracle by a **generated sweep**: every
> registered gateway tool × every path through `_handle_call_tool`, with keys,
> value shapes and positions generated over axes derived from what
> `record_invocation` reads and filters, and a differential-plus-baseline
> oracle that names no channel (§7). `ledger_fields.py` now derives the
> reducer's reads by taint analysis, not by a variable's name. See *Rev 3
> board findings — before/after*. The sweep also surfaced one pre-existing
> log echo on main, which Consiliency/pmcp#297 already names (*Non-goals*).
>
> **What rev 3 changed** (the rev 2 board's claude seat: PARTIALLY AGREE, no
> code defect, four non-equivalent mutants survived): **tests and plan text
> only — `src/` is byte-identical to rev 2** (`git diff e10304a e4e1bbb --
> src/` is empty). New tests pin what the declared-keys filter must keep for
> the ledger (every field agent-harness's reducer reads, derived from
> `research.py`; the source hash from `invoke.arguments`) and what it must not
> read (a blocked tool's declared key; the `except` arm of an allowed tool);
> E16 is pinned for `KeyError`/`TypeError` too; a CHANGELOG cross-reference is
> corrected; the `source_reference_hash` channel on non-invoke tools is
> recorded as a follow-up. See *Rev 2 board findings — before/after*. Line
> numbers into `src/` below are therefore still those of `e10304a`.
>
> **What rev 2 changed** (the rev 1 board's claude seat returned DISAGREE; every
> finding is resolved in *Rev 1 board findings — before/after*): the
> policy-denied arm handed unvalidated arguments to `record_invocation`, and
> "policy first" widened that to malformed blocked calls (B1). Rev 2 fixes the
> class, not the instance: **every** `audit.invocation` record now reads only
> the registry's tool name and the top-level keys the tool's schema declares,
> of arguments that passed the gate — so a denied or unregistered call records
> nothing the caller chose, and an undeclared key of an allowed call is not
> read either (design §6; every writer call site is listed with a disposition
> in *Every path into the audit writer*). Also: tests that kill the seat's
> surviving mutants X1–X6, and a new exit E16 (an exception while describing
> a rejection) that now fails closed.
>
> Decisions, each argued from the code below:
> - **A new event, `audit.rejection`, with `terminal_status:
>   "invalid_arguments"`** — not an `audit.invocation`. The one downstream
>   reader (agent-harness's board ledger) correlates every `gateway.invoke`
>   invocation to its run, and a correlation-free invocation record would fail
>   the seat's whole research ledger as `audit_correlation_mismatch`
>   (measured from its source below). The invocation vocabulary stays
>   `success`/`failure`/`denied`; the capability stays `scoped_advisor_audit.v1`.
> - **Policy is judged before the gate, and read once.** A call to a blocked
>   tool is `denied` whatever its arguments; the single verdict feeds both the
>   gate and `call_tool`'s `denied` arm.
> - **The record carries the tool, the failing JSON path and the validator
>   keyword, and the writer's own envelope — nothing else.** A dedicated
>   `record_rejected_arguments` builds it;
>   it never goes through `record_invocation`, whose correlation, source-hash
>   and digest channels all read argument values.
> - **Caller-chosen keys in the path are themselves a leak and are redacted to
>   `null`.** A path segment survives only as a key some `properties` map in
>   the tool's schema declares, or as an index into a list.
> - **(rev 2) The audit reads only what the gate vouched for.** One pair of
>   variables, set in `_handle_call_tool` before `call_tool`, feeds all three
>   `record_invocation` call sites: `audited_name` (the registry's `tool.name`,
>   or `None` for an unregistered name) and `audited_arguments` (the declared
>   top-level keys of gate-passed arguments; `None` for a blocked or
>   unregistered call). The denied arm also stops digesting its response
>   payload, which echoes the caller's name.

## Task

Address Consiliency/pmcp#296. `GatewayServer._handle_call_tool`
(`src/pmcp/server.py:281-471` on `main`) validates arguments with
`jsonschema.validate` and, on `ValidationError`, returns
`CallToolResult(is_error=True, "Input validation error: …")` at `:300-308`
**before** `call_tool` is entered. So a gate rejection never reaches
`_record_scoped_invocation`, and the scoped-advisor audit sees no attempt.
Piece A of Consiliency/pmcp#236 (PR 300, `cdaf3c6`) moved more rejections onto
that early return; piece B moves every unknown-key call there. This must land
before B.

Required by the issue and the coordinator:

1. Decide the `terminal_status`.
2. Decide whether a policy-blocked tool is judged before or after the gate,
   and preserve `denied` for a malformed call to a blocked tool.
3. The record carries tool name, failing JSON path, validator keyword only —
   never the jsonschema message, the instance, or any argument value. Fix the
   class: prove no instance-derived field reaches the record, including
   caller-supplied dict keys in `error.path` and the key an
   `additionalProperties` error names.
4. Enumerate **every** exit of `_handle_call_tool` and its audit fate.

## Rev 1 board findings — before/after

Board 1 on rev 1 (`f24ede9`): the native claude seat (claude-opus-5-5,
correctness) returned **DISAGREE**. Every finding, what rev 1 did, and what
rev 2 does — each "after" measured on `wip/296-code` @ `e10304a`.

| finding | rev 1 (before) | rev 2 (after) | evidence |
|---|---|---|---|
| **B1 (blocking)** the policy-denied arm passes unvalidated `arguments` to `record_invocation`; "policy first" extends it to *malformed* blocked calls, which main answered with `Input validation error` and did not record. Seat's probe: `provision {"server_name": 12345, "run_correlation_id": "ghp_…"}` → main: nothing recorded; rev 1: `denied` with `run_correlation_id="ghp_…"`; a URL anywhere → `source_reference_hash` of it | denied arm: `arguments=arguments, result=payload`, `gateway_tool=name` | the class, not the arm: all three `record_invocation` call sites read only `audited_name` / `audited_arguments` (§6); the denied arm records `arguments=None` (by construction), `result=None`, registry name or `None`. Every writer call site listed with a disposition in *Every path into the audit writer* (C2 — undeclared keys of an *allowed* call, pre-existing on main — fixed too) | seat's `leak.py` rerun on rev 2: all four cases `run= None seat= None src= None`; `test_an_ungated_call_records_nothing_the_caller_chose[×4]` and `test_a_gated_call_records_only_the_keys_its_schema_declares`: red on rev 1 (`6 failed, 31 passed` with E16's test), green on rev 2; mutants N1–N5, N8 killed |
| B1, reader claim: "`gateway.invoke` never reaches the denied arm while the audit is on" | argued by the seat | **verified in code** (the path from `--policy` to `is_gateway_tool_allowed`, and no other writer of `_policy`/`_project_policy`) and **run**: agent-harness `reduce_research_audit` @ `18a324a4` on a rev 2 stream with three nulled `denied` records → `ledger: success None [('success', 'verified')]` | *Every path into the audit writer* |
| B1, test blind spot: `test_policy_is_judged_before_the_gate` put its secret in `api_key`, a field `record_invocation` never reads | — | the new tests put caller values in exactly the fields it reads (`run_correlation_id`, `seat_correlation_id`, `evidence_label_digest`, `tool_id`, a public URL), shaped to pass its charset filters, and compare two calls differentially | test table |
| Adjacent (seat, non-blocking): `gateway_tool_digest = sha256(name)` digests a caller-chosen string for an unregistered name | unchanged from main | digest of the registry name, or of `None`; and the denied payload (which echoes the name) is no longer digested | differential test, `unregistered` case; mutants N2, N4 |
| X1 dead-sink branch could log `{e}` (the message echoes the value) — survived | no log assertions | `test_rejected_and_denied_calls_never_log_argument_values` (`caplog`, `DEBUG`, value-echoing rejection on a dead sink) | X1 KILLED |
| X2 caller key kept if `isidentifier()` — survived; every test key contained `-` | — | `caller_chosen_key_value`, `CallerChosenKey42` cases | X2 KILLED |
| X3 validator kept if `isidentifier()` — survived | only `sk-KEYWORD-SECRET` | `caller_chosen_keyword` case | X3 KILLED |
| X4 `gateway_tool` not bounded to the scoped set — survived; seat: harmless | — | harmless (the gate records only a registered, allowed tool, and under the scoped policy that is always one of the four) **and** killed cheaply by `test_the_rejection_names_only_a_scoped_gateway_tool`, since the bound is documented behaviour | X4 KILLED |
| X5 declared names = every key anywhere in the schema — survived; seat: harmless | — | harmless (every schema key is an author string) **and** killed by the `additionalProperties`-as-a-caller-key case, pinning §3's definition | X5 KILLED |
| X6 gate logs the arguments at `DEBUG` — survived | no log assertions | same `caplog` test | X6 KILLED |
| X7 caller key kept if `len < 16` | killed | killed | — |
| Q2: uncounted exit — a non-`ScopedAdvisorAuditError` inside `record_rejected_arguments` escapes unaudited | not in the E-list | E16: re-raised as `ScopedAdvisorAuditError(...) from None` → "channel failed", nothing written, sink live | `test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink`; N6, N7 killed |
| Nit: E4 "same code" hides that its input domain changed | — | E4 row states the new domain and what it records | §5 |
| Nit: E13 unverified | *Unverified* | read by rev 1 and the seat (`policy.py:410-423,524-534`); still not proven by test | §5 |
| Q1, Q3, Q4 | seat AGREEs (rejection record itself, reader, TOCTOU) | unchanged; Q3's reader run repeated on rev 2 | — |

## Rev 3 board findings — before/after

Board 3 on rev 3 (`6343ad8`): the native claude seat returned **PARTIALLY
AGREE**, no code defect. It confirmed `src/` unchanged, Y1/Y3/Y4/Y6/Y9 killed,
`34/34`, the ledger constant, the suite. Its fresh `zmutants.py` found four
non-equivalent survivors, each shown to leak a caller value under the mutation
by its `test_zprobe.py`. Its diagnosis: the differential only ever sent one
caller-key shape, always beside a declared key, never to an allowed tool that
declares nothing. The coordinator asked for the class fix — a systematic
generator — rather than four more hand-picked cases.

| finding | rev 3 (before) | rev 4 (after) | evidence |
|---|---|---|---|
| **Z2** success arm falls back to raw arguments when the filter leaves `{}` → `gateway.health` (allowed, declares nothing) records a caller correlation and URL hash | survived | killed by the generated sweep: every tool, including the ones declaring nothing, gets every generated key | Z2 KILLED (8 sweep items) |
| **Z3** `_`-prefixed undeclared keys pass | survived | killed: `_` + every name is a generated spelling | Z3 KILLED (52) |
| **Z4** dict-valued undeclared keys pass | survived | killed: `dict` and `list` nesting a URL are generated shapes | Z4 KILLED (52) |
| **Z6** case-insensitive key match | survived | killed: upper- and title-case of every declared and read name are generated spellings | Z6 KILLED (46) |
| Z1 filter only on `gateway.invoke` | killed | killed (also by the sweep alone) | — |
| Z5 unregistered blocked name dispatched | killed | killed (not a leak: a status change; the sweep does not claim it) | — |
| Nit: `_LEDGER_READ_FIELDS` comment said "every record field … reads" | imprecise | comment states it is the fields read from an invoke record or checked on every record; the three read from `audit.completed` only are `_COMPLETION_RECORD_FIELDS`, asserted on the e2e completion record | test patch |
| Nit: `ledger_fields.py` matched only a receiver named `record` | fragile | taint analysis over `reduce_research_audit` and its transitive module callees: any name bound from `json.loads`, from a tainted name, or from a function returning tainted data; `.get("…")` and `["…"]` on it, and `helper(tainted, "…")` into a function that does `p.get(k)` / `p[k]`. Self-tested: renamed receiver + subscript → `equal: True`; read moved into a helper → `equal: True`; a read deleted → `equal: False` | `ledger_fields.py` output |
| **Found by the sweep, pre-existing:** an undeclared `meta` on a scoped `gateway.invoke` reaches `InvokeInput` by alias; a non-dict value fails pydantic, and `call_tool` logs `Tool execution error: …` with the value | — | not changed (Consiliency/pmcp#297 names exactly this: "A non-dict `meta` echoes its value", on main). The sweep's log oracle excludes exactly that line shape — prefix `Tool execution error: ` **and** `validation error for InvokeInput` (or `Unknown tool:`) — and nothing else | `meta_log_probe.py`: `logged: True \| audit: False` on main and on the patch |

## Rev 2 board findings — before/after

Board 2 on rev 2 (`2911ff5`): the native claude seat (claude-opus-5-5,
correctness) returned **PARTIALLY AGREE**. It found no code defect: B1,
X1–X7, Q2/E16, the E4 nit and E13 were verified closed, `29/29` of the plan's
mutants were reproduced killed, and the suite reproduced exactly. It found
**four non-equivalent mutants that survived** the tests, each with a
demonstrated consequence, plus nits. Rev 3 changes tests and plan text only;
`src/` is unchanged from rev 2.

| finding | rev 2 (before) | rev 3 (after) | evidence |
|---|---|---|---|
| **Y1** filter drops `evidence_label_digest` → the reducer returns `('failed', 'audit_correlation_mismatch')` for every research seat | survived: the end-to-end test asserted `run`/`seat` but not the evidence digest | `_LEDGER_READ_FIELDS` — every field the reducer reads, **derived from `research.py`** by `ledger_fields.py` (AST over its `record.get(...)` calls; `equal: True` against the test's constant @ `18a324a4`, whose `research.py` is byte-identical to `~/code/agent-harness`) — asserted non-null on the e2e invoke record, plus `evidence_label_digest == "a" * 64` | Y1 KILLED |
| **Y3** filter drops `invoke.arguments` → source hash lost when the result carries no URL (`('failed', None, unverified)`) | survived: the e2e mocked result itself contains a URL | `test_an_invoke_source_hash_comes_from_its_declared_arguments`: URL only in `arguments`, hash asserted equal to its sha256 | Y3 KILLED |
| **Y4** filter applied *before* the gate → a blocked tool's declared key is read (`provision {"server_name": <URL>}` → `denied src=…`), reopening B1 through a declared key | survived: the blocked cases put the URL in undeclared keys only | blocked differential cases carry a per-call URL in `server_name` (list when malformed, string when well-formed) | Y4 KILLED |
| **Y9** `except` arm records full gate-passed arguments → a registered, allowed tool's error path records an undeclared caller correlation and URL | survived: C3 was pinned only for the unregistered name, where `audited_arguments` is `None` anyway | `test_an_allowed_call_that_raises_records_only_declared_keys` (differential over a raising `describe`) | Y9 KILLED |
| **Y6** E16 catches only `ValueError` | survived: the test raised only `ValueError` | E16 test parametrized over `ValueError`, `KeyError`, `TypeError` (the latter two from `absolute_path`, since the walk tolerates them on a lookup) | Y6 KILLED |
| Y2, Y7, Y8 | killed | killed | — |
| Y5, Y10–Y13 | survived; the seat judged them equivalent or harmless | agreed, not added: Y10/Y11 pass `name` where it equals `tool.name` (registered path); Y12's walk never misses on a real error path; Y13's denied result digest is a constant either way; Y5's nested names collide with no field the record reads | — |
| Nit: CHANGELOG #236 entry says "see the #296 entry **below**"; it is above | wrong | "above" | CHANGELOG patch |
| Note: `source_reference_hash` from declared free text on non-invoke tools (`catalog_search.query`, `filters.tags`) — same as main | §6 claimed the URL scan's purpose generally | §6 now scopes that claim to `gateway.invoke`; *Non-goals* records the follow-up (restrict correlation/`tool_id`/URL reads to `gateway.invoke`) | — |
| Note: `meta` (alias name) no longer scanned for a URL; `_meta` still is | unstated | *Non-goals*, harmless to the ledger | — |

The five added mutants, from the rev 3 run (full log in *Mutation evidence*):

```text
Y1 declared filter drops evidence_label_digest: KILLED | 2 failed, 39 passed in 2.88s | test_an_invoke_source_hash_comes_from_its_declared_arguments, test_scoped_server_filters_controls_and_writes_private_complete_audit
Y3 declared filter drops invoke's arguments: KILLED | 1 failed, 40 passed in 2.87s | test_an_invoke_source_hash_comes_from_its_declared_arguments
Y4 declared filter applied before the gate: KILLED | 2 failed, 39 passed in 3.03s | test_an_ungated_call_records_nothing_the_caller_chose
Y6 E16 catches only ValueError: KILLED | 2 failed, 39 passed in 2.96s | test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink
Y9 except arm records full gate-passed arguments: KILLED | 1 failed, 40 passed in 2.91s | test_an_allowed_call_that_raises_records_only_declared_keys
```

## Research summary (all measured this session)

### The gate and the audit on `main` (`959d4d4`)

`_handle_call_tool` (main, line numbers of `src/pmcp/server.py`):

| line | step |
|---|---|
| `:293-294` | `name = params.name`; `arguments = params.arguments or {}` |
| `:296` | `tool = self._find_gateway_tool(name)` (registry lookup; `None` for an unregistered name) |
| `:297-308` | **gate**: if `tool is not None`, `jsonschema.validate`; on `ValidationError` → `return CallToolResult(is_error=True, …e.message)` — **no audit, no `_require_scoped_audit`, no policy check** |
| `:310` | `async def call_tool(...)`: everything below is inside its `try` |
| `:313` | `self._require_scoped_audit()` |
| `:315-327` | `if not self._policy_manager.is_gateway_tool_allowed(name)` → record `denied` → return |
| `:329-336` | `if tool is None: raise ValueError("Unknown tool")` (X1 guard, #236) |
| `:338-343` | scoped `gateway.invoke` correlation check (`InvokeInput.model_validate`) |
| `:345-399` | dispatch chain |
| `:405-419` | record `success` / `failure` / `denied` → return |
| `:423-434` | `except ScopedAdvisorAuditError` → return "channel failed" (unrecorded) |
| `:436-469` | `except Exception` → record `failure`/`denied` → return; if that record raises `ScopedAdvisorAuditError` → return "channel failed" |
| `:470-471` | `content = await call_tool(...)`; `return CallToolResult(content=content)` |

The audit is only ever live under the exact scoped policy:
`PolicyManager.activate_scoped_advisor` (`policy/policy.py:597-601`) raises
unless `is_scoped_advisor_policy()`, and `GatewayServer.__init__` calls it
whenever `audit_jsonl` is set. That policy allows exactly
`gateway.health`, `gateway.catalog_search`, `gateway.describe`,
`gateway.invoke` (`README.md:1511-1513`) — the same four names as
`_SCOPED_GATEWAY_TOOLS` (`scoped_advisor_audit.py:20-25`).

### What `record_invocation` would leak for an unvalidated payload

`ScopedAdvisorAudit.record_invocation` (`scoped_advisor_audit.py:186-228`)
derives these fields from `arguments` and `result`:

| field | source | why unusable for a rejected payload |
|---|---|---|
| `run_correlation_id`, `seat_correlation_id` | `arguments[...]` if it matches `^[A-Za-z0-9._:-]{1,128}$` | most API keys match this charset; on a rejected call nothing else vouches for the field |
| `downstream_tool_id` | `arguments["tool_id"]` if it matches `_TOOL_ID_PATTERN` | caller value |
| `evidence_label_digest` | `arguments[...]` if 64 hex | caller value |
| `source_reference_hash` | sha256 of the first public URL in `result` or **`arguments`** | a hash of a caller URL |
| `redacted_result_digest` | `sha256(json(result))` | passing the error or its message as `result` would publish a dictionary-attackable hash of a low-entropy secret |

So the #236 plan's "likely shape" (`:1106-1112`: `failure`/`denied`,
`result={"error_type": …}`, "arguments passed through the audit's existing
filtering") is **overridden**: the existing filtering is a *charset* filter,
not a provenance filter, and on a payload the gate just rejected no field is
vouched for. The gate record is built by a dedicated method from three inputs.

### Every path into the audit writer, and what the caller controls there (rev 2)

`ScopedAdvisorAudit._write` has four producers (`grep -n "self._write("
src/pmcp/scoped_advisor_audit.py`), and `record_invocation` has exactly one
caller, `GatewayServer._record_scoped_invocation`, which has three call sites
(`grep -rn "_record_scoped_invocation\|record_invocation\|record_rejected"
src/`). Line numbers are `wip/296-code` @ `e10304a`.

| # | writer / call site | caller-controlled input reaching it | main / rev 1 | disposition in rev 2 |
|---|---|---|---|---|
| W1 | `__init__` → `audit.started` | none (writer state) | — | unchanged |
| W2 | `complete()` → `audit.completed` | none (writer state) | — | unchanged |
| W3 | `record_rejected_arguments` (gate, `server.py:315-337`) | the error's path and keyword, `arguments` for container types | new in rev 1 | path/keyword redaction as in §3; any exception while describing now fails closed (E16) |
| C1 | `record_invocation` from the policy-**denied** arm (`server.py:356-371`) | **all of `arguments`, unvalidated** (correlations, `tool_id`, digest, any URL → `source_reference_hash`), the caller's `name` (→ `gateway_tool_digest`, and → `redacted_result_digest` via the payload's message) | main: well-formed blocked calls and unregistered names leak; rev 1 widened it to malformed blocked calls (**B1**) | `arguments=audited_arguments`, which is `None` on this arm by construction; `gateway_tool=audited_name` (registry name or `None`); `result=None` |
| C2 | `record_invocation` after dispatch (`server.py:457-462`) | gate-passed `arguments` — but the gate ignores undeclared keys until piece B, so e.g. `gateway.describe {"tool_id": …, "run_correlation_id": <secret>, "x": <URL>}` passes the gate and is recorded (on main too) | pre-existing | `arguments=audited_arguments` = only the top-level keys `tool.input_schema["properties"]` declares; `gateway.invoke` declares every field `record_invocation` reads, so invoke records are unchanged |
| C3 | `record_invocation` from `except Exception` (`server.py:488-493`) | as C2 for a registered, allowed tool; **unvalidated** `arguments` for an allowed *unregistered* name (`raise Unknown tool`) — unreachable under the scoped policy, whose allowlist is four registered, glob-free names | pre-existing | `audited_arguments` (so `None` for the unregistered name); `gateway_tool=audited_name`; `result={"error_type": type(e).__name__}` is a class name, not a caller value |
| — | `result` on C2 (handler output) → `redacted_result_digest`, `source_reference_hash` | only what a handler returns | by design | unchanged: the digest of the result and the hashed public source *are* the channel's documented purpose (README *Scoped advisor research*) |
| — | declared correlation fields of a well-formed `gateway.invoke` | the caller's own run/seat IDs and evidence digest, pattern- and length-checked by the gate | by design | unchanged: they are what the ledger compares against its config; a caller choosing to put a secret in its own correlation ID is not a gateway leak |

The reader-safety argument for C1 (verified in code, as the seat argued):
`GatewayServer.__init__` creates the audit only with `audit_jsonl`, and then
calls `activate_scoped_advisor` (`policy.py:597-601`), which raises unless
`is_scoped_advisor_policy()`; that returns `False` unless `_explicit_policy`
(`policy.py:562`), and with an explicit path `PolicyManager.__init__` never
calls `_discover_policies` (`policy.py:203-208`). `_discover_policies` →
`_load_project_policy` is the only writer of `_project_policy` and
`__init__` its only caller (`grep -rn "_discover_policies\|_load_project_policy"
src/pmcp`), and nothing reassigns `_policy` after `__init__`. So while the
audit is live, `is_gateway_tool_allowed(name)` is exactly
`fnmatchcase(name, p)` over an allowlist that must equal the four scoped names
(`policy.py:564-565`), none of which contains a glob character: **`gateway.invoke`
is always allowed and never reaches C1**. The one reader skips every record
that is not `audit.invocation` for `gateway.invoke` (`research.py:465-469`), so
nulling C1's fields and filtering C2 on non-invoke tools changes nothing it
reads. Measured, not just read: `reader.py` (below) drives the rev 2 gateway
through a correlated `invoke`, three rejections, three denied calls and a
`describe` with undeclared correlation keys, then feeds the stream to
agent-harness's `reduce_research_audit` from the pinned install
(`~/.local/share/agent-harness` @ `18a324a4`):

```text
1 audit.started None None None None
2 audit.invocation gateway.invoke success None run-1
3 audit.rejection gateway.invoke invalid_arguments ['arguments'] None
4 audit.rejection gateway.describe invalid_arguments ['tool_id'] None
5 audit.rejection gateway.invoke invalid_arguments [] None
6 audit.invocation None denied None None
7 audit.invocation None denied None None
8 audit.invocation None denied None None
9 audit.invocation gateway.describe failure None None
10 audit.completed None None None None
ledger: success None [('success', 'verified')]
```

(columns: sequence, event, `gateway_tool`, `terminal_status`,
`rejected_argument_path`, `run_correlation_id`; record 9 is `failure` because
the probe has no downstream servers, and its undeclared `run-1` is not read.)

### The downstream reader: agent-harness's advisor-board research ledger

Read at `~/code/agent-harness` @ `b9627d53`
(`phase-loop-runtime/src/phase_loop_runtime/advisor_board/research.py`; the
pinned install `~/.local/share/agent-harness` @ `18a324a4` has the same
`scoped_advisor_audit.v1` requirement). It is the only code in either tree
that parses the audit (`grep -rln "audit.invocation\|scoped_advisor_audit"
--include='*.py'`: `research.py`, `schema.py` (capability string only),
`runner.py` (unrelated "audit" hits), and tests).

- `_validated_records` (`:403-442`): contiguous `sequence`, one
  `audit.started` first and one `audit.completed` last with
  `record_count == len(records)`, **one `policy_digest` and one
  `audit_session_id` across every record**. It does not enumerate `event`.
- The ledger loop (`:465-511`):

  ```python
  if (
      record.get("event") != "audit.invocation"
      or record.get("gateway_tool") != "gateway.invoke"
  ):
      continue
  ...
  correlated = (
      record.get("run_correlation_id") == config.run_correlation_id
      and record.get("seat_correlation_id") == config.seat_correlation_id
      and evidence_digest == config.evidence_label_digest
  )
  correlation_mismatch = correlation_mismatch or not correlated
  ...
  if correlation_mismatch:
      status = "failed"
  ```

So a rejection written as an `audit.invocation` for `gateway.invoke` with
`null` correlations would set `correlation_mismatch` and fail the seat's
entire research ledger — overriding every verified invocation — with a
misleading `audit_correlation_mismatch`. Carrying the correlations is ruled
out (they are unvalidated argument values). **So the rejection is its own
event.** The reader skips it by its first test, and its envelope
(`sequence`, `audit_session_id`, `policy_digest`) satisfies
`_validated_records`. The capability string stays `.v1`: no existing record
changes shape or meaning, and the reader dispatches on `event`. Mutant M13
(event renamed back to `audit.invocation`) is killed by the assertion that
rejected calls write **zero** `audit.invocation` records.

### Probe: which rejections reach the audit (`probe_gate_audit.py`, below)

Real `GatewayServer` with the scoped policy and a real audit file;
`provision` is outside that policy, so it is blocked. Pre-A is `1fb36f2`
(`cdaf3c6^`), main is `959d4d4`, patch is `wip/296-code` (rev 1 @ `9ced94f`;
rerun on rev 2 @ `e10304a`, the patch section's lines are identical — this probe
prints only event, status and the `rejected_*` fields, and rev 2 changes none of
those).

```text
== pre-A @ 1fb36f2
allowed, schema-malformed                gateway.describe   is_error=False text='{"error": true, "message": "1 validation error for DescribeI'
                                         audit=['audit.invocation:failure'] extra=[{}]
allowed, type error echoing a secret     gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-1' is not of type 'object"
                                         audit=[] extra=[]
allowed, pattern error echoing a secret  gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-2' does not match '^[0-9a"
                                         audit=[] extra=[]
blocked, schema-malformed                gateway.provision  is_error=True  text="Input validation error: 12345 is not of type 'string'"
                                         audit=[] extra=[]
blocked, model-only-malformed on pre-A   gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
blocked, well-formed                     gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
unregistered name                        gateway.nope       is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
SECRET in audit: False
dead sink, malformed describe: False {"error": true, "message": "Scoped advisor audit channel failed"}
== main @ 959d4d4 (src)
allowed, schema-malformed                gateway.describe   is_error=True  text="Input validation error: '' should be non-empty"
                                         audit=[] extra=[]
allowed, type error echoing a secret     gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-1' is not of type 'object"
                                         audit=[] extra=[]
allowed, pattern error echoing a secret  gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-2' is too short"
                                         audit=[] extra=[]
blocked, schema-malformed                gateway.provision  is_error=True  text="Input validation error: 12345 is not of type 'string'"
                                         audit=[] extra=[]
blocked, model-only-malformed on pre-A   gateway.provision  is_error=True  text="Input validation error: '' should be non-empty"
                                         audit=[] extra=[]
blocked, well-formed                     gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
unregistered name                        gateway.nope       is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
SECRET in audit: False
dead sink, malformed describe: True Input validation error: '' should be non-empty
== patch @ wip/296-code
allowed, schema-malformed                gateway.describe   is_error=True  text="Input validation error: '' should be non-empty"
                                         audit=['audit.rejection:invalid_arguments'] extra=[{'rejected_argument_path': ['tool_id'], 'rejected_argument_validator': 'minLength'}]
allowed, type error echoing a secret     gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-1' is not of type 'object"
                                         audit=['audit.rejection:invalid_arguments'] extra=[{'rejected_argument_path': ['arguments'], 'rejected_argument_validator': 'type'}]
allowed, pattern error echoing a secret  gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-2' is too short"
                                         audit=['audit.rejection:invalid_arguments'] extra=[{'rejected_argument_path': ['evidence_label_digest'], 'rejected_argument_validator': 'minLength'}]
blocked, schema-malformed                gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
blocked, model-only-malformed on pre-A   gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
blocked, well-formed                     gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
unregistered name                        gateway.nope       is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
SECRET in audit: False
dead sink, malformed describe: False {"error": true, "message": "Scoped advisor audit channel failed"}
```

Read off the probe:

- **Pre-existing on pre-A and main:** a type error (`'sk-SECRET-1' is not of
  type 'object'`) and a pattern/length error on `evidence_label_digest` are
  never audited, and the message echoes the value.
- **Piece A moved** `describe {"tool_id": ""}` from `failure` (pre-A, raised
  by the model inside `call_tool`) to unaudited.
- **The blocked-tool premise, measured precisely.** On pre-A a malformed call
  to a blocked tool was `denied` **only** when the hand-written schema passed
  it (`provision {"server_name": ""}`); one the hand-written schema already
  rejected (`{"server_name": 12345}`) was unaudited and got `Input
  validation error`, disclosing the blocked tool's schema. On main (after A)
  both are unaudited. So "preserve `denied`" means adopting the rule pre-A
  had by accident and applying it uniformly: this plan records **both** as
  `denied` — a superset of pre-A.
- **Dead sink.** Pre-A, the `""` case (reached `call_tool`) got "Scoped
  advisor audit channel failed". On main it gets `Input validation error`:
  a malformed call is answered even though the audit can no longer record
  it. The patch restores fail-closed behaviour for every gate rejection.

### Path segments: where instance data can enter `error.absolute_path`

jsonschema 4.25.1 (`uv run python -c "import importlib.metadata as m;
print(m.version('jsonschema'))"`; mcp 2.0.0). `absolute_path` is built from
the `path=` of each `descend()`: `properties` descends with the **declared**
name; `items`/`prefixItems`/`contains` with an **index**;
`additionalProperties` and `patternProperties` with the **caller's key**.
`propertyNames` descends with no path (the key is the error's `instance`,
which the record never reads). An `additionalProperties: false` error sits at
the *parent* object's path and names the extra key only in `message`.

Survey of the 26 advertised schemas on main (`survey.py`, below, plus a
`json.dumps` scan for the combinators — last line):

```text
26
gateway.catalog_search properties.filters.properties.tags items {"type": "string"}
gateway.invoke properties.arguments additionalProperties true
gateway.invoke properties.task.properties.metadata additionalProperties true
gateway.invoke properties.task.properties.requestor_context additionalProperties true
gateway.invoke properties._meta additionalProperties true
gateway.set_startup_policy properties.names items {"type": "string"}
gateway.request_capability properties.available_clis items {"type": "string"}
gateway.sync_environment properties.detected_clis items {"type": "string"}
gateway.tasks_list properties.requestor_context additionalProperties true
gateway.tasks_get properties.requestor_context additionalProperties true
gateway.tasks_result properties.requestor_context additionalProperties true
gateway.tasks_cancel properties.requestor_context additionalProperties true
gateway.register_discovered_server properties.env_vars items {"type": "string"}
anyOf/oneOf/allOf/$schema hits: []
```

No schema uses `patternProperties`, `$ref`, `anyOf`/`oneOf`/`allOf`, or has a
`$schema` (so `validator_for` picks Draft 2020-12). Caller-keyed descent exists only
through `additionalProperties: true` (`invoke.arguments`, `invoke._meta`,
`invoke.task.metadata`, `invoke.task.requestor_context`,
`tasks_*.requestor_context`), which can never fail. `items` appears on five
string arrays. **So on `main` the caller-key class is empty, but only by the
current schemas' shape** — any future `dict[str, int]` field
(`additionalProperties: {"type": "integer"}`) fills it. The patch redacts it
by construction and the tests exercise it with a synthetic schema.

`error.path` vs `error.absolute_path`: `best_match` can return an `anyOf`
child from `context`, whose `.path` is relative. Measured with
`{"a": {"anyOf": [{"properties": {"b": {"type": "string"}}}, {"type":
"integer"}]}}` on `{"a": {"b": 1}}`: `validator='type'`, `path=['b']`,
`absolute_path=['a', 'b']`.

### Deep nesting: an exception the gate does not catch

In-process, `jsonschema.validate` on a `tool_id` that is a 100,000-deep
nested list raises **`RecursionError`** (the error message `repr`s the
instance), which escapes `_handle_call_tool` unaudited on main and with the
patch. On the wire it cannot arrive: the SDK parses stdio frames with
`types.jsonrpc_message_adapter.validate_json` (`mcp/server/stdio.py:189`),
and measured with that adapter a nesting depth of 150 parses (and the gate
returns `ValidationError`), while 200, 254, 255, 1000 and 5000 are refused
before the handler: `Invalid JSON: recursion limit exceeded`. Out of scope
(see *Non-goals*).

## Design

### 1. `event = "audit.rejection"`, `terminal_status = "invalid_arguments"`

Two module constants in `scoped_advisor_audit.py`: `REJECTION_EVENT` and
`INVALID_ARGUMENTS_STATUS`. The event is new for the reason in *The
downstream reader*: nothing was invoked, and the record cannot carry the
correlations an invocation record is judged by. The status name, for an
operator reading the stream, is not `failure`: in the current vocabulary
`failure` means the call entered `call_tool` and either a handler returned
`ok: false` or an audited check raised (`server.py:405-412`, `:436-450`) —
a consumer counting `failure` as "dispatch began" would be wrong. Not
`denied`: that is policy (`:322`, `:410`, `:440-443`). A pydantic
*model-only* rejection inside a handler (a gate/model disagreement, see the
#236 plan's differential) still records `failure`; that is correct — it did
reach the handler — and is the same as today.

### 2. Policy first, read once

```python
allowed = self._policy_manager.is_gateway_tool_allowed(name)
if tool is not None and allowed:
    ...gate...
```

and in `call_tool`, `if not allowed:` replaces the second
`is_gateway_tool_allowed(name)` call. A blocked name skips the gate and is
recorded `denied` by the existing arm. That arm's code is not unchanged in
rev 2, and its *input domain* did change in rev 1: it now receives payloads
the gate would have rejected, so it must not read them at all (§6, board
finding B1). Reasons:

- The verdict does not depend on arguments, so a blocked tool's attempt is
  `denied` whatever its shape (restores and generalises pre-A; probe above).
- A blocked tool's schema is no longer disclosed through `Input validation
  error` messages to a caller that may not call it.
- **Read once, not twice.** With two reads, a predicate that answered
  "blocked" at the gate and "allowed" in `call_tool` would dispatch
  *ungated* arguments to a handler — exactly what X1 of #236 exists to
  prevent. Policy is loaded once at start-up today (`PolicyManager` has no
  reload path), so this is structural rather than a live race, and it is
  pinned by a flipping-stub test (mutant M2).
- The unregistered-name path is unchanged: `tool is None` skips the gate;
  `call_tool` then records `denied` (blocked) or raises `Unknown tool`
  inside the audited path (X1 guard, main `:329-336`, untouched). Since
  rev 2 either record carries nothing the caller chose — neither its
  arguments nor its name (§6).

### 3. `record_rejected_arguments` — provenance of every key

A new method on `ScopedAdvisorAudit`, called only from the gate. Inputs:
`gateway_tool` (the **registry's** `tool.name`, not the caller's string —
equal, since `_find_gateway_tool` matched it), the `ValidationError`, the
tool's schema, and `arguments` (read for container *types* only). It reads
exactly two attributes of the error: `absolute_path` and `validator`.

| key | value | provenance |
|---|---|---|
| `event` | `"audit.rejection"` | constant |
| `sequence`, `schema`, `audit_session_id`, `timestamp`, `policy_digest` | as every record | writer state |
| `gateway_tool` | `tool.name` if in `_SCOPED_GATEWAY_TOOLS`, else `null` | registry; under the only policy that activates the audit, always one of the four, so always named |
| `gateway_tool_digest` | `sha256(tool.name)` | registry |
| `terminal_status` | `"invalid_arguments"` | constant |
| `rejected_argument_path` | list of `str` / `int` / `null` | see below |
| `rejected_argument_validator` | `error.validator` if it is a `str` key of `validator_for(schema).VALIDATORS`, else `null` | the draft's keyword table |

Eleven keys, pinned exactly by `_REJECTION_RECORD_KEYS`. No correlation,
`downstream_tool_id`, `source_reference_hash`, `evidence_label_digest` or
`redacted_result_digest` — each is an argument value or a hash of one (see
*What `record_invocation` would leak*), and there is no result to digest.

**Path proof.** For each segment of `error.absolute_path`, walking
`arguments` alongside:

- `list` container and `type(segment) is int` → the index. A position, not a
  value.
- `dict` container and `type(segment) is str` (exactly `str`: a subclass
  could override `__eq__`/`__hash__` to pass the membership test while
  carrying a secret — in-process only, pinned by mutant M4b) and
  `segment in declared` → the segment. `declared` is every key of every `properties` map anywhere
  in the tool's schema, so the emitted string is equal to a string the
  schema's author wrote and discloses nothing the advertised schema does
  not. A caller key that happens to equal a declared name elsewhere (e.g.
  `env.tool_id`) is kept — equality with a public string is not a leak.
- anything else → `null`. This covers keys matched by
  `additionalProperties`/`patternProperties`, and an in-process `int` dict
  key (which a JSON object cannot carry, but `params.arguments` nested
  values are `Any`).

The walk reads `arguments` only through `isinstance(node, list|dict)` and
`node[segment]`; no value is copied out. **The key an `additionalProperties:
false` error names** (piece B) is only in `message` — the path is the parent
object — and `message` is never read. The record never reads `message`,
`instance`, `validator_value`, `schema`, `context`, `cause` or `json_path`;
a test swaps the error's class for one whose value-bearing attributes raise
on access, and the record is still written.

The client-facing response to an allowed tool's rejection is **byte
identical** to main (`Input validation error: {e.message}`). Its value echo
is Consiliency/pmcp#297's scope, not this issue's.

### 4. Dead sink at the gate

If the gate's record raises `ScopedAdvisorAuditError` (sink closed or
failed), return the same `{"error": true, "message": "Scoped advisor audit
channel failed"}` payload `call_tool` returns (`:423-434`), without
`is_error`, like it.

The same answer covers E16 (rev 2): if describing the rejection raises
anything else (reachable only in-process, e.g. a nested mapping whose
`__getitem__` raises `ValueError`), `record_rejected_arguments` re-raises it
as `ScopedAdvisorAuditError("scoped advisor audit could not describe a
rejected call") from None`. Nothing is written, the sink is not marked failed
(only `_write` sets `_failed`), so the next call is recorded normally; and
`from None` keeps the original — which may carry a caller value — out of the
cause and out of any formatted traceback.

### 5. Every exit of `_handle_call_tool`, before and after

Patched line numbers (`wip/296-code` @ `e10304a`). "Records" below means the
fields rev 2 lets through: `audited_name` and `audited_arguments` (§6).

| # | exit | main | patched |
|---|---|---|---|
| E1 | gate `ValidationError` on an **allowed** registered tool | return, **unaudited** | `:311-345`: `audit.rejection` / `invalid_arguments`, return the unchanged error |
| E2 | E1 with a dead sink | return validation error, unaudited | `:323-337`: return "channel failed" (unrecordable by definition) |
| E3 | gate on a **blocked** registered tool | E1 (unaudited, schema disclosed) | gate skipped; E4 |
| E4 | blocked name (`if not allowed`) | `:315-327` `denied`, with every argument-derived field read from the unvalidated payload | `:356-371` `denied`, single verdict. **Input domain changed** (rev 1): it now also receives payloads the gate would reject. **Records** (rev 2): no argument-derived field (`audited_arguments` is `None`), `gateway_tool_digest` of the registry name or of nothing, `redacted_result_digest` of `None` instead of the name-echoing payload |
| E5 | unregistered name, allowed | `raise Unknown tool` → `failure`, recording the unvalidated payload | `:373-380` raise → E9, now recording no argument (`audited_arguments` is `None`) and no caller name; unreachable under the scoped policy |
| E6 | scoped `invoke` without correlations | raise → `failure` | unchanged path (`:382-387`); records declared keys only (all of invoke's) |
| E7 | dispatch returns | `success`/`failure`/`denied` | `:457-462`; records declared keys only (§6) |
| E8 | `except ScopedAdvisorAuditError` (from `_require_scoped_audit` or a record) | "channel failed", unrecordable | unchanged (`:466-477`) |
| E9 | `except Exception` → record → return | recorded | `:479-513`; records `audited_name` / `audited_arguments` |
| E10 | E9 whose record raises `ScopedAdvisorAuditError` | "channel failed", unrecordable | unchanged |
| E11 | exception escaping the handler: `jsonschema.SchemaError` | — | unchanged; unreachable, every advertised schema passes `Draft202012Validator.check_schema` (`tests/test_gateway_tool_schemas.py:402`) |
| E12 | exception escaping: `RecursionError` from `validate` | in-process only | unchanged; the wire caps nesting below 200 (measured above) — *Non-goals* |
| E13 | exception escaping: `is_gateway_tool_allowed` | inside `call_tool`'s `try` (would be `failure`) | now evaluated before `call_tool`; it is `_section_allows` → `fnmatchcase` over lists loaded at start-up (`policy.py:410-423,524-534`) and does not raise on a `str` (read by rev 1 and by the board seat; not proven by test) |
| E14 | `CancelledError` / `BaseException` | propagates | unchanged |
| E15 | SDK rejection of `CallToolRequestParams` before the handler (e.g. `arguments` not an object) | never reaches `_handle_call_tool` | unchanged — *Non-goals* |
| E16 | (rev 2; board Q2) a non-`ScopedAdvisorAuditError` raised **inside** `record_rejected_arguments` while describing the rejection — in-process only, e.g. a nested mapping whose `__getitem__` raises `ValueError` (wire JSON cannot build one) | n/a (no gate record on main); in rev 1 it escaped `_handle_call_tool` unaudited | re-raised as `ScopedAdvisorAuditError(...) from None` (`scoped_advisor_audit.py:329-335`) → the gate's E2 arm: "channel failed", nothing written, sink stays live; pinned by `test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink` (mutants N6, N7) |

### 6. (rev 2) Only gated, declared arguments reach any record

Board finding B1 is one instance of a class: *a field of an audit record
derived from input nothing vouched for*. The class has three members on the
invocation path (C1–C3 in *Every path into the audit writer*), so the fix is
one rule applied at the one place all three share — `_handle_call_tool`,
before `call_tool` — rather than a patch to the denied arm:

```python
audited_name = tool.name if tool is not None else None
audited_arguments: dict[str, Any] | None = None
if tool is not None and allowed:
    ...gate (returns on ValidationError)...
    declared = tool.input_schema.get("properties") or {}
    audited_arguments = {
        key: value for key, value in arguments.items() if key in declared
    }
```

`audited_arguments` is assigned only on the line after the gate passed, so it
is `None` for a blocked or unregistered call by construction, and every
`_record_scoped_invocation` call site passes it and `audited_name` (C1, C2,
C3). The denied arm also passes `result=None`: its payload's message
(`f"Gateway tool blocked by policy: {name}"`) is the caller's string, and its
digest was another channel for it. `record_invocation` and
`_record_scoped_invocation` accept `gateway_tool: str | None` (`_digest(None)`
is the digest of `null`, a constant). The response to every call is
unchanged; `call_tool` still dispatches the full `arguments`.

Why top-level declared keys and not the full schema walk: every field
`record_invocation` reads is a top-level key (`tool_id`, the two correlations,
`evidence_label_digest`) or the public-URL scan; the scan over a declared
value (e.g. `invoke.arguments`, `additionalProperties: true`) is where the
intended `source_reference_hash` comes from, so it must keep working — for
`gateway.invoke`, the only tool the ledger reads. (Rev 3, per the rev 2 seat:
on other tools the same scan still hashes a URL in a *declared* free-text
field, e.g. `catalog_search.query`; unchanged from main, recorded under
*Non-goals* as a follow-up.) What the ledger needs from the filter is pinned
by test, not argued: every field its reducer reads survives on an invoke
record (Y1), and the source hash is taken from `invoke.arguments` when the
result has no URL (Y3). When
piece B makes the gate reject undeclared keys, the filter becomes a no-op for
every payload that reaches it — it is not a substitute for B, and B does not
make it wrong.

### 7. (rev 4) The generated sweep, and why its axes cover the class

The class this plan fixes is *a caller-chosen value, or anything derived from
one, reaching an audit record or a log through a record site*. Three board
rounds each found a mutant the hand-picked values missed, so rev 4 generates
them (`test_generated_caller_values_never_reach_{an_allowed,a_rejection,an_ungated}_record`,
134 items):

- **Tools and paths** — from the registry, not a list: every registered gateway
  tool (26) on each path of §5 that can write a record: allowed with the
  handler returning (E7) and raising (E9), gate-rejected (E1; skipped with an
  assertion only for the three tools that declare no property and so cannot
  be rejected until piece B), blocked (E4), and unregistered names blocked
  (E4) and allowed (E5). Handlers are a stub, so every tool reaches its record
  site without a downstream.
- **Key spelling** — generated from the grammar that decides what is read: the
  names `record_invocation` reads by name (`tool_id`, the two correlations,
  `evidence_label_digest`), the names the tool declares, the alias pair
  `meta`/`_meta`, and a caller key whose own name varies per call — each
  as is, `_`-prefixed, upper-case, title-case, with a Cyrillic look-alike
  letter, and with a zero-width suffix. On an allowed tool the declared names
  themselves are left to the baseline (the gate vouches for them); on ungated
  paths they are generated too. Unregistered *names* get the same axes.
- **Value shape** — generated from the filters `record_invocation` applies: a
  string in the correlation charset, a `tool::id`, a 64-hex digest, a public
  URL (the recursive scan), a dict and a list nesting a URL (the scan's
  recursion), and a non-string.
- **Position** — top level, and nested inside a declared key on ungated paths.
  On allowed tools, content nested in a declared key is gated and is the
  documented `source_reference_hash` channel (*Non-goals*), so it is the
  baseline, not a generated value.

**Oracle, which names no channel.** Two calls that differ only in the
generated content (tag `a` vs `b`) must leave equal records (modulo
`sequence`/`timestamp`); on an allowed tool, the record must also equal the
one for the declared arguments alone (except `terminal_status` and the result
digest, since an undeclared `meta` reaches `InvokeInput` by alias). On top, no
generated marker, URL, digest-shaped value, or sha256 of any of them appears in
the raw audit, and no marker appears in any log record (DEBUG, root; asserted
non-empty), bar the one pre-existing #297 line shape above.

**Measured, not argued: the sweep alone** (`mutants.py` with `-k
generated_caller_values`, below) kills every mutant in the class — M9, M10,
N1–N5, N8, X6, Y4, Y9, Z1–Z4, Z6 (plus M1, M7, M13) — `19/40`. The 21 it does
not kill are outside the class and killed by the targeted tests: status and
dispatch (M2, M11, Z5); fail-closed (M8, N6, N7, Y6, X1 on the dead-sink
path, which the sweep does not enter); retention the ledger needs (Y1, Y3 —
the sweep asserts absence, those assert presence); and the path- and
keyword-redaction family (M3–M6, M4b, M12, X2–X5, X7), which needs a
caller-keyed *failing* descent or a non-draft keyword that no registered
schema on `959d4d4` can produce (*Path segments* survey) — pinned by the
synthetic-schema tests.

**The oracle is a differential, not a field list.** For each ungated path the
test makes two calls that differ only in what the caller chose — every value
shaped to survive `record_invocation`'s charset filters, and for an
unregistered name the name itself — and asserts the two records are equal
except for `sequence` and `timestamp`. A channel nobody listed fails it too.

## Changes

### `src/pmcp/scoped_advisor_audit.py` (modify; patch under *Verbatim bodies*, +124 / −1)

- `import jsonschema`; `REJECTION_EVENT = "audit.rejection"`;
  `INVALID_ARGUMENTS_STATUS = "invalid_arguments"`.
- `_declared_property_names(schema) -> frozenset[str]`.
- `_rejected_argument_path(error, schema, arguments) -> list[str | int | None]`.
- `_rejected_argument_validator(error, schema) -> str | None`.
- `ScopedAdvisorAudit.record_rejected_arguments(*, gateway_tool, error, schema, arguments)`;
  (rev 2) any exception while describing the rejection is re-raised as
  `ScopedAdvisorAuditError(...) from None` (E16).
- (rev 2) `record_invocation(gateway_tool: str | None, ...)`; its body is
  unchanged.

### `src/pmcp/server.py` (modify; patch under *Verbatim bodies*, +53 / −10)

The gate becomes `if tool is not None and allowed:` with `allowed` read
once; the `except jsonschema.ValidationError` arm records the rejection
(or returns "channel failed"); `call_tool`'s policy arm reads `allowed`.
(rev 2) `audited_name` / `audited_arguments` (§6) feed all three
`_record_scoped_invocation` call sites; the denied arm records `result=None`;
`_record_scoped_invocation(gateway_tool: str | None, ...)`.

### `tests/test_scoped_advisor_audit.py` (modify; patch under *Verbatim bodies*, +1051 / −0)

Adds `import hashlib`, `import jsonschema`, `import logging`, `import
traceback`, the constant `_LEDGER_READ_FIELDS` (with two assertions in the
existing `test_scoped_server_filters_controls_and_writes_private_complete_audit`,
rev 3), `_COMPLETION_RECORD_FIELDS` (rev 4), and 163 test items (bodies in
the patch): 29 targeted items and the 134-item generated sweep (§7). Rev 1's thirteen, with rev 2's added cases
marked:

| test | pins |
|---|---|
| `test_gate_rejections_are_audited_without_argument_values` | five real gate rejections through the SDK handler (`type` on `arguments`, `pattern` on `evidence_label_digest`, `type` on `run_correlation_id`, nested `task.enabled`, `required`): **zero** `audit.invocation` records (the consumer-safety pin), five `audit.rejection` / `invalid_arguments`, exact `(path, keyword)` pairs, exact key set, secret substrings, correlations and message fragments absent, nothing dispatched, response unchanged |
| `test_policy_is_judged_before_the_gate` | `provision {"server_name": 12345}` → "blocked by policy", one `denied`, no `Input validation error` |
| `test_the_policy_verdict_is_read_once_per_call` | a stub answering blocked-then-allowed; the malformed `describe` is denied and never dispatched |
| `test_gate_rejection_with_a_dead_sink_fails_closed` | closed sink → "channel failed" payload |
| `test_rejected_argument_path_redacts_caller_chosen_keys[×10]` | `additionalProperties` key → `null`; **(rev 2)** identifier-shaped caller keys `caller_chosen_key_value` and `CallerChosenKey42` → `null` (X2); **(rev 2)** a caller key equal to a schema *keyword* (`additionalProperties`) → `null` (X5); `patternProperties` key → `null`; caller key equal to a declared name kept; in-process `int` key → `null`; a `str` subclass whose `__eq__` matches every declared name → `null`; array index kept; `anyOf` child → absolute path |
| `test_an_unknown_validator_keyword_is_not_recorded[×2]` | a keyword outside the draft's table → `null`; **(rev 2)** also the identifier-shaped `caller_chosen_keyword` (X3) |
| `test_the_rejection_record_never_reads_the_message_or_the_instance` | `_PoisonedError`: `message`, `instance`, `validator_value`, `context`, `cause`, `json_path`, `schema` raise on access; the record is still written with the exact key set |

Rev 2's new tests (B1, the class of B1, X1/X4/X6, E16):

| test | pins |
|---|---|
| `test_an_ungated_call_records_nothing_the_caller_chose[blocked-malformed, blocked-wellformed, unregistered, unregistered-allowed]` | **B1 and its class (C1, C3).** Two calls per path differing only in caller values — `run_correlation_id`/`seat_correlation_id` (`caller_chosen_run_value_a`…, which fit the correlation charset), a 64-hex `evidence_label_digest`, a `tool_id` fitting `_TOOL_ID_PATTERN`, a public URL — and, for unregistered names, the name: the two `denied` (or `failure`) records are equal modulo `sequence`/`timestamp`; the five argument-derived fields are `null`; no caller string in the raw audit. `unregistered-allowed` uses a policy stub that allows every name (E5) |
| `test_a_gated_call_records_only_the_keys_its_schema_declares` | **C2.** A well-formed `describe` carrying undeclared correlation-shaped keys and a URL: equal records across two calls, `downstream_tool_id` (declared) kept, the rest `null` |
| `test_rejected_and_denied_calls_never_log_argument_values` | **X1, X6.** `caplog` at `DEBUG` over E1, E2 (dead sink, value-echoing `type` error), and the three E4 variants: no argument value, no `is not of type`, no `Input validation error` in any log record; asserts something *was* logged |
| `test_the_rejection_names_only_a_scoped_gateway_tool` | **X4.** `record_rejected_arguments(gateway_tool="gateway.provision")` → `gateway_tool: null` |
| `test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink[ValueError, KeyError, TypeError]` | **E16.** `ValueError`: a nested mapping whose `__getitem__` raises; **(rev 3, Y6)** `KeyError` / `TypeError`: an error whose `absolute_path` raises it (the walk itself tolerates those two on a lookup, so they must come from elsewhere). Each: `ScopedAdvisorAuditError`, the caller string absent from `traceback.format_exception` of it, nothing written, `require_available()` passes, and the next rejection is recorded |

Rev 3's additions (the rev 2 seat's surviving mutants):

| test / assertion | pins |
|---|---|
| `_LEDGER_READ_FIELDS` + `test_scoped_server_filters_controls_and_writes_private_complete_audit` | **Y1.** Every field agent-harness's reducer reads is non-null on the end-to-end firecrawl `invoke` record, and `evidence_label_digest == "a" * 64`. The set is not written by hand from memory: `ledger_fields.py` (below) derives it from `research.py`'s `record.get(...)` calls and compares it with the test's constant — `equal: True` @ `18a324a4` |
| `test_an_invoke_source_hash_comes_from_its_declared_arguments` | **Y3 (and Y1).** The stubbed downstream result carries no URL; the record's `source_reference_hash` equals `sha256("https://source.example/article")`, the URL in the declared `invoke.arguments`; all ledger fields non-null |
| `test_an_ungated_call_records_nothing_the_caller_chose[blocked-*]` (changed) | **Y4.** The blocked cases' URL now also sits in `server_name`, the one key `provision` declares, varied per call — as a list (malformed) and as a string (well-formed) — so a filter run before the gate breaks the differential |
| `test_generated_caller_values_never_reach_an_allowed_record[26 tools × ok, handler-error]` | **(rev 4) the class, allowed paths (C2, C3; Z1–Z4, Z6).** §7 |
| `test_generated_caller_values_never_reach_a_rejection_record[26 tools]` | **(rev 4) the class, gate rejections (W3).** A declared key carries a tagged value the gate rejects, beside the generated keys; equal `audit.rejection` records, no `audit.invocation` |
| `test_generated_caller_values_never_reach_an_ungated_record[26 blocked tools + unregistered blocked/allowed × top-level, nested-in-declared]` | **(rev 4) the class, ungated paths (C1, C3; N1, N4, Y4).** All records equal across every generated payload and name; argument-derived fields `null` |
| `test_an_allowed_call_that_raises_records_only_declared_keys` | **Y9 (C3 for a registered tool).** A registered, allowed `describe` whose handler raises (E9): two calls differing only in undeclared correlation-shaped keys and a URL leave equal `failure` records; `downstream_tool_id` kept, the rest `null` |

Sample secrets are identifier-like strings that are not shaped like any
provider's token (`caller_chosen_*`, `CallerChosenKey42`), so GitHub push
protection has nothing to match in the tests or in this plan.

### Test bodies

Verbatim in the test patch (extract with the heading prefix
``### Patch — `test_scoped_advisor_audit``, under *Verbatim bodies*),
appended after `test_capability_probe_is_machine_readable` under the banners
`# --- gate rejections reach the audit (Consiliency/pmcp#296) ---` and
`# --- rev 2: nothing the caller chose reaches a denied record (Consiliency/pmcp#296) ---`,
with the helpers `_REJECTION_RECORD_KEYS`, `_scoped_server`, `_call`,
`_first_error`, `_record_one`, `_EqualsEverything`, `_CALLER_KEYED_SCHEMA`,
`_PoisonedError`, `_VOLATILE_KEYS`, `_caller_values`, `_stable`,
`_RaisingDict`, (rev 3) `_LEDGER_READ_FIELDS`, `_format_tag`,
`_raising_path_error`, and (rev 4) `_COMPLETION_RECORD_FIELDS`,
`_RECORD_READ_NAMES`, `_SHAPES`, `_MARKERS`, `_INVALID_INDEX`,
`_FOREIGN_LOG_PREFIX`, `_FOREIGN_LOG_CHANNELS`, `_gateway_tools_by_name`,
`_spellings`, `_generated_keys`, `_generated_value`, `_generated`,
`_forbidden_in_audit`, `_declared_baseline`, `_StubGatewayTools`,
`_invocations`, `_assert_nothing_generated_leaked`, `_is_foreign`,
`_log_text`, `_invalid_declared`, `_UNREGISTERED_NAME_PAIRS`. What each test pins is in the tables above; which mutant each
kills is in *Mutation evidence*.

### `CHANGELOG.md` (modify) — `[Unreleased]` → `### Fixed`

A new first entry under `### Fixed`, and the piece-A entry's "Scoped-audit
change until Consiliency/pmcp#296 lands" sentence is replaced by one
sentence pointing at it (both are unreleased, so the release notes must not
contradict each other; rev 3 corrects its pointer to the new entry, which
sits *above* it, not below). (rev 2) The new entry also states what denied and
undeclared-key invocation records no longer carry. Patch under *Verbatim
bodies*.

### `README.md` (modify, `:1517-1520`)

(rev 2: two sentences) the `audit.rejection` event, and that only gate-passed,
declared arguments are read into any record, in the *Scoped advisor research*
section.

## Verification

```bash
cd <fresh worktree of origin/main @ 876fd33>
uv sync --all-extras -p 3.10                    # fresh worktree: else pytest is the system one
# apply (see *Verbatim bodies → how to apply*)
uv run pytest tests/test_scoped_advisor_audit.py -p no:cacheprovider --cov-fail-under=0 -q
                                                # expect 175 passed (12 existing + 163 new)
uv run ruff check src/ tests/                   # expect "All checks passed!"
uv run ruff format --check src/ tests/          # expect "165 files already formatted" (163 on 959d4d4)
uv run mypy src/pmcp --exclude baml_client     # CI gate (test.yml:388); expect "no issues found in 51 source files" (50 on 959d4d4)
uv run mypy src/                                # same result on this tree
env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
  uv run pytest -m 'not live and not slow' -p no:cacheprovider -q
                                                # full suite; run as a background task that notifies on exit
python3 <scratch>/mutants.py <scratch>/mut     # from the worktree root; expect "40/40 killed"
python3 <scratch>/ledger_fields.py <agent-harness>/phase-loop-runtime/src/phase_loop_runtime/advisor_board/research.py \
  tests/test_scoped_advisor_audit.py            # expect "equal: True"
```

## Acceptance criteria — measured this session

Tree: `wip/296-code` @ `80d0f93` (rev 4 merged with main `876fd33`; this
plan's `src/` identical to rev 2's `e10304a`, its tests to rev 4's `6ab3db9`),
base `876fd33`; the gates marked *proof tree* ran in a fresh `876fd33`
worktree with the five patches extracted from this file applied
(*Embedding proof*), which `git diff --quiet 80d0f93` confirms equal to the
merged branch as a whole tree.

- [x] **Red on main** `876fd33` (`PYTHONPATH=<git archive 876fd33 src>`): `159 failed, 16 passed in 9.75s`
  (on `959d4d4`: `159 failed, 16 passed in 10.41s`). The 16 passing: the 12
  existing tests, the Y3 retention test (main reads all arguments), and the
  rejection sweep for the three tools that declare no property (no rejection
  path; the test asserts that and returns).
- [x] **Red on rev 1** (`9ced94f` `src/`): `117 failed, 58 passed in 8.29s` —
  all 52 allowed-sweep and 56 ungated-sweep items, plus the 9 targeted items
  listed for rev 3. The rejection sweep passes on rev 1: its rejection record
  was already clean; the sweep pins it.
- [x] **Rev 2/3 `src/`** (`e10304a`): `175 passed in 5.70s` — expected, rev 4
  changes no code; its additions are measured by the Z mutants.
- [x] **Green with the patch** (proof tree, `876fd33`): `175 passed in 6.05s`.
- [x] `ruff check src/ tests/` (proof tree): `All checks passed!`
- [x] `ruff format --check src/ tests/` (proof tree): `165 files already formatted`
- [x] `mypy src/pmcp --exclude baml_client` (proof tree): `Success: no issues found in 51 source files`;
  `mypy src/`: `Success: no issues found in 51 source files`
- [x] Full suite, patched (proof tree `876fd33`, `env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir`): `4516 passed, 3 skipped, 25 deselected in 460.19s (0:07:40)` (`exit=0`)
- [x] Full suite, main `876fd33` (fresh worktree, same host, same command): `4353 passed, 3 skipped, 25 deselected in 427.14s (0:07:07)` (`exit=0`)
  Difference: +163 passed = exactly the 163 new test items; skips and deselections unchanged.
  (On `959d4d4`, rev 4: `4499` patched vs `4336` main, the same +163.)
- [x] Mutation run (on `6ab3db9`, `959d4d4`-based; not rerun after the merge, since this plan's `src/` and tests are unchanged by it): `40/40 killed`; the sweep alone `19/40`, every survivor outside the class (§7).
- [x] Ledger fields: `ledger_fields.py` → `equal: True` @ `18a324a4`; self-tests renamed `equal: True`, helper `equal: True`, dropped `equal: False`.
- [x] Reader: agent-harness `reduce_research_audit` @ `18a324a4` on a rev 2 stream → `ledger: success None [('success', 'verified')]` (`src/` unchanged since).
- [x] Embedding proof: five patches extracted from this file apply to
  `876fd33` and the result is `cmp`-equal to `wip/296-code` @ `6ceff9a` on all five files.
- [x] After the escape commit `6ceff9a` (fresh `876fd33` proof tree): test module `175 passed in 6.74s`;
  `ruff check`: `All checks passed!`; `ruff format --check`: `165 files already formatted`. The
  full suite and mypy above ran on `80d0f93`; `6ceff9a` changes only four string
  literals in the test file to equivalent escapes.
- [ ] Panel CR + reconcile before merge (repo rule).
- [ ] Piece B of Consiliency/pmcp#236 does not merge before this.

## Mutation evidence

`mutants.py` (below) applies each mutant as one exact-string replacement to a
saved copy, runs `tests/test_scoped_advisor_audit.py`, and restores from the
saved copy (never `git checkout`). Rev 4 runs one merged set: rev 1's 14
(M1–M13, M4b), the rev 1 board seat's X1–X7, rev 2's N1–N8, the rev 2 board
seat's Y1, Y3, Y4, Y6, Y9, and the rev 3 board seat's Z1–Z6 (anchors from its
`zmutants.py`). Every anchor is unique. Run on `wip/296-code` @ `6ab3db9`;
output, verbatim from the log:

```text
M1 policy not judged before the gate: KILLED | 50 failed, 125 passed in 8.86s | test_an_ungated_call_records_nothing_the_caller_chose, test_generated_caller_values_never_reach_an_ungated_record, test_policy_is_judged_before_the_gate, test_the_policy_verdict_is_read_once_per_call
M2 call_tool re-reads the policy verdict: KILLED | 1 failed, 174 passed in 6.16s | test_the_policy_verdict_is_read_once_per_call
M3 relative .path instead of .absolute_path: KILLED | 3 failed, 172 passed in 5.80s | test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink, test_rejected_argument_path_redacts_caller_chosen_keys
M4 caller keys not checked against declared names: KILLED | 6 failed, 169 passed in 6.35s | test_rejected_argument_path_redacts_caller_chosen_keys, test_the_rejection_record_never_reads_the_message_or_the_instance
M4b str subclass accepted by isinstance: KILLED | 1 failed, 174 passed in 5.95s | test_rejected_argument_path_redacts_caller_chosen_keys
M5 int segment kept without a list container: KILLED | 1 failed, 174 passed in 5.63s | test_rejected_argument_path_redacts_caller_chosen_keys
M6 validator keyword not bounded: KILLED | 2 failed, 173 passed in 5.86s | test_an_unknown_validator_keyword_is_not_recorded
M7 gate rejection not recorded: KILLED | 26 failed, 149 passed in 5.89s | test_gate_rejection_with_a_dead_sink_fails_closed, test_gate_rejections_are_audited_without_argument_values, test_generated_caller_values_never_reach_a_rejection_record, test_rejected_and_denied_calls_never_log_argument_values
M8 dead sink not caught at the gate: KILLED | 2 failed, 173 passed in 6.14s | test_gate_rejection_with_a_dead_sink_fails_closed, test_rejected_and_denied_calls_never_log_argument_values
M9 correlation copied from unvalidated arguments: KILLED | 24 failed, 151 passed in 6.34s | test_gate_rejections_are_audited_without_argument_values, test_generated_caller_values_never_reach_a_rejection_record, test_the_rejection_record_never_reads_the_message_or_the_instance
M10 jsonschema message recorded: KILLED | 25 failed, 150 passed in 6.60s | test_gate_rejections_are_audited_without_argument_values, test_generated_caller_values_never_reach_a_rejection_record, test_the_rejection_record_never_reads_the_message_or_the_instance
M11 terminal_status failure: KILLED | 1 failed, 174 passed in 5.94s | test_gate_rejections_are_audited_without_argument_values
M13 rejection written as an audit.invocation: KILLED | 27 failed, 148 passed in 6.53s | test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink, test_gate_rejections_are_audited_without_argument_values, test_generated_caller_values_never_reach_a_rejection_record
M12 declared names from the top level only: KILLED | 2 failed, 173 passed in 5.67s | test_gate_rejections_are_audited_without_argument_values, test_rejected_argument_path_redacts_caller_chosen_keys
X1 dead-sink branch logs the ValidationError: KILLED | 1 failed, 174 passed in 6.18s | test_rejected_and_denied_calls_never_log_argument_values
X2 caller key kept when it looks like an identifier: KILLED | 3 failed, 172 passed in 6.40s | test_rejected_argument_path_redacts_caller_chosen_keys
X3 validator kept when it looks like an identifier: KILLED | 1 failed, 174 passed in 6.08s | test_an_unknown_validator_keyword_is_not_recorded
X4 gateway_tool not bounded to the scoped set: KILLED | 1 failed, 174 passed in 6.27s | test_the_rejection_names_only_a_scoped_gateway_tool
X5 declared names = every dict key in the schema: KILLED | 1 failed, 174 passed in 5.91s | test_rejected_argument_path_redacts_caller_chosen_keys
X6 gate logs the rejected arguments at debug: KILLED | 24 failed, 151 passed in 7.24s | test_generated_caller_values_never_reach_a_rejection_record, test_rejected_and_denied_calls_never_log_argument_values
X7 caller key kept when shorter than 16 chars: KILLED | 3 failed, 172 passed in 5.61s | test_rejected_argument_path_redacts_caller_chosen_keys, test_the_rejection_record_never_reads_the_message_or_the_instance
N1 denied arm records the caller's arguments (rev 1; board B1): KILLED | 57 failed, 118 passed in 8.02s | test_an_ungated_call_records_nothing_the_caller_chose, test_generated_caller_values_never_reach_an_ungated_record
N2 denied arm digests the payload that echoes the caller's name: KILLED | 3 failed, 172 passed in 5.84s | test_an_ungated_call_records_nothing_the_caller_chose, test_generated_caller_values_never_reach_an_ungated_record
N3 gated record reads undeclared keys: KILLED | 54 failed, 121 passed in 7.37s | test_a_gated_call_records_only_the_keys_its_schema_declares, test_an_allowed_call_that_raises_records_only_declared_keys, test_generated_caller_values_never_reach_an_allowed_record
N4 audit names the tool by the caller's string: KILLED | 6 failed, 169 passed in 6.41s | test_an_ungated_call_records_nothing_the_caller_chose, test_generated_caller_values_never_reach_an_ungated_record
N5 except arm records ungated arguments: KILLED | 31 failed, 144 passed in 6.52s | test_an_allowed_call_that_raises_records_only_declared_keys, test_an_ungated_call_records_nothing_the_caller_chose, test_generated_caller_values_never_reach_an_allowed_record, test_generated_caller_values_never_reach_an_ungated_record
N6 an exception describing the rejection escapes: KILLED | 2 failed, 173 passed in 6.47s | test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink
N7 the describing exception is chained: KILLED | 3 failed, 172 passed in 6.03s | test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink
N8 post-dispatch record reads the caller's arguments: KILLED | 27 failed, 148 passed in 6.73s | test_a_gated_call_records_only_the_keys_its_schema_declares, test_generated_caller_values_never_reach_an_allowed_record
Y1 declared filter drops evidence_label_digest: KILLED | 2 failed, 173 passed in 5.78s | test_an_invoke_source_hash_comes_from_its_declared_arguments, test_scoped_server_filters_controls_and_writes_private_complete_audit
Y3 declared filter drops invoke's arguments: KILLED | 1 failed, 174 passed in 6.00s | test_an_invoke_source_hash_comes_from_its_declared_arguments
Y4 declared filter applied before the gate: KILLED | 48 failed, 127 passed in 7.28s | test_an_ungated_call_records_nothing_the_caller_chose, test_generated_caller_values_never_reach_an_ungated_record
Y6 E16 catches only ValueError: KILLED | 2 failed, 173 passed in 6.13s | test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink
Y9 except arm records full gate-passed arguments: KILLED | 28 failed, 147 passed in 6.82s | test_an_allowed_call_that_raises_records_only_declared_keys, test_generated_caller_values_never_reach_an_allowed_record
Z1 filter applied only to gateway.invoke: KILLED | 52 failed, 123 passed in 7.06s | test_a_gated_call_records_only_the_keys_its_schema_declares, test_an_allowed_call_that_raises_records_only_declared_keys, test_generated_caller_values_never_reach_an_allowed_record
Z2 success arm falls back to raw arguments when the filter leaves nothing: KILLED | 8 failed, 167 passed in 6.23s | test_generated_caller_values_never_reach_an_allowed_record
Z3 underscore-prefixed undeclared keys pass the filter: KILLED | 52 failed, 123 passed in 7.24s | test_generated_caller_values_never_reach_an_allowed_record
Z4 dict-valued undeclared keys pass the filter: KILLED | 52 failed, 123 passed in 7.68s | test_generated_caller_values_never_reach_an_allowed_record
Z5 unregistered blocked name dispatched instead of denied: KILLED | 3 failed, 172 passed in 6.09s | test_an_ungated_call_records_nothing_the_caller_chose, test_rejected_and_denied_calls_never_log_argument_values, test_scoped_server_filters_controls_and_writes_private_complete_audit
Z6 filter keys compared case-insensitively: KILLED | 46 failed, 129 passed in 7.57s | test_generated_caller_values_never_reach_an_allowed_record
40/40 killed
```

**The sweep alone.** The same 40 mutants, running only the three generated
sweep tests (`mutants.py` with `"-k", "generated_caller_values"` added to the
pytest command); the partition is argued in §7:

```text
M1 policy not judged before the gate: KILLED | 46 failed, 88 passed, 41 deselected in 4.83s | test_generated_caller_values_never_reach_an_ungated_record
M2 call_tool re-reads the policy verdict: SURVIVED | 134 passed, 41 deselected in 3.03s | 
M3 relative .path instead of .absolute_path: SURVIVED | 134 passed, 41 deselected in 2.80s | 
M4 caller keys not checked against declared names: SURVIVED | 134 passed, 41 deselected in 2.91s | 
M4b str subclass accepted by isinstance: SURVIVED | 134 passed, 41 deselected in 3.03s | 
M5 int segment kept without a list container: SURVIVED | 134 passed, 41 deselected in 3.09s | 
M6 validator keyword not bounded: SURVIVED | 134 passed, 41 deselected in 2.75s | 
M7 gate rejection not recorded: KILLED | 23 failed, 111 passed, 41 deselected in 3.21s | test_generated_caller_values_never_reach_a_rejection_record
M8 dead sink not caught at the gate: SURVIVED | 134 passed, 41 deselected in 2.68s | 
M9 correlation copied from unvalidated arguments: KILLED | 22 failed, 112 passed, 41 deselected in 3.19s | test_generated_caller_values_never_reach_a_rejection_record
M10 jsonschema message recorded: KILLED | 23 failed, 111 passed, 41 deselected in 3.28s | test_generated_caller_values_never_reach_a_rejection_record
M11 terminal_status failure: SURVIVED | 134 passed, 41 deselected in 3.27s | 
M13 rejection written as an audit.invocation: KILLED | 23 failed, 111 passed, 41 deselected in 3.11s | test_generated_caller_values_never_reach_a_rejection_record
M12 declared names from the top level only: SURVIVED | 134 passed, 41 deselected in 3.22s | 
X1 dead-sink branch logs the ValidationError: SURVIVED | 134 passed, 41 deselected in 2.65s | 
X2 caller key kept when it looks like an identifier: SURVIVED | 134 passed, 41 deselected in 2.72s | 
X3 validator kept when it looks like an identifier: SURVIVED | 134 passed, 41 deselected in 2.81s | 
X4 gateway_tool not bounded to the scoped set: SURVIVED | 134 passed, 41 deselected in 2.93s | 
X5 declared names = every dict key in the schema: SURVIVED | 134 passed, 41 deselected in 2.68s | 
X6 gate logs the rejected arguments at debug: KILLED | 23 failed, 111 passed, 41 deselected in 3.36s | test_generated_caller_values_never_reach_a_rejection_record
X7 caller key kept when shorter than 16 chars: SURVIVED | 134 passed, 41 deselected in 2.89s | 
N1 denied arm records the caller's arguments (rev 1; board B1): KILLED | 54 failed, 80 passed, 41 deselected in 4.01s | test_generated_caller_values_never_reach_an_ungated_record
N2 denied arm digests the payload that echoes the caller's name: KILLED | 2 failed, 132 passed, 41 deselected in 2.68s | test_generated_caller_values_never_reach_an_ungated_record
N3 gated record reads undeclared keys: KILLED | 52 failed, 82 passed, 41 deselected in 3.79s | test_generated_caller_values_never_reach_an_allowed_record
N4 audit names the tool by the caller's string: KILLED | 4 failed, 130 passed, 41 deselected in 3.27s | test_generated_caller_values_never_reach_an_ungated_record
N5 except arm records ungated arguments: KILLED | 29 failed, 105 passed, 41 deselected in 3.26s | test_generated_caller_values_never_reach_an_allowed_record, test_generated_caller_values_never_reach_an_ungated_record
N6 an exception describing the rejection escapes: SURVIVED | 134 passed, 41 deselected in 2.86s | 
N7 the describing exception is chained: SURVIVED | 134 passed, 41 deselected in 2.97s | 
N8 post-dispatch record reads the caller's arguments: KILLED | 26 failed, 108 passed, 41 deselected in 3.69s | test_generated_caller_values_never_reach_an_allowed_record
Y1 declared filter drops evidence_label_digest: SURVIVED | 134 passed, 41 deselected in 2.74s | 
Y3 declared filter drops invoke's arguments: SURVIVED | 134 passed, 41 deselected in 2.78s | 
Y4 declared filter applied before the gate: KILLED | 46 failed, 88 passed, 41 deselected in 3.94s | test_generated_caller_values_never_reach_an_ungated_record
Y6 E16 catches only ValueError: SURVIVED | 134 passed, 41 deselected in 2.68s | 
Y9 except arm records full gate-passed arguments: KILLED | 27 failed, 107 passed, 41 deselected in 4.05s | test_generated_caller_values_never_reach_an_allowed_record
Z1 filter applied only to gateway.invoke: KILLED | 50 failed, 84 passed, 41 deselected in 3.94s | test_generated_caller_values_never_reach_an_allowed_record
Z2 success arm falls back to raw arguments when the filter leaves nothing: KILLED | 8 failed, 126 passed, 41 deselected in 2.95s | test_generated_caller_values_never_reach_an_allowed_record
Z3 underscore-prefixed undeclared keys pass the filter: KILLED | 52 failed, 82 passed, 41 deselected in 3.83s | test_generated_caller_values_never_reach_an_allowed_record
Z4 dict-valued undeclared keys pass the filter: KILLED | 52 failed, 82 passed, 41 deselected in 3.66s | test_generated_caller_values_never_reach_an_allowed_record
Z5 unregistered blocked name dispatched instead of denied: SURVIVED | 134 passed, 41 deselected in 2.74s | 
Z6 filter keys compared case-insensitively: KILLED | 46 failed, 88 passed, 41 deselected in 3.59s | test_generated_caller_values_never_reach_an_allowed_record
19/40 killed
```

History: X1–X6 survived rev 1 (killed since rev 2); N7 survived rev 2's first
E16 test; Y1, Y3, Y4, Y6, Y9 survived rev 2 (killed since rev 3); Z2, Z3, Z4,
Z6 survived rev 3 (killed since rev 4, by the sweep).

## Non-goals

- **Value echo in the response** (`Input validation error: 'sk-…' is not of
  type …`). Pre-existing, unchanged, and the caller's own value; tracked on
  Consiliency/pmcp#297 with the pydantic echo.
- **Surfacing rejections in the advisor-board ledger.** agent-harness skips
  `audit.rejection` today, which is the safe default. Counting them (e.g. a
  per-seat "rejected attempts" figure) is an agent-harness change; file an
  issue there if wanted.
- **Correlating a rejected call to its run/seat.** A rejected payload's
  correlation fields are unvalidated, and most API keys fit the correlation
  charset, so the gate record leaves them `null`. An operator sees the
  attempt, the tool, where and why it failed, and its position in the
  sequence; not which run sent it.
- **`RecursionError` on deeply nested in-process arguments (E12).** The wire
  cannot deliver them (measured). Catching it would mean treating an
  arbitrary exception from `validate` as a rejection; a separate issue if
  in-process embedders matter.
- **SDK-level rejections before `_handle_call_tool` (E15)** — a JSON-RPC
  error, not a tool call.
- Changing `record_invocation`'s body, the statuses of existing paths, or the
  response shape of any existing path. (Rev 2 changes what the *server* hands
  it — §6 — and widens its `gateway_tool` parameter to `str | None`.)
- **Logging the caller's tool name.** `call_tool`'s `except Exception` logs
  `f"Tool execution error: {e}"`, which for E5 includes `Unknown tool: <name>`
  and for a pydantic error can echo a value. Pre-existing, untouched, and
  unreachable for E5 under the scoped policy; Consiliency/pmcp#297's scope.
  The new `caplog` test covers every path this plan adds or reroutes (E1, E2,
  E4) and E16's exception is never logged.
- Piece B itself.
- **Follow-up (rev 2 seat): URL hashing on non-invoke tools.**
  `record_invocation` runs its public-URL scan (`source_reference_hash`) over
  the arguments of *every* tool, so a declared free-text field of a gated,
  allowed non-invoke call — `catalog_search {"query": "see https://…"}`, or a
  URL in `filters.tags` — is still hashed into the record. Same as main; this
  plan neither widens nor narrows it (the declared-keys filter lets declared
  fields through by design), and the ledger ignores non-invoke records.
  Proposed follow-up issue: have `record_invocation` read the correlations,
  `tool_id`, `evidence_label_digest` and the URL scan **only for
  `gateway.invoke`**, keeping just `gateway_tool`, its digest, the status and
  the result digest on other tools.
- **(rev 4, found by the sweep) `meta` echoed into the log.** An undeclared
  `meta` on a scoped `gateway.invoke` passes the gate, reaches `InvokeInput`
  by alias, fails it when not a dict, and `call_tool`'s `except Exception`
  logs `Tool execution error: …` including the value. Pre-existing on main,
  reachable under the scoped policy, never in the audit
  (`meta_log_probe.py`: `logged: True | audit: False` on both). It is the
  channel Consiliency/pmcp#297 owns and names verbatim ("A non-dict `meta`
  echoes its value"); piece B of Consiliency/pmcp#236 also turns it into a
  gate rejection. The sweep's log oracle excludes exactly that line shape
  (`_FOREIGN_LOG_PREFIX` + `_FOREIGN_LOG_CHANNELS`) and nothing wider.
- **`meta` vs `_meta` on `invoke` (rev 2 seat, harmless).** `InvokeInput`
  has `populate_by_name=True` with `meta` aliased to `_meta`, so a caller
  sending `meta` passes the gate as an undeclared key and the handler accepts
  it; the audit's URL scan now skips it (main scanned it). Research sources
  travel in `invoke.arguments`, which is scanned, so the ledger is unaffected
  (seat's reducer runs A–C: `success`).

## Unverified

- **Consumers outside `~/code/agent-harness` and `~/.local/share/agent-harness`.**
  The one reader found skips unknown events; rev 2 *ran* it (pinned install
  @ `18a324a4`) on a patched stream with rejections and nulled `denied`
  records (*Every path into the audit writer*). Any other reader that rejects
  unknown `event` values would need `audit.rejection`.
- **E13**: that `is_gateway_tool_allowed` cannot raise on a `str` name was
  read from `policy.py:399-423,524-534` (`fnmatchcase` over lists loaded at
  start-up), by rev 1 and independently by the board seat; not proven by test.

## Execution Policy

- execute: effort=low, reason=the patch is embedded verbatim and proven
  byte-identical to verified code; the executor applies, runs the
  Verification block and compares with *Acceptance criteria*.
- Every PR to main needs panel CR + reconcile first (repo rule).
- Commit/PR text says "see Consiliency/pmcp#296", never a closing keyword
  until the maintainer decides.

## Embedding proof

Rev 4 on main `876fd33`, after the escape commit: patches regenerated with
`git diff 876fd33 6ceff9a -- <file>` (only the test patch changed from the
`80d0f93` proof, and it is now pure ASCII) and embedded; fresh worktree
`$WORKTREE_ROOT/pmcp-296-esc-proof` (removed afterwards). No character of
category Cc (other than tab), Cf, Zl or Zp, no U+0085 and no Cyrillic letter
remains anywhere in this plan (`unicodedata` scan).

```text
$ git -C <fresh worktree> rev-parse --short HEAD
876fd33
<scratch>/emb/server.patch: 119 lines
<scratch>/emb/scoped_advisor_audit.patch: 158 lines
<scratch>/emb/test_scoped_advisor_audit.patch: 1089 lines
<scratch>/emb/CHANGELOG.patch: 15 lines
<scratch>/emb/README.patch: 18 lines
$ git apply --check <scratch>/emb/*.patch
check: ok
applied
cmp src/pmcp/server.py: identical to wip/296-code@6ceff9a
cmp src/pmcp/scoped_advisor_audit.py: identical to wip/296-code@6ceff9a
cmp tests/test_scoped_advisor_audit.py: identical to wip/296-code@6ceff9a
cmp CHANGELOG.md: identical to wip/296-code@6ceff9a
cmp README.md: identical to wip/296-code@6ceff9a
$ git status --short
 M CHANGELOG.md
 M README.md
 M src/pmcp/scoped_advisor_audit.py
 M src/pmcp/server.py
 M tests/test_scoped_advisor_audit.py
$ git diff --quiet 6ceff9a && echo "proof tree == 6ceff9a (whole tree)"
proof tree == 6ceff9a (whole tree)
```

## Verbatim bodies

### How to apply (and the extractor)

From a fresh worktree of `origin/main` @ `876fd33`:

```bash
PLAN=.consiliency/plans/detailed-296-gate-audit-20260926-2141.md   # read from branch plan/296-gate-audit
X=<scratch>/extract_plan_block.py        # the script below, saved verbatim
for f in server scoped_advisor_audit test_scoped_advisor_audit CHANGELOG README; do
  python3 $X $PLAN "### Patch — \`$f" <scratch>/$f.patch
done
git apply --check <scratch>/*.patch && git apply <scratch>/*.patch
```

Patches are fenced with **four** backticks, and the extractor closes on the
same fence string, so a context line of three backticks cannot end a block
early. Blank context lines carry one leading space: an editor that strips
trailing whitespace breaks them and `git apply --check` fails loudly.

````python
"""Extract one verbatim patch from the Consiliency/pmcp#296 plan, byte for byte.

usage: python extract_plan_block.py <plan.md> "<heading prefix>" <out-file>

Finds the single line that starts with the heading prefix, takes the first
fenced block after it, and writes every line up to the line that equals that
block's opening fence (stripped of its info string), each followed by "\n".
"""

import sys
from pathlib import Path

plan, heading, out = sys.argv[1], sys.argv[2], sys.argv[3]
lines = Path(plan).read_text().split("\n")
starts = [i for i, line in enumerate(lines) if line.startswith(heading)]
assert len(starts) == 1, f"heading {heading!r} found {len(starts)} times"
i = starts[0] + 1
while not lines[i].startswith("```"):
    i += 1
fence = lines[i][: len(lines[i]) - len(lines[i].lstrip("`"))]
j = i + 1
while lines[j] != fence:
    j += 1
Path(out).write_text("".join(line + "\n" for line in lines[i + 1 : j]))
print(f"{out}: {j - i - 1} lines")
````

### Patch — `server.py` (`src/pmcp/server.py`)

````diff
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 193ffe1..0a6ef28 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -233,7 +233,7 @@ class GatewayServer:
     def _record_scoped_invocation(
         self,
         *,
-        gateway_tool: str,
+        gateway_tool: str | None,
         terminal_status: str,
         arguments: dict[str, Any] | None,
         result: Any,
@@ -294,10 +294,47 @@ class GatewayServer:
         arguments = params.arguments or {}
 
         tool = self._find_gateway_tool(name)
-        if tool is not None:
+        # Policy is judged once, before the schema (Consiliency/pmcp#296): a
+        # blocked name is recorded `denied` by `call_tool` whatever its
+        # arguments, and its schema is never disclosed through a validation
+        # message. `call_tool` reads this same verdict, so no name can skip
+        # the gate as "blocked" and then be dispatched as "allowed".
+        allowed = self._policy_manager.is_gateway_tool_allowed(name)
+        # What the scoped audit may read from this call: the registry's name
+        # (never the caller's string), and only the top-level keys the tool's
+        # schema declares, of arguments that passed the gate. A blocked or
+        # unregistered call never met a schema, so nothing in it is vouched
+        # for; and until the gate forbids undeclared keys (piece B of
+        # Consiliency/pmcp#236), an undeclared key passes it unexamined.
+        audited_name = tool.name if tool is not None else None
+        audited_arguments: dict[str, Any] | None = None
+        if tool is not None and allowed:
             try:
                 jsonschema.validate(instance=arguments, schema=tool.input_schema)
             except jsonschema.ValidationError as e:
+                try:
+                    if self._scoped_advisor_audit is not None:
+                        self._scoped_advisor_audit.record_rejected_arguments(
+                            gateway_tool=tool.name,
+                            error=e,
+                            schema=tool.input_schema,
+                            arguments=arguments,
+                        )
+                except ScopedAdvisorAuditError:
+                    logger.error("Scoped advisor audit channel failed")
+                    return CallToolResult(
+                        content=[
+                            TextContent(
+                                type="text",
+                                text=json.dumps(
+                                    {
+                                        "error": True,
+                                        "message": "Scoped advisor audit channel failed",
+                                    }
+                                ),
+                            )
+                        ]
+                    )
                 return CallToolResult(
                     is_error=True,
                     content=[
@@ -306,22 +343,28 @@ class GatewayServer:
                         )
                     ],
                 )
+            declared = tool.input_schema.get("properties") or {}
+            audited_arguments = {
+                key: value for key, value in arguments.items() if key in declared
+            }
 
         async def call_tool(name: str, arguments: dict[str, Any]) -> list[ContentBlock]:
             try:
                 result: Any
                 self._require_scoped_audit()
 
-                if not self._policy_manager.is_gateway_tool_allowed(name):
+                if not allowed:
                     payload = {
                         "error": True,
                         "message": f"Gateway tool blocked by policy: {name}",
                     }
+                    # Nothing from the call: `audited_arguments` is None
+                    # here, and `payload` echoes the caller's name.
                     self._record_scoped_invocation(
-                        gateway_tool=name,
+                        gateway_tool=audited_name,
                         terminal_status="denied",
-                        arguments=arguments,
-                        result=payload,
+                        arguments=audited_arguments,
+                        result=None,
                     )
                     return [
                         TextContent(type="text", text=json.dumps(payload, indent=2))
@@ -412,9 +455,9 @@ class GatewayServer:
                         else "failure"
                     )
                 self._record_scoped_invocation(
-                    gateway_tool=name,
+                    gateway_tool=audited_name,
                     terminal_status=terminal_status,
-                    arguments=arguments,
+                    arguments=audited_arguments,
                     result=result,
                 )
 
@@ -443,9 +486,9 @@ class GatewayServer:
                         else "failure"
                     )
                     self._record_scoped_invocation(
-                        gateway_tool=name,
+                        gateway_tool=audited_name,
                         terminal_status=failure_status,
-                        arguments=arguments,
+                        arguments=audited_arguments,
                         result={"error_type": type(e).__name__},
                     )
                 except ScopedAdvisorAuditError:
````

### Patch — `scoped_advisor_audit.py` (`src/pmcp/scoped_advisor_audit.py`)

````diff
diff --git a/src/pmcp/scoped_advisor_audit.py b/src/pmcp/scoped_advisor_audit.py
index 8dfb4ee..76d96e6 100644
--- a/src/pmcp/scoped_advisor_audit.py
+++ b/src/pmcp/scoped_advisor_audit.py
@@ -13,7 +13,16 @@ from pathlib import Path
 from typing import Any, TextIO
 from urllib.parse import urlsplit, urlunsplit
 
+import jsonschema
+
 SCOPED_ADVISOR_AUDIT_CAPABILITY = "scoped_advisor_audit.v1"
+#: Event of a ``tools/call`` the input-schema gate rejected before dispatch
+#: (Consiliency/pmcp#296). Not an ``audit.invocation``: nothing was invoked, and
+#: a reader that correlates invocations to a run must not see an uncorrelated
+#: one (agent-harness ``advisor_board/research.py`` filters on this field).
+REJECTION_EVENT = "audit.rejection"
+#: Its ``terminal_status``. The invocation vocabulary stays success/failure/denied.
+INVALID_ARGUMENTS_STATUS = "invalid_arguments"
 _CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
 _DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
 _TOOL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+::[A-Za-z0-9_.-]{1,192}$")
@@ -129,6 +138,70 @@ def _public_source_hash(value: Any) -> str | None:
     return None
 
 
+def _declared_property_names(schema: Any) -> frozenset[str]:
+    """Every key of every ``properties`` map anywhere in ``schema``.
+
+    These strings are written by the schema's author, never by a caller, so a
+    path segment equal to one of them discloses nothing the schema does not.
+    """
+    names: set[str] = set()
+
+    def collect(node: Any) -> None:
+        if isinstance(node, dict):
+            properties = node.get("properties")
+            if isinstance(properties, dict):
+                names.update(key for key in properties if isinstance(key, str))
+            for child in node.values():
+                collect(child)
+        elif isinstance(node, list):
+            for child in node:
+                collect(child)
+
+    collect(schema)
+    return frozenset(names)
+
+
+def _rejected_argument_path(
+    error: jsonschema.ValidationError, schema: Any, arguments: Any
+) -> list[str | int | None]:
+    """The failing instance location, with every caller-chosen key redacted.
+
+    Walks ``error.absolute_path`` (not ``.path``, which is relative when
+    ``best_match`` returns an ``anyOf`` child). A segment survives only as an
+    array index (an ``int`` whose container is a list) or as a key the schema
+    declares under some ``properties``. Any other key -- one matched by
+    ``additionalProperties`` or ``patternProperties``, or a non-``str`` key a
+    caller built in-process (an ``int``, or a ``str`` subclass whose ``__eq__``
+    could pass the membership test) -- is chosen by the caller and may itself
+    be a secret, so it becomes ``None``. The walk reads only container types
+    from ``arguments``, never a value.
+    """
+    declared = _declared_property_names(schema)
+    path: list[str | int | None] = []
+    node = arguments
+    for segment in error.absolute_path:
+        if isinstance(node, list) and type(segment) is int:
+            path.append(segment)
+        elif isinstance(node, dict) and type(segment) is str and segment in declared:
+            path.append(segment)
+        else:
+            path.append(None)
+        try:
+            node = node[segment]
+        except (KeyError, IndexError, TypeError):
+            node = None
+    return path
+
+
+def _rejected_argument_validator(
+    error: jsonschema.ValidationError, schema: Any
+) -> str | None:
+    """The failing keyword, if it is one the schema's draft defines."""
+    keywords = jsonschema.validators.validator_for(schema).VALIDATORS
+    validator = error.validator
+    return validator if isinstance(validator, str) and validator in keywords else None
+
+
 class ScopedAdvisorAudit:
     """Append-only JSONL writer with a single fsynced terminal marker."""
 
@@ -182,7 +255,7 @@ class ScopedAdvisorAudit:
     def record_invocation(
         self,
         *,
-        gateway_tool: str,
+        gateway_tool: str | None,
         terminal_status: str,
         arguments: dict[str, Any] | None,
         result: Any,
@@ -227,6 +300,56 @@ class ScopedAdvisorAudit:
             }
         )
 
+    def record_rejected_arguments(
+        self,
+        *,
+        gateway_tool: str,
+        error: jsonschema.ValidationError,
+        schema: dict[str, Any],
+        arguments: dict[str, Any],
+    ) -> None:
+        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296).
+
+        The record names the tool, the failing JSON path and the validator
+        keyword, and nothing else from the call. It never reads the error's
+        ``message``, ``instance``, ``validator_value`` or ``context``: a
+        ``type``/``pattern``/``enum`` message echoes the rejected value, which
+        can be a secret. It carries no correlation, source hash or digest of
+        any argument either, because an unvalidated payload is exactly where a
+        caller can put a secret in a correlation-shaped field. It is its own
+        event, not an ``audit.invocation``, for the same reason: without
+        correlations it would read as a mismatched invocation.
+
+        Any exception while describing the rejection (a mapping built
+        in-process whose lookup raises, say) becomes
+        ``ScopedAdvisorAuditError``, so the gate fails closed instead of the
+        exception escaping unaudited. It is raised ``from None``: the original
+        may carry a caller value, and nothing chains it into a log.
+        """
+        try:
+            argument_path = _rejected_argument_path(error, schema, arguments)
+            validator = _rejected_argument_validator(error, schema)
+        except Exception:
+            raise ScopedAdvisorAuditError(
+                "scoped advisor audit could not describe a rejected call"
+            ) from None
+        self._write(
+            {
+                "event": REJECTION_EVENT,
+                "schema": SCOPED_ADVISOR_AUDIT_CAPABILITY,
+                "audit_session_id": self.audit_session_id,
+                "timestamp": time.time(),
+                "policy_digest": self.policy_digest,
+                "gateway_tool": gateway_tool
+                if gateway_tool in _SCOPED_GATEWAY_TOOLS
+                else None,
+                "gateway_tool_digest": _digest(gateway_tool),
+                "terminal_status": INVALID_ARGUMENTS_STATUS,
+                "rejected_argument_path": argument_path,
+                "rejected_argument_validator": validator,
+            }
+        )
+
     def complete(self) -> None:
         if self._completed:
             return
````

### Patch — `test_scoped_advisor_audit.py` (`tests/test_scoped_advisor_audit.py`)

````diff
diff --git a/tests/test_scoped_advisor_audit.py b/tests/test_scoped_advisor_audit.py
index 6ae178c..b4b6fed 100644
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -1,14 +1,18 @@
 from __future__ import annotations
 
+import hashlib
 import json
+import logging
 import os
 import subprocess
 import sys
 import time
+import traceback
 from pathlib import Path
 from typing import Any
 from unittest.mock import MagicMock
 
+import jsonschema
 import pytest
 from mcp.server.connection import Connection
 from mcp.server.context import ServerRequestContext
@@ -62,6 +66,36 @@ def _write_scoped_policy(path: Path) -> Path:
     return path
 
 
+#: The fields agent-harness's research reducer
+#: (`phase_loop_runtime/advisor_board/research.py` @ 18a324a4) reads from a
+#: `gateway.invoke` invocation record, or checks on every record. Each must
+#: survive on an invoke record, or the seat's ledger fails. It also reads
+#: `_COMPLETION_RECORD_FIELDS`, from the `audit.completed` record only.
+#: Consiliency/pmcp#296's plan derives the union of both sets from that file
+#: with `ledger_fields.py` (every string-keyed read on any name holding audit
+#: data, in the reducer and its helpers) and compares it with this file.
+_LEDGER_READ_FIELDS = frozenset(
+    {
+        "sequence",
+        "event",
+        "audit_session_id",
+        "policy_digest",
+        "gateway_tool",
+        "downstream_tool_id",
+        "terminal_status",
+        "source_reference_hash",
+        "evidence_label_digest",
+        "run_correlation_id",
+        "seat_correlation_id",
+    }
+)
+
+
+_COMPLETION_RECORD_FIELDS = frozenset(
+    {"first_sequence", "last_sequence", "record_count"}
+)
+
+
 def _correlations() -> dict[str, str]:
     return {
         "run_correlation_id": "run-103",
@@ -324,7 +358,13 @@ async def test_scoped_server_filters_controls_and_writes_private_complete_audit(
     assert firecrawl_record["run_correlation_id"] == "run-103"
     assert firecrawl_record["seat_correlation_id"] == "seat-codex"
     assert firecrawl_record["source_reference_hash"]
+    # Everything the board ledger reads survives the declared-keys filter.
+    for field in _LEDGER_READ_FIELDS:
+        assert firecrawl_record[field] is not None, field
+    assert firecrawl_record["evidence_label_digest"] == "a" * 64
     assert records[-1]["record_count"] == len(records)
+    for field in _COMPLETION_RECORD_FIELDS:
+        assert records[-1][field] is not None, field
     raw_audit = audit_path.read_text()
     for forbidden in (
         "example.com",
@@ -578,3 +618,1014 @@ def test_capability_probe_is_machine_readable() -> None:
     assert (
         "terminal_completion_fsync" in payload["capabilities"][0]["activation_requires"]
     )
+
+
+# --- gate rejections reach the audit (Consiliency/pmcp#296) --------------------
+
+#: The exact key set of every ``audit.rejection`` record. A new key is a new
+#: channel out of an unvalidated payload, so it must be added here on purpose.
+_REJECTION_RECORD_KEYS = frozenset(
+    {
+        "sequence",
+        "event",
+        "schema",
+        "audit_session_id",
+        "timestamp",
+        "policy_digest",
+        "gateway_tool",
+        "gateway_tool_digest",
+        "terminal_status",
+        "rejected_argument_path",
+        "rejected_argument_validator",
+    }
+)
+
+
+def _scoped_server(tmp_path: Path) -> tuple[GatewayServer, Path]:
+    audit_path = tmp_path / "audit.jsonl"
+    server = GatewayServer(
+        policy_path=_write_scoped_policy(tmp_path / "policy.json"),
+        audit_jsonl=audit_path,
+    )
+    server._create_server()
+    return server, audit_path
+
+
+async def _call(server: GatewayServer, name: str, arguments: dict) -> Any:
+    assert server._server is not None
+    entry = server._server.get_request_handler("tools/call")
+    assert entry is not None
+    return await entry.handler(
+        _make_ctx(), CallToolRequestParams(name=name, arguments=arguments)
+    )
+
+
+@pytest.mark.asyncio
+async def test_gate_rejections_are_audited_without_argument_values(
+    tmp_path: Path,
+) -> None:
+    server, audit_path = _scoped_server(tmp_path)
+    dispatched: list[dict] = []
+
+    async def recording_invoke(arguments: dict) -> dict:
+        dispatched.append(arguments)
+        return {"ok": True}
+
+    server._gateway_tools.invoke = recording_invoke  # type: ignore[method-assign]
+    cases = [
+        # (arguments, expected path, expected keyword)
+        (
+            {"tool_id": "firecrawl::web_search", "arguments": "sk-TYPE-SECRET"},
+            ["arguments"],
+            "type",
+        ),
+        (
+            {
+                "tool_id": "firecrawl::web_search",
+                **_correlations(),
+                "evidence_label_digest": "sk-PATTERN-SECRET" + "Z" * 47,
+            },
+            ["evidence_label_digest"],
+            "pattern",
+        ),
+        (
+            {
+                "tool_id": "firecrawl::web_search",
+                **_correlations(),
+                "run_correlation_id": ["run-LIST-SECRET"],
+            },
+            ["run_correlation_id"],
+            "type",
+        ),
+        (
+            {"tool_id": "firecrawl::web_search", "task": {"enabled": "sk-ENUM"}},
+            ["task", "enabled"],
+            "type",
+        ),
+        ({"arguments": {"q": "sk-MISSING-SECRET"}}, [], "required"),
+    ]
+    for arguments, _, _ in cases:
+        result = await _call(server, "gateway.invoke", arguments)
+        # The caller still gets the gate's own message, unchanged.
+        assert result.is_error is True
+        assert result.content[0].text.startswith("Input validation error: ")
+    assert dispatched == []
+    await server.shutdown()
+
+    records = validate_scoped_advisor_audit(audit_path)
+    # No `audit.invocation` at all: agent-harness's board ledger
+    # (`advisor_board/research.py:465-481` @ b9627d53) correlates every
+    # `gateway.invoke` invocation to its run, and a record without
+    # correlations would fail the seat as `audit_correlation_mismatch`.
+    assert [r for r in records if r["event"] == "audit.invocation"] == []
+    rejections = [r for r in records if r["event"] == "audit.rejection"]
+    assert [r["terminal_status"] for r in rejections] == ["invalid_arguments"] * len(
+        cases
+    )
+    assert [
+        (r["rejected_argument_path"], r["rejected_argument_validator"])
+        for r in rejections
+    ] == [(path, keyword) for _, path, keyword in cases]
+    for record in rejections:
+        assert set(record) == _REJECTION_RECORD_KEYS
+        assert record["gateway_tool"] == "gateway.invoke"
+    raw_audit = audit_path.read_text()
+    for forbidden in (
+        "sk-TYPE-SECRET",
+        "sk-PATTERN-SECRET",
+        "run-LIST-SECRET",
+        "sk-ENUM",
+        "sk-MISSING-SECRET",
+        "run-103",
+        "seat-codex",
+        "firecrawl::web_search",
+        "is not of type",
+        "does not match",
+    ):
+        assert forbidden not in raw_audit
+
+
+@pytest.mark.asyncio
+async def test_policy_is_judged_before_the_gate(tmp_path: Path) -> None:
+    """A malformed call to a blocked tool is `denied`, not `invalid_arguments`.
+
+    `gateway.provision` is outside the scoped policy. Its argument is the
+    wrong type, which the gate would reject, but policy answers first and
+    the blocked tool's schema is not disclosed.
+    """
+    server, audit_path = _scoped_server(tmp_path)
+    result = await _call(
+        server, "gateway.provision", {"server_name": 12345, "api_key": "sk-X"}
+    )
+    text = result.content[0].text
+    assert "blocked by policy" in json.loads(text)["message"]
+    assert "Input validation error" not in text
+    await server.shutdown()
+    records = validate_scoped_advisor_audit(audit_path)
+    assert [
+        r["terminal_status"] for r in records if r["event"] == "audit.invocation"
+    ] == ["denied"]
+    assert "sk-X" not in audit_path.read_text()
+
+
+@pytest.mark.asyncio
+async def test_the_policy_verdict_is_read_once_per_call(tmp_path: Path) -> None:
+    """No name can skip the gate as blocked and then be dispatched as allowed.
+
+    The stub answers "blocked" the first time and "allowed" after. Reading
+    the verdict twice would skip the schema gate and then dispatch the
+    malformed arguments to the handler.
+    """
+    server, _ = _scoped_server(tmp_path)
+    verdicts = iter([False, True, True, True])
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: next(verdicts)
+    )
+    dispatched: list[dict] = []
+
+    async def recording_describe(arguments: dict) -> dict:
+        dispatched.append(arguments)
+        return {"ok": True}
+
+    server._gateway_tools.describe = recording_describe  # type: ignore[method-assign]
+    result = await _call(server, "gateway.describe", {"tool_id": ""})
+    assert "blocked by policy" in json.loads(result.content[0].text)["message"]
+    assert dispatched == []
+    await server.shutdown()
+
+
+@pytest.mark.asyncio
+async def test_gate_rejection_with_a_dead_sink_fails_closed(tmp_path: Path) -> None:
+    server, _ = _scoped_server(tmp_path)
+    assert server._scoped_advisor_audit is not None
+    assert server._scoped_advisor_audit._file is not None
+    server._scoped_advisor_audit._file.close()
+    result = await _call(server, "gateway.describe", {"tool_id": ""})
+    assert json.loads(result.content[0].text) == {
+        "error": True,
+        "message": "Scoped advisor audit channel failed",
+    }
+    assert not result.is_error
+
+
+def _first_error(instance: Any, schema: dict[str, Any]) -> jsonschema.ValidationError:
+    error = jsonschema.exceptions.best_match(
+        jsonschema.validators.validator_for(schema)(schema).iter_errors(instance)
+    )
+    assert error is not None
+    return error
+
+
+def _record_one(
+    tmp_path: Path,
+    error: jsonschema.ValidationError,
+    schema: dict[str, Any],
+    arguments: Any,
+) -> dict[str, Any]:
+    path = tmp_path / f"audit-{len(list(tmp_path.iterdir()))}.jsonl"
+    audit = ScopedAdvisorAudit(path, policy_digest="e" * 64)
+    audit.record_rejected_arguments(
+        gateway_tool="gateway.invoke", error=error, schema=schema, arguments=arguments
+    )
+    audit.complete()
+    records = validate_scoped_advisor_audit(path)
+    assert "sk-" not in path.read_text()
+    return records[1]
+
+
+class _EqualsEverything(str):
+    """A ``str`` subclass that claims to equal any declared name."""
+
+    def __eq__(self, other: object) -> bool:
+        return True
+
+    def __hash__(self) -> int:
+        return hash("tool_id")
+
+
+_CALLER_KEYED_SCHEMA: dict[str, Any] = {
+    "type": "object",
+    "properties": {
+        "env": {"type": "object", "additionalProperties": {"type": "integer"}},
+        "hdrs": {
+            "type": "object",
+            "patternProperties": {"^x-": {"type": "integer"}},
+        },
+        "names": {"type": "array", "items": {"type": "string"}},
+        "tool_id": {"type": "string"},
+        "a": {
+            "anyOf": [
+                {"type": "object", "properties": {"b": {"type": "string"}}},
+                {"type": "integer"},
+            ]
+        },
+    },
+}
+
+
+@pytest.mark.parametrize(
+    ("arguments", "expected_path", "keyword"),
+    [
+        # A key the caller chose under additionalProperties is itself redacted.
+        ({"env": {"sk-KEY-SECRET": "v"}}, ["env", None], "type"),
+        # ... whatever its shape: an identifier-like key is no more public.
+        ({"env": {"caller_chosen_key_value": "v"}}, ["env", None], "type"),
+        ({"env": {"CallerChosenKey42": "v"}}, ["env", None], "type"),
+        # A schema keyword is not a declared property name either.
+        ({"env": {"additionalProperties": "v"}}, ["env", None], "type"),
+        # ... and so is one matched by patternProperties.
+        ({"hdrs": {"x-sk-HEADER": "v"}}, ["hdrs", None], "type"),
+        # A caller key that equals a declared name elsewhere discloses nothing.
+        ({"env": {"tool_id": "v"}}, ["env", "tool_id"], "type"),
+        # An in-process int key is not an array index.
+        ({"env": {12345: "v"}}, ["env", None], "type"),
+        # A str subclass cannot smuggle its content past the membership test.
+        ({"env": {_EqualsEverything("sk-EQ-SECRET"): "v"}}, ["env", None], "type"),
+        # An array index survives, as a position.
+        ({"names": ["ok", 7]}, ["names", 1], "type"),
+        # best_match returns an anyOf child: its `.path` is relative (["b"]).
+        ({"a": {"b": 1}}, ["a", "b"], "type"),
+    ],
+)
+def test_rejected_argument_path_redacts_caller_chosen_keys(
+    tmp_path: Path, arguments: dict, expected_path: list, keyword: str
+) -> None:
+    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
+    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, arguments)
+    assert record["rejected_argument_path"] == expected_path
+    assert record["rejected_argument_validator"] == keyword
+
+
+@pytest.mark.parametrize("keyword", ["sk-KEYWORD-SECRET", "caller_chosen_keyword"])
+def test_an_unknown_validator_keyword_is_not_recorded(
+    tmp_path: Path, keyword: str
+) -> None:
+    error = jsonschema.ValidationError("m", validator=keyword, path=["tool_id"])
+    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, {"tool_id": "x"})
+    assert record["rejected_argument_validator"] is None
+    assert record["rejected_argument_path"] == ["tool_id"]
+
+
+class _PoisonedError(jsonschema.ValidationError):
+    """Every attribute that can carry the rejected value raises on access."""
+
+    def _poisoned(self: Any) -> Any:
+        raise AssertionError("the audit read a value-bearing error attribute")
+
+    message = property(_poisoned)  # type: ignore[assignment]
+    instance = property(_poisoned)  # type: ignore[assignment]
+    validator_value = property(_poisoned)  # type: ignore[assignment]
+    context = property(_poisoned)  # type: ignore[assignment]
+    cause = property(_poisoned)  # type: ignore[assignment]
+    json_path = property(_poisoned)  # type: ignore[assignment]
+    schema = property(_poisoned)  # type: ignore[assignment]
+
+
+def test_the_rejection_record_never_reads_the_message_or_the_instance(
+    tmp_path: Path,
+) -> None:
+    arguments = {"env": {"sk-KEY": "sk-VALUE"}}
+    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
+    error.__class__ = _PoisonedError
+    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, arguments)
+    assert set(record) == _REJECTION_RECORD_KEYS
+    assert record["rejected_argument_path"] == ["env", None]
+
+
+# --- rev 2: nothing the caller chose reaches a denied record (Consiliency/pmcp#296) ---
+
+#: Everything in a record that does not come from the writer's clock or counter.
+_VOLATILE_KEYS = frozenset({"sequence", "timestamp"})
+
+
+def _caller_values(tag: str) -> dict[str, str]:
+    """Values shaped to pass every filter `record_invocation` applies.
+
+    Each fits the correlation charset, the tool-id pattern, the 64-hex digest
+    pattern or the public-URL scan, so a record that reads the field at all
+    carries it. Identifier-like on purpose: a secret need not contain `-`.
+    """
+    digit = {"a": "0", "b": "1"}[tag]
+    return {
+        "run_correlation_id": f"caller_chosen_run_value_{tag}",
+        "seat_correlation_id": f"caller_chosen_seat_value_{tag}",
+        "evidence_label_digest": digit * 64,
+        "tool_id": f"caller::chosen_tool_value_{tag}",
+        "note": f"https://caller-chosen-host-{tag}.example.com/caller_chosen_path",
+    }
+
+
+def _format_tag(value: Any, tag: str) -> Any:
+    if isinstance(value, str):
+        return value.format(tag=tag)
+    if isinstance(value, list):
+        return [_format_tag(item, tag) for item in value]
+    return value
+
+
+def _stable(record: dict[str, Any]) -> dict[str, Any]:
+    return {k: v for k, v in record.items() if k not in _VOLATILE_KEYS}
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize(
+    ("label", "names", "base", "allow_everything", "response"),
+    [
+        (
+            "blocked, malformed",
+            ("gateway.provision", "gateway.provision"),
+            # Malformed (not a string) yet URL-bearing, in the one key
+            # `provision` declares: a filter run before the gate would read it.
+            {"server_name": ["https://caller-chosen-server-{tag}.example.com/p"]},
+            False,
+            "blocked by policy",
+        ),
+        (
+            "blocked, well-formed",
+            ("gateway.provision", "gateway.provision"),
+            {"server_name": "https://caller-chosen-server-{tag}.example.com/p"},
+            False,
+            "blocked by policy",
+        ),
+        (
+            "unregistered name, blocked",
+            ("gateway.caller_chosen_name_a", "gateway.caller_chosen_name_b"),
+            {},
+            False,
+            "blocked by policy",
+        ),
+        (
+            # Unreachable under the scoped policy (it allows four registered
+            # names), so pinned with a policy that allows every name: the
+            # arguments never met a schema, so they never reach the record.
+            "unregistered name, allowed",
+            ("gateway.caller_chosen_name_a", "gateway.caller_chosen_name_b"),
+            {},
+            True,
+            "Unknown tool",
+        ),
+    ],
+    ids=[
+        "blocked-malformed",
+        "blocked-wellformed",
+        "unregistered",
+        "unregistered-allowed",
+    ],
+)
+async def test_an_ungated_call_records_nothing_the_caller_chose(
+    tmp_path: Path,
+    label: str,
+    names: tuple[str, str],
+    base: dict[str, Any],
+    allow_everything: bool,
+    response: str,
+) -> None:
+    """Two calls that differ only in what the caller chose leave equal records.
+
+    The differential is the oracle: it does not depend on knowing which field
+    `record_invocation` reads, so a new channel fails it too. Every value is
+    shaped to survive that method's charset filters.
+    """
+    server, audit_path = _scoped_server(tmp_path)
+    if allow_everything:
+        server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+            lambda name: True
+        )
+    for name, tag in zip(names, ("a", "b")):
+        tagged = {key: _format_tag(value, tag) for key, value in base.items()}
+        result = await _call(server, name, {**tagged, **_caller_values(tag)})
+        assert response in result.content[0].text, label
+    await server.shutdown()
+
+    records = validate_scoped_advisor_audit(audit_path)
+    first, second = [r for r in records if r["event"] == "audit.invocation"]
+    assert _stable(first) == _stable(second), label
+    for field in (
+        "run_correlation_id",
+        "seat_correlation_id",
+        "evidence_label_digest",
+        "downstream_tool_id",
+        "source_reference_hash",
+    ):
+        assert first[field] is None, (label, field)
+    raw_audit = audit_path.read_text()
+    for forbidden in ("caller_chosen", "caller::chosen", "caller-chosen", "0" * 64):
+        assert forbidden not in raw_audit, (label, forbidden)
+
+
+@pytest.mark.asyncio
+async def test_a_gated_call_records_only_the_keys_its_schema_declares(
+    tmp_path: Path,
+) -> None:
+    """The gate ignores undeclared keys (until piece B of Consiliency/pmcp#236).
+
+    `gateway.describe` declares only `tool_id`, so the correlation-shaped keys
+    and the URL below pass the gate unexamined. They must not reach the record.
+    """
+    server, audit_path = _scoped_server(tmp_path)
+
+    async def stub_describe(arguments: dict) -> dict:
+        return {"ok": True}
+
+    server._gateway_tools.describe = stub_describe  # type: ignore[method-assign]
+    for tag in ("a", "b"):
+        undeclared = {k: v for k, v in _caller_values(tag).items() if k != "tool_id"}
+        result = await _call(
+            server,
+            "gateway.describe",
+            {"tool_id": "firecrawl::web_search", **undeclared},
+        )
+        assert json.loads(result.content[0].text) == {"ok": True}
+    await server.shutdown()
+
+    records = validate_scoped_advisor_audit(audit_path)
+    first, second = [r for r in records if r["event"] == "audit.invocation"]
+    assert _stable(first) == _stable(second)
+    assert first["terminal_status"] == "success"
+    # A declared, gate-checked field still reaches the record.
+    assert first["downstream_tool_id"] == "firecrawl::web_search"
+    for field in (
+        "run_correlation_id",
+        "seat_correlation_id",
+        "evidence_label_digest",
+        "source_reference_hash",
+    ):
+        assert first[field] is None, field
+    raw_audit = audit_path.read_text()
+    for forbidden in ("caller_chosen", "caller-chosen", "0" * 64):
+        assert forbidden not in raw_audit, forbidden
+
+
+@pytest.mark.asyncio
+async def test_rejected_and_denied_calls_never_log_argument_values(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture
+) -> None:
+    """No rejection or denial path logs a value or the jsonschema message."""
+    caplog.set_level(logging.DEBUG)
+    secret = "caller_chosen_logged_value"
+    (tmp_path / "live").mkdir()
+    (tmp_path / "dead").mkdir()
+    live, _ = _scoped_server(tmp_path / "live")
+    await _call(  # E1: an allowed tool's gate rejection echoes the value
+        live,
+        "gateway.invoke",
+        {"tool_id": "firecrawl::web_search", "arguments": secret, **_correlations()},
+    )
+    await _call(  # E4: blocked, malformed
+        live, "gateway.provision", {"server_name": 12345, "run_correlation_id": secret}
+    )
+    await _call(  # E4: blocked, well-formed
+        live, "gateway.provision", {"server_name": "x", "run_correlation_id": secret}
+    )
+    await _call(  # E4: unregistered name
+        live, f"gateway.{secret}", {"run_correlation_id": secret}
+    )
+    await live.shutdown()
+
+    dead, _ = _scoped_server(tmp_path / "dead")
+    assert dead._scoped_advisor_audit is not None
+    assert dead._scoped_advisor_audit._file is not None
+    dead._scoped_advisor_audit._file.close()
+    result = await _call(  # E2: the same rejection with a dead sink
+        dead,
+        "gateway.invoke",
+        {"tool_id": "firecrawl::web_search", "arguments": secret, **_correlations()},
+    )
+    assert "Scoped advisor audit channel failed" in result.content[0].text
+
+    assert caplog.records, "nothing was logged, so this test proves nothing"
+    for forbidden in (secret, "is not of type", "Input validation error"):
+        assert forbidden not in caplog.text, forbidden
+
+
+def test_the_rejection_names_only_a_scoped_gateway_tool(tmp_path: Path) -> None:
+    arguments = {"tool_id": 1}
+    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
+    path = tmp_path / "audit.jsonl"
+    audit = ScopedAdvisorAudit(path, policy_digest="e" * 64)
+    audit.record_rejected_arguments(
+        gateway_tool="gateway.provision",
+        error=error,
+        schema=_CALLER_KEYED_SCHEMA,
+        arguments=arguments,
+    )
+    audit.complete()
+    record = validate_scoped_advisor_audit(path)[1]
+    assert record["gateway_tool"] is None
+    assert record["rejected_argument_path"] == ["tool_id"]
+
+
+class _RaisingDict(dict):
+    """A mapping an in-process caller built, whose lookups raise."""
+
+    def __getitem__(self, key: Any) -> Any:
+        raise ValueError("caller_chosen_lookup_failure")
+
+
+def _raising_path_error(exc_type: type[Exception]) -> jsonschema.ValidationError:
+    """An error whose `absolute_path` raises `exc_type` when read."""
+
+    class _RaisingPath(jsonschema.ValidationError):
+        @property  # type: ignore[override]
+        def absolute_path(self) -> Any:
+            raise exc_type("caller_chosen_lookup_failure")
+
+    error = _first_error({"env": {"k": "v"}}, _CALLER_KEYED_SCHEMA)
+    error.__class__ = _RaisingPath
+    return error
+
+
+@pytest.mark.parametrize("exc_type", [ValueError, KeyError, TypeError])
+def test_a_rejection_that_cannot_be_described_fails_closed_and_keeps_the_sink(
+    tmp_path: Path, exc_type: type[Exception]
+) -> None:
+    """Any exception while describing the rejection is an audit failure.
+
+    It surfaces as `ScopedAdvisorAuditError`, which the gate answers with
+    "channel failed", instead of escaping `_handle_call_tool` unaudited; it
+    writes nothing, and the sink stays live for the next call. `ValueError`
+    comes from a caller's mapping during the path walk; `KeyError` and
+    `TypeError` -- which the walk itself tolerates on a lookup -- from reading
+    the error's path.
+    """
+    error = _first_error({"env": {"k": "v"}}, _CALLER_KEYED_SCHEMA)
+    path = tmp_path / "audit.jsonl"
+    audit = ScopedAdvisorAudit(path, policy_digest="e" * 64)
+    if exc_type is ValueError:
+        failing_error = error
+        arguments: dict[str, Any] = {"env": _RaisingDict({"k": "v"})}
+    else:
+        failing_error = _raising_path_error(exc_type)
+        arguments = {"env": {"k": "v"}}
+    with pytest.raises(ScopedAdvisorAuditError) as raised:
+        audit.record_rejected_arguments(
+            gateway_tool="gateway.invoke",
+            error=failing_error,
+            schema=_CALLER_KEYED_SCHEMA,
+            arguments=arguments,
+        )
+    # Not in the message, and not in a logged traceback either: the original
+    # exception is neither the cause nor displayed as the context.
+    logged = "".join(traceback.format_exception(raised.value))
+    assert "caller_chosen" not in logged
+    audit.require_available()
+    audit.record_rejected_arguments(
+        gateway_tool="gateway.invoke",
+        error=error,
+        schema=_CALLER_KEYED_SCHEMA,
+        arguments={"env": {"k": "v"}},
+    )
+    audit.complete()
+    records = validate_scoped_advisor_audit(path)
+    assert [r["event"] for r in records] == [
+        "audit.started",
+        "audit.rejection",
+        "audit.completed",
+    ]
+
+
+@pytest.mark.asyncio
+async def test_an_invoke_source_hash_comes_from_its_declared_arguments(
+    tmp_path: Path,
+) -> None:
+    """`invoke.arguments` is declared, so its URL still reaches the record.
+
+    The downstream result carries no URL here, so the only source for
+    `source_reference_hash` -- which the ledger needs to verify a claim -- is
+    the declared `arguments` object.
+    """
+    server, audit_path = _scoped_server(tmp_path)
+
+    async def stub_invoke(arguments: dict) -> dict:
+        return {"ok": True, "result": "a page with no link in it"}
+
+    server._gateway_tools.invoke = stub_invoke  # type: ignore[method-assign]
+    result = await _call(
+        server,
+        "gateway.invoke",
+        {
+            "tool_id": "firecrawl::web_search",
+            "arguments": {"url": "https://source.example/article"},
+            **_correlations(),
+        },
+    )
+    assert json.loads(result.content[0].text)["ok"] is True
+    await server.shutdown()
+
+    records = validate_scoped_advisor_audit(audit_path)
+    (record,) = [r for r in records if r["event"] == "audit.invocation"]
+    assert (
+        record["source_reference_hash"]
+        == hashlib.sha256(b"https://source.example/article").hexdigest()
+    )
+    for field in _LEDGER_READ_FIELDS:
+        assert record[field] is not None, field
+
+
+@pytest.mark.asyncio
+async def test_an_allowed_call_that_raises_records_only_declared_keys(
+    tmp_path: Path,
+) -> None:
+    """The `except` arm (E9) of a registered, allowed tool reads the filter too."""
+    server, audit_path = _scoped_server(tmp_path)
+
+    async def failing_describe(arguments: dict) -> dict:
+        raise ValueError("downstream describe failed")
+
+    server._gateway_tools.describe = failing_describe  # type: ignore[method-assign]
+    for tag in ("a", "b"):
+        undeclared = {k: v for k, v in _caller_values(tag).items() if k != "tool_id"}
+        await _call(
+            server,
+            "gateway.describe",
+            {"tool_id": "firecrawl::web_search", **undeclared},
+        )
+    await server.shutdown()
+
+    records = validate_scoped_advisor_audit(audit_path)
+    first, second = [r for r in records if r["event"] == "audit.invocation"]
+    assert first["terminal_status"] == "failure"
+    assert _stable(first) == _stable(second)
+    assert first["downstream_tool_id"] == "firecrawl::web_search"
+    for field in (
+        "run_correlation_id",
+        "seat_correlation_id",
+        "evidence_label_digest",
+        "source_reference_hash",
+    ):
+        assert first[field] is None, field
+    raw_audit = audit_path.read_text()
+    for forbidden in ("caller_chosen", "caller-chosen", "0" * 64):
+        assert forbidden not in raw_audit, forbidden
+
+
+# --- rev 4: a generated sweep of caller values over every tool and path ----
+#
+# Rounds 1-3 of the board each found a mutant the hand-picked caller values
+# missed (an identifier-shaped key, a blocked tool's declared key, a tool that
+# declares nothing, an `_`-prefixed, dict-valued or upper-case key). This sweep
+# generates the values instead, over three axes, for every registered gateway
+# tool on every path through `_handle_call_tool`:
+#
+# - key spelling: every name `record_invocation` reads (`_RECORD_READ_NAMES`),
+#   every name the tool declares, `meta`/`_meta`, and a plain caller key --
+#   each as is, `_`-prefixed, upper-case, title-case, with a Cyrillic
+#   look-alike letter, and with a zero-width suffix; minus the names the tool
+#   declares (those are the gate's to vouch for);
+# - value shape: a correlation-shaped string, a `tool::id`-shaped string, a
+#   64-hex digest, a public URL, a dict and a list nesting a URL, a number;
+# - position: top level, and nested inside a declared key (ungated paths).
+#
+# The oracle is differential, so it needs no list of channels: two calls that
+# differ only in the generated values must leave equal records, and on an
+# allowed tool that record must equal the one for the declared arguments
+# alone. On top, no generated marker, URL, or hash of either appears in the
+# audit or the log.
+
+#: Argument names `record_invocation` reads by name.
+_RECORD_READ_NAMES = (
+    "tool_id",
+    "run_correlation_id",
+    "seat_correlation_id",
+    "evidence_label_digest",
+)
+_SHAPES = ("correlation", "tool_id", "digest", "url", "dict", "list", "number")
+_MARKERS = (
+    "caller_marker",
+    "caller::marker",
+    "caller-host",
+    "caller_path",
+    "caller_key",
+)
+#: The index of the one generated value that sits in a declared key the gate
+#: rejects (see `_invalid_declared`).
+_INVALID_INDEX = 99
+#: The one pre-existing log line this plan does not own, and exactly it:
+#: `call_tool`'s `except Exception` logs `f"Tool execution error: {e}"`, which
+#: echoes a pydantic `InvokeInput` error's input -- e.g. a non-dict `meta`
+#: reaching the model by alias, on main too (Consiliency/pmcp#297 names it) --
+#: and an unknown tool's name (unreachable under the scoped policy).
+_FOREIGN_LOG_PREFIX = "Tool execution error: "
+_FOREIGN_LOG_CHANNELS = ("validation error for InvokeInput", "Unknown tool:")
+
+
+def _gateway_tools_by_name() -> dict[str, Any]:
+    from pmcp.tools.handlers import get_gateway_tool_definitions
+
+    return {tool.name: tool for tool in get_gateway_tool_definitions()}
+
+
+def _spellings(name: str) -> set[str]:
+    return {
+        name,
+        "_" + name,
+        name.upper(),
+        name.title(),
+        name.replace("e", "\u0435", 1).replace("a", "\u0430", 1),
+        name + "\u200b",
+    }
+
+
+def _generated_keys(declared: set[str], tag: str) -> list[str]:
+    """Key spellings; the caller's own key name varies with `tag` too."""
+    names = set(_RECORD_READ_NAMES) | declared | {"meta", "_meta", f"caller_key_{tag}"}
+    keys: set[str] = set()
+    for name in names:
+        keys |= _spellings(name)
+    return sorted(keys - declared)
+
+
+def _generated_value(shape: str, tag: str, index: int) -> Any:
+    url = f"https://caller-host-{tag}{index}.example.com/caller_path_{tag}{index}"
+    if shape == "correlation":
+        return f"caller_marker_{tag}{index}"
+    if shape == "tool_id":
+        return f"caller::marker_{tag}{index}"
+    if shape == "digest":
+        return hashlib.sha256(f"caller_marker_{tag}{index}".encode()).hexdigest()
+    if shape == "url":
+        return url
+    if shape == "dict":
+        return {"caller_marker_inner": {"deep": url}}
+    if shape == "list":
+        return [f"caller_marker_{tag}{index}", [url]]
+    assert shape == "number"
+    return {"a": 1000, "b": 2000}[tag] + index
+
+
+def _generated(keys: list[str], shape: str, tag: str) -> dict[str, Any]:
+    return {key: _generated_value(shape, tag, i) for i, key in enumerate(keys)}
+
+
+def _forbidden_in_audit(count: int, tag: str) -> set[str]:
+    """Every generated string, and each hash of one, the audit could carry."""
+    forbidden = set(_MARKERS)
+    for i in [*range(count), _INVALID_INDEX]:
+        forbidden.add(_generated_value("digest", tag, i))
+        url = _generated_value("url", tag, i)
+        normalized = url.lower().split("?")[0]
+        forbidden.add(hashlib.sha256(normalized.encode()).hexdigest())
+        for shape in ("correlation", "tool_id", "url"):
+            value = _generated_value(shape, tag, i)
+            forbidden.add(hashlib.sha256(value.encode()).hexdigest())
+            forbidden.add(hashlib.sha256(json.dumps(value).encode()).hexdigest())
+    return forbidden
+
+
+def _declared_baseline(tool: Any) -> dict[str, Any]:
+    """The smallest arguments the tool's schema accepts, from its schema."""
+    schema = tool.input_schema
+    baseline: dict[str, Any] = {}
+    for name in schema.get("required") or []:
+        prop = schema["properties"][name]
+        if "enum" in prop:
+            baseline[name] = prop["enum"][0]
+        else:
+            baseline[name] = "x" * max(prop.get("minLength", 1), 1)
+    if tool.name == "gateway.invoke":
+        # Correlated, so the scoped `InvokeInput` check passes and the
+        # allowed path reaches the handler.
+        baseline.update(_correlations())
+    jsonschema.validate(baseline, schema)
+    return baseline
+
+
+class _StubGatewayTools:
+    """Stands in for `GatewayTools`: every handler succeeds or raises."""
+
+    def __init__(self, fail: bool) -> None:
+        self._fail = fail
+
+    def __getattr__(self, name: str) -> Any:
+        async def handler(*args: Any, **kwargs: Any) -> dict:
+            if self._fail:
+                raise RuntimeError("stub handler failed")
+            return {"ok": True}
+
+        return handler
+
+
+def _invocations(audit_path: Path, event: str) -> list[dict[str, Any]]:
+    return [
+        _stable(r)
+        for r in validate_scoped_advisor_audit(audit_path)
+        if r["event"] == event
+    ]
+
+
+def _assert_nothing_generated_leaked(raw_audit: str, log_text: str, count: int) -> None:
+    for tag in ("a", "b"):
+        for forbidden in _forbidden_in_audit(count, tag):
+            assert forbidden not in raw_audit, forbidden
+    for marker in _MARKERS:
+        assert marker not in log_text, marker
+
+
+def _is_foreign(message: str) -> bool:
+    return message.startswith(_FOREIGN_LOG_PREFIX) and any(
+        channel in message for channel in _FOREIGN_LOG_CHANNELS
+    )
+
+
+def _log_text(caplog: pytest.LogCaptureFixture) -> str:
+    # The capture is live (DEBUG, root), so an empty result is not vacuous
+    # by accident: the gateway logs on start-up and shutdown.
+    assert caplog.records, "nothing was logged, so the log oracle proves nothing"
+    return "\n".join(
+        record.getMessage()
+        for record in caplog.records
+        if not _is_foreign(record.getMessage())
+    )
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("path", ["allowed-ok", "allowed-handler-error"])
+@pytest.mark.parametrize("tool_name", sorted(_gateway_tools_by_name()))
+async def test_generated_caller_values_never_reach_an_allowed_record(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture, tool_name: str, path: str
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    tool = _gateway_tools_by_name()[tool_name]
+    declared = set(tool.input_schema.get("properties") or {})
+    baseline = _declared_baseline(tool)
+    server, audit_path = _scoped_server(tmp_path)
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: True
+    )
+    real_tools = server._gateway_tools
+    server._gateway_tools = _StubGatewayTools(  # type: ignore[assignment]
+        fail=path == "allowed-handler-error"
+    )
+    await _call(server, tool_name, baseline)
+    for shape in _SHAPES:
+        for tag in ("a", "b"):
+            keys = _generated_keys(declared, tag)
+            await _call(server, tool_name, {**baseline, **_generated(keys, shape, tag)})
+    server._gateway_tools = real_tools
+    await server.shutdown()
+
+    records = _invocations(audit_path, "audit.invocation")
+    assert len(records) == 1 + 2 * len(_SHAPES)
+    reference, generated = records[0], records[1:]
+    for shape, first, second in zip(_SHAPES, generated[::2], generated[1::2]):
+        assert first == second, (tool_name, shape)
+        # Against the declared arguments alone. Status and result digest may
+        # differ: an undeclared `meta` reaches `InvokeInput` by alias.
+        for field in set(first) - {"terminal_status", "redacted_result_digest"}:
+            assert first[field] == reference[field], (tool_name, shape, field)
+    _assert_nothing_generated_leaked(
+        audit_path.read_text(), _log_text(caplog), len(_generated_keys(declared, "a"))
+    )
+
+
+def _invalid_declared(tool: Any, baseline: dict[str, Any], tag: str) -> dict | None:
+    """The baseline with one declared value the gate rejects, or None."""
+    for name in sorted(tool.input_schema.get("properties") or {}):
+        for shape in ("dict", "list", "number", "correlation"):
+            candidate = {**baseline, name: _generated_value(shape, tag, _INVALID_INDEX)}
+            try:
+                jsonschema.validate(candidate, tool.input_schema)
+            except jsonschema.ValidationError:
+                return candidate
+    return None
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("tool_name", sorted(_gateway_tools_by_name()))
+async def test_generated_caller_values_never_reach_a_rejection_record(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture, tool_name: str
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    tool = _gateway_tools_by_name()[tool_name]
+    declared = set(tool.input_schema.get("properties") or {})
+    baseline = _declared_baseline(tool)
+    if _invalid_declared(tool, baseline, "a") is None:
+        # No declared property -- the gate accepts any object until piece B
+        # of Consiliency/pmcp#236 -- so this tool has no rejection path.
+        assert not declared, tool_name
+        return
+    server, audit_path = _scoped_server(tmp_path)
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: True
+    )
+    for shape in _SHAPES:
+        for tag in ("a", "b"):
+            invalid = _invalid_declared(tool, baseline, tag)
+            assert invalid is not None
+            keys = _generated_keys(declared, tag)
+            result = await _call(
+                server, tool_name, {**invalid, **_generated(keys, shape, tag)}
+            )
+            assert result.is_error is True
+    await server.shutdown()
+
+    assert _invocations(audit_path, "audit.invocation") == []
+    records = _invocations(audit_path, "audit.rejection")
+    assert len(records) == 2 * len(_SHAPES)
+    for shape, first, second in zip(_SHAPES, records[::2], records[1::2]):
+        assert first == second, (tool_name, shape)
+    _assert_nothing_generated_leaked(
+        audit_path.read_text(), _log_text(caplog), len(_generated_keys(declared, "a"))
+    )
+
+
+#: Pairs of unregistered names over the same spelling axes as the keys.
+_UNREGISTERED_NAME_PAIRS = (
+    ("gateway.caller_marker_a", "gateway.caller_marker_b"),
+    ("GATEWAY.HEALTH", "Gateway.Health"),
+    ("_gateway.health", "gateway.health\u200b"),
+    ("gateway.h\u0435alth", "gateway.he\u0430lth"),
+    ("gateway.run_correlation_id", "gateway.tool_id"),
+)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("position", ["top-level", "nested-in-declared"])
+@pytest.mark.parametrize(
+    ("tool_name", "policy"),
+    [
+        *((name, "blocked") for name in sorted(_gateway_tools_by_name())),
+        ("<unregistered>", "blocked"),
+        ("<unregistered>", "allowed"),
+    ],
+)
+async def test_generated_caller_values_never_reach_an_ungated_record(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    tool_name: str,
+    position: str,
+    policy: str,
+) -> None:
+    """Blocked tools, and unregistered names blocked or allowed: nothing met a
+    schema, so every argument -- declared keys included -- and the name are
+    the caller's alone."""
+    caplog.set_level(logging.DEBUG)
+    tools = _gateway_tools_by_name()
+    tool = tools.get(tool_name)
+    declared = set(tool.input_schema.get("properties") or {}) if tool else set()
+    host = sorted(declared)[0] if declared else "arguments"
+    server, audit_path = _scoped_server(tmp_path)
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: policy == "allowed"
+    )
+    names = _UNREGISTERED_NAME_PAIRS if tool is None else ((tool_name, tool_name),)
+    for shape in _SHAPES:
+        for pair in names:
+            for name, tag in zip(pair, ("a", "b")):
+                keys = _generated_keys(declared, tag) + sorted(declared)
+                generated = _generated(keys, shape, tag)
+                arguments = generated if position == "top-level" else {host: generated}
+                await _call(server, name, arguments)
+    await server.shutdown()
+
+    records = _invocations(audit_path, "audit.invocation")
+    assert len(records) == 2 * len(_SHAPES) * len(names)
+    # Nothing in any record depends on the call: all are equal.
+    assert all(record == records[0] for record in records), tool_name
+    for field in (*_RECORD_READ_NAMES, "downstream_tool_id", "source_reference_hash"):
+        assert records[0].get(field) is None, field
+    _assert_nothing_generated_leaked(
+        audit_path.read_text(),
+        _log_text(caplog),
+        len(_generated_keys(declared, "a")) + len(declared),
+    )
````

### Patch — `CHANGELOG.md`

````diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ee5bacd..fae154f 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -367,8 +367,9 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 
 
 ### Fixed
+- **`tools/call` input-schema rejections are now recorded in the scoped-advisor audit, without argument values (Consiliency/pmcp#296).** A call the transport gate rejects used to return `Input validation error: …` before the audit was reached, so an operator saw no attempt at all. It is now written as a new `audit.rejection` event (not an `audit.invocation`: nothing was invoked, and a reader that correlates invocations to a run skips it) with the tool name, `terminal_status: "invalid_arguments"`, `rejected_argument_path`, the failing location as a JSON array (a key the schema declares, an array index, or `null` for a key the caller chose, since that key can itself be a secret), and `rejected_argument_validator`, the failing JSON Schema keyword (`type`, `pattern`, `required`, …). The record never contains the validation message, the rejected value, correlation IDs, or any digest of the arguments. The capability stays `scoped_advisor_audit.v1`; readers that dispatch on `event` are unaffected. Policy is now judged **before** the schema: a call to a policy-blocked gateway tool is refused with "Gateway tool blocked by policy" and recorded `denied` whatever its arguments, instead of getting an `Input validation error` that described the blocked tool's schema. If the audit sink has failed, a malformed call now gets "Scoped advisor audit channel failed" like every other call, instead of its validation error. The response to a rejected call from an allowed tool is unchanged. An `audit.invocation` record now reads nothing the schema gate did not vouch for: a call refused by policy, or made to an unregistered name, is recorded `denied` with every argument-derived field (`run_correlation_id`, `seat_correlation_id`, `downstream_tool_id`, `evidence_label_digest`, `source_reference_hash`) `null`, a result digest that no longer covers the caller's tool name, and a `gateway_tool_digest` of the registered name (for an unregistered name, of nothing) — previously a correlation-shaped value or a public URL anywhere in such a call's arguments was copied or hashed into the audit. Every other invocation record reads only the top-level arguments the tool's schema declares, so a correlation-shaped key a tool does not declare (e.g. `run_correlation_id` on `gateway.describe`) is no longer recorded; `gateway.invoke` declares every field the record reads, so its records are unchanged.
 - **`sanitize_auth_diagnostic` does its keyword and URL-punctuation work in linear time.** The keyword rule now runs through `pmcp.keyword_matcher` (the same matches as the regular expression it replaces, pinned by a seeded corpus), and trailing punctuation is split off a URL in one pass. Output is unchanged.
-- **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree (Consiliency/pmcp#236).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and the transport gate now accepts an explicit `null` for them, as the handlers always did; 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` now advertises its range on both sides, so `1e20`, `-1e20` and `float(±2**63)` are rejected at the gate, and so is any integer outside [−2^63+1, 2^63−1] (including `-2**63` itself), which the handler previously accepted. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. **Scoped-audit change until Consiliency/pmcp#296 lands:** gate rejections are not written to the scoped-advisor audit. So a *malformed* call to a *policy-blocked* gateway tool now gets `Input validation error: …` instead of "Gateway tool blocked by policy", and it is **no longer recorded as `denied`**. The same holds for the other inputs the gate now rejects that previously reached the handler and were recorded as `failure`. Well-formed calls to blocked tools are still recorded `denied`. Unknown keys are still ignored in this release — see the following entry once B lands. Argument descriptions agents already saw are unchanged, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description.
+- **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree (Consiliency/pmcp#236).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and the transport gate now accepts an explicit `null` for them, as the handlers always did; 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` now advertises its range on both sides, so `1e20`, `-1e20` and `float(±2**63)` are rejected at the gate, and so is any integer outside [−2^63+1, 2^63−1] (including `-2**63` itself), which the handler previously accepted. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. Inputs the gate now rejects that previously reached the handler were recorded in the scoped-advisor audit as `failure`; they are now recorded as `audit.rejection` events with `terminal_status: "invalid_arguments"` (see the Consiliency/pmcp#296 entry above). Unknown keys are still ignored in this release — see the following entry once B lands. Argument descriptions agents already saw are unchanged, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description.
 - **Exact-version validation follows npm's classification of package specs.** `is_valid_package_version` now refuses a version ending in `.tgz`, `.tar` or `.tar.gz` (any case), matching npm-package-arg's `isFileType` rule, which npm applies before reading a selector as a registry version; and a version whose major, minor or patch exceeds 2^53 - 1 (JavaScript's `Number.MAX_SAFE_INTEGER`), which node-semver refuses and npm-package-arg then reads as a dist-tag. It uses npm 10's pattern (npm-package-arg 12.x, whose `.` before `gz` is unescaped), a superset of npm 11's, since pmcp runs whichever `npx` is on PATH. The provision gate, package approvals, the CLI and the `gateway.provision` handler inherit it. **Upgrade note:** a package approval recorded earlier at either kind of version now approves nothing; it is ignored with a warning naming it (the rest of the store keeps working) and dropped on the next write to the store; re-approving the package at a registry version is that write. (`pmcp trust revoke-package <name>` also clears it, but a bare name revokes that package's valid approvals too.) A record with any other defect still fails the store closed.
 - **A downstream MCP server can no longer hang a caller by sending a request, being cancelled, or timing out — the remaining "the server hangs" runtime gaps are closed.** A server→client JSON-RPC request (a frame carrying both `method` and `id`) is now answered rather than dropped: `ping` gets an empty result and any other method a `-32601` "Method not found" refusal (the gateway advertises no client capabilities, so it does not forward an untrusted server's request to the agent), and classifying by `method` first also stops a downstream request whose id collides with one of ours from being misrouted as our response. `_send_request` no longer leaks a `pending_requests` entry when the caller is cancelled or the write itself raises — the entry is popped in a `finally` and a mid-write error still propagates. Cancellation is now propagated downstream as `notifications/cancelled` on `gateway.cancel`, on idle/ceiling timeout, and on caller cancellation (never for `initialize`, per spec; exactly once per cancellation). Replies and cancellation notifications go through a bounded per-server outbound queue drained by a single writer task whose lifecycle is torn down with the connection, so a downstream that stalls its own sink cannot make the gateway allocate unbounded tasks or buffer unbounded frames (review findings C-01, C-02, C-04). See [#232](https://github.com/Consiliency/pmcp/issues/232).
 - **A non-ASCII `Authorization` header no longer turns any request into a 500.** `hmac.compare_digest` raises `TypeError` on `str` containing non-ASCII, so an unauthenticated caller could crash any request with one header byte; the shared-secret comparison now happens on bytes and a bad header is simply unauthorized (review finding S-09). See [#231](https://github.com/Consiliency/pmcp/issues/231).
````

### Patch — `README.md`

````diff
diff --git a/README.md b/README.md
index 550464c..8a274d9 100644
--- a/README.md
+++ b/README.md
@@ -1517,7 +1517,12 @@ supply `run_correlation_id`, `seat_correlation_id`, and a SHA-256
 `evidence_label_digest` together. The append-only audit stores correlations,
 tool/status/policy/result digests, and a hashed public-source reference—not raw
 URLs, queries, arguments, credentials, or result bodies—and ends with one
-fsynced completeness marker.
+fsynced completeness marker. A call whose arguments fail the tool's input schema
+is recorded as a separate `audit.rejection` event (`terminal_status:
+"invalid_arguments"`) carrying only the tool, the failing JSON path
+(caller-chosen keys shown as `null`) and the failing schema keyword. Only
+arguments that passed the tool's input schema, and only keys it declares, are
+read into any record; a call denied by policy records none of its arguments.
 
 Consumers can fail closed on older installations with:
 
````

## Measurement scripts

### `probe_gate_audit.py`

````python
"""Probe: which tools/call rejections reach the scoped audit (Consiliency/pmcp#296)."""
import asyncio, json, sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock
from mcp.server.connection import Connection
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from mcp.types import CallToolRequestParams
import pmcp.server as S
from pmcp.server import GatewayServer

print("pmcp.server from", S.__file__)

def ctx():
    c = Connection.from_envelope("2025-11-25", None, None)
    return ServerRequestContext(session=ServerSession(MagicMock(), c), lifespan_context={}, protocol_version="2025-11-25", method="test")

def policy(p: Path) -> Path:
    p.write_text(json.dumps({"servers": {"allowlist": ["firecrawl", "brightdata"]},
        "gateway_tools": {"allowlist": ["gateway.health", "gateway.catalog_search", "gateway.describe", "gateway.invoke"]},
        "tools": {"allowlist": ["firecrawl::*search*", "firecrawl::*scrape*", "brightdata::*search*", "brightdata::*scrape*"]},
        "resources": {"denylist": ["*"]}, "prompts": {"denylist": ["*"]}}))
    return p

CORR = {"run_correlation_id": "run-1", "seat_correlation_id": "seat-1", "evidence_label_digest": "a" * 64}
CASES = [
    ("allowed, schema-malformed", "gateway.describe", {"tool_id": ""}),
    ("allowed, type error echoing a secret", "gateway.invoke", {"tool_id": "firecrawl::web_search", "arguments": "sk-SECRET-1", **CORR}),
    ("allowed, pattern error echoing a secret", "gateway.invoke", {"tool_id": "firecrawl::web_search", "arguments": {}, **CORR, "evidence_label_digest": "sk-SECRET-2"}),
    ("blocked, schema-malformed", "gateway.provision", {"server_name": 12345}),
    ("blocked, model-only-malformed on pre-A", "gateway.provision", {"server_name": ""}),
    ("blocked, well-formed", "gateway.provision", {"server_name": "x"}),
    ("unregistered name", "gateway.nope", {"a": 1}),
]

async def main():
    tmp = Path(tempfile.mkdtemp())
    audit = tmp / "audit.jsonl"
    srv = GatewayServer(policy_path=policy(tmp / "p.json"), audit_jsonl=audit)
    for label, name, args in CASES:
        before = len(audit.read_text().splitlines())
        r = await srv._handle_call_tool(ctx(), CallToolRequestParams(name=name, arguments=args))
        after = audit.read_text().splitlines()[before:]
        recs = [json.loads(l) for l in after]
        print(f"{label:40s} {name:18s} is_error={bool(r.is_error)!s:5s} text={r.content[0].text[:60]!r}")
        print(f"{'':40s} audit={[x['event'] + ':' + x['terminal_status'] for x in recs]} extra={[{k: x.get(k) for k in x if k.startswith('rejected')} for x in recs]}")
    await srv.shutdown()
    raw = audit.read_text()
    print("SECRET in audit:", any(s in raw for s in ("sk-SECRET-1", "sk-SECRET-2")))
    # dead sink
    srv2 = GatewayServer(policy_path=policy(tmp / "p2.json"), audit_jsonl=tmp / "a2.jsonl")
    srv2._scoped_advisor_audit._failed = True
    r = await srv2._handle_call_tool(ctx(), CallToolRequestParams(name="gateway.describe", arguments={"tool_id": ""}))
    print("dead sink, malformed describe:", bool(r.is_error), r.content[0].text[:70])

asyncio.run(main())
````

### `reader.py` (rev 2; the board seat's script plus four calls)

Usage: `uv run python reader.py <dir holding probe_gate_audit.py saved as
probe.py> ~/.local/share/agent-harness/phase-loop-runtime/src`. Importing
`probe` also runs that probe once (its module level calls `asyncio.run`), so
its output precedes this script's.

````python
"""Feed a patched-pmcp audit stream (valid invoke + rejections) to agent-harness's reducer."""
import asyncio, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
from probe import ctx, policy
from mcp.types import CallToolRequestParams
from pmcp.server import GatewayServer
from phase_loop_runtime.advisor_board import research as R
CORR = {"run_correlation_id": "run-1", "seat_correlation_id": "seat-1", "evidence_label_digest": "a"*64}
async def main():
    tmp = Path(tempfile.mkdtemp()); audit = tmp/"audit.jsonl"
    srv = GatewayServer(policy_path=policy(tmp/"p.json"), audit_jsonl=audit)
    async def fake_invoke(a): return {"ok": True, "result": "see https://example.com/page"}
    srv._gateway_tools.invoke = fake_invoke
    calls = [("gateway.invoke", {"tool_id": "firecrawl::web_search", "arguments": {}, **CORR}),
             ("gateway.invoke", {"tool_id": "firecrawl::web_search", "arguments": "x", **CORR}),
             ("gateway.describe", {"tool_id": ""}),
             ("gateway.invoke", {"arguments": {}}),
             # rev 2: denied records (all argument-derived fields null) and a gated
             # non-invoke record with undeclared correlation-shaped keys
             ("gateway.provision", {"server_name": 12345, "run_correlation_id": "run-1"}),
             ("gateway.provision", {"server_name": "x", **CORR}),
             ("gateway.caller_chosen_name", CORR),
             ("gateway.describe", {"tool_id": "firecrawl::web_search", **CORR})]
    for n, a in calls:
        await srv._handle_call_tool(ctx(), CallToolRequestParams(name=n, arguments=a))
    pd = srv._scoped_advisor_audit.policy_digest
    await srv.shutdown()
    for l in audit.read_text().splitlines(): r=json.loads(l); print(r["sequence"], r["event"], r.get("gateway_tool"), r.get("terminal_status"), r.get("rejected_argument_path"), r.get("run_correlation_id"))
    cfg = R.ResearchSeatConfig(lane="l", run_correlation_id="run-1", seat_correlation_id="seat-1", evidence_label="EVID", evidence_label_digest="a"*64,
        policy_path=tmp/"p.json", provider_config_path=tmp/"x", manifest_path=tmp/"y", policy_digest=pd, lock_dir=tmp, audit_path=audit, pmcp_command=("pmcp",))
    led = R.reduce_research_audit(cfg, "text with EVID")
    print("ledger:", led.status, led.detail, [(i.terminal_status, i.claim_status) for i in led.invocations])
asyncio.run(main())
````

The seat's `leak.py` is not reproduced here: its sample secret is shaped like
a real GitHub token, which push protection rejects. Its rerun on rev 2, with
that string elided:

```text
blocked tool, MALFORMED, secret in run_correlation_id
    audit.invocation denied run= None seat= None src= None
blocked tool, MALFORMED, secret URL
    audit.invocation denied run= None seat= None src= None
blocked tool, well-formed, secret in seat_correlation_id
    audit.invocation denied run= None seat= None src= None
unregistered name, secret
    audit.invocation denied run= None seat= None src= None
```

### `ledger_fields.py` (rev 3; rev 4: taint-based, robust to naming and helpers)

````python
"""Derive every audit-record field agent-harness's research reducer reads, and
compare them with the test module's `_LEDGER_READ_FIELDS` and
`_COMPLETION_RECORD_FIELDS` (Consiliency/pmcp#296, rev 4).

usage: python ledger_fields.py <research.py> <tests/test_scoped_advisor_audit.py>

Robust to naming: it does not look for a variable called `record`. Within
`reduce_research_audit` and every module function it calls (transitively), a
name holds audit data ("tainted") if it is bound -- by assignment, tuple
unpacking, a `for` or a comprehension -- from an expression that mentions
`json.loads`, a tainted name, or a call to a function that returns a tainted
value. Every string-constant `.get(...)` or `[...]` on a tainted name is a
field read. It fails loudly if it finds no source or no reads.
"""
import ast
import sys
from pathlib import Path

module = ast.parse(Path(sys.argv[1]).read_text())
functions = {n.name: n for n in module.body if isinstance(n, ast.FunctionDef)}


def called(fn: ast.FunctionDef) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in functions
    }


reach, todo = set(), ["reduce_research_audit"]
while todo:
    name = todo.pop()
    if name not in reach:
        reach.add(name)
        todo.extend(called(functions[name]))


def names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def targets_of(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def is_json_loads(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "loads"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "json"
        for n in ast.walk(node)
    )


tainted: dict[str, set[str]] = {name: set() for name in reach}
returns_tainted: set[str] = set()
changed = True
while changed:
    changed = False
    for name in reach:
        fn, t = functions[name], tainted[name]

        def source(value: ast.AST) -> bool:
            calls = {
                n.func.id
                for n in ast.walk(value)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            return is_json_loads(value) or bool(names_in(value) & t) or bool(calls & returns_tainted)

        bindings: list[tuple[ast.AST, ast.AST]] = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                bindings += [(target, n.value) for target in n.targets]
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and n.value is not None:
                bindings.append((n.target, n.value))
            elif isinstance(n, (ast.For, ast.comprehension)):
                bindings.append((n.target, n.iter))
            elif isinstance(n, ast.NamedExpr):
                bindings.append((n.target, n.value))
            elif isinstance(n, ast.Return) and n.value is not None and source(n.value):
                if name not in returns_tainted:
                    returns_tainted.add(name)
                    changed = True
        for target, value in bindings:
            if source(value):
                new = targets_of(target) - t
                if new:
                    t |= new
                    changed = True

reads: dict[str, set[str]] = {}
for name in reach:
    for n in ast.walk(functions[name]):
        receiver, key = None, None
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)
        ):
            receiver, key = n.func.value, n.args[0].value
        elif (
            isinstance(n, ast.Subscript)
            and isinstance(n.slice, ast.Constant)
            and isinstance(n.slice.value, str)
        ):
            receiver, key = n.value, n.slice.value
        if isinstance(receiver, ast.Name) and receiver.id in tainted[name]:
            reads.setdefault(key, set()).add(f"{name}:{receiver.id}")


def param_reads(fn: ast.FunctionDef) -> set[tuple[int, int]]:
    """(receiver param index, key param index) of each `p.get(q)` / `p[q]`."""
    params = [a.arg for a in fn.args.args]
    found = set()
    for n in ast.walk(fn):
        receiver = key = None
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args:
            receiver, key = n.func.value, n.args[0]
        elif isinstance(n, ast.Subscript):
            receiver, key = n.value, n.slice
        if isinstance(receiver, ast.Name) and isinstance(key, ast.Name) and receiver.id in params and key.id in params:
            found.add((params.index(receiver.id), params.index(key.id)))
    return found


# A helper that reads a field it is handed: `helper(tainted, "field")`.
for name in reach:
    for n in ast.walk(functions[name]):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in functions:
            for r, k in param_reads(functions[n.func.id]):
                if max(r, k) < len(n.args):
                    receiver, key = n.args[r], n.args[k]
                    if (
                        isinstance(receiver, ast.Name)
                        and receiver.id in tainted[name]
                        and isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                    ):
                        reads.setdefault(key.value, set()).add(f"{name}:{receiver.id}->{n.func.id}")
assert returns_tainted or any(tainted.values()), "no audit data source found"
assert reads, "no field reads found"

tests = ast.parse(Path(sys.argv[2]).read_text())


def pinned(constant: str) -> set[str]:
    (value,) = [
        node.value
        for node in tests.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == constant for t in node.targets)
    ]
    return set(ast.literal_eval(value.args[0]))


print("functions:", sorted(reach))
for key in sorted(reads):
    print(f"  {key:24s} read via {sorted(reads[key])}")
derived = set(reads)
pins = pinned("_LEDGER_READ_FIELDS") | pinned("_COMPLETION_RECORD_FIELDS")
print("derived only:", sorted(derived - pins))
print("pinned only: ", sorted(pins - derived))
print("equal:", derived == pins)
````

Output on the pinned install (`~/.local/share/agent-harness` @ `18a324a4`;
its `research.py` `cmp`-equal to `~/code/agent-harness`'s) against
`wip/296-code` @ `6ab3db9`, followed by three self-tests on edited copies of
that `research.py` (`record` renamed to `rec` with one read turned into a
subscript; that read moved into a helper `_field(rec, "downstream_tool_id")`;
and one read deleted — which must, and does, print `equal: False`):

```text
== pinned install @ 18a324a4
functions: ['_digest_json', '_validated_records', 'reduce_research_audit']
  audit_session_id         read via ['_validated_records:record']
  downstream_tool_id       read via ['reduce_research_audit:record']
  event                    read via ['_validated_records:record', 'reduce_research_audit:record']
  evidence_label_digest    read via ['reduce_research_audit:record']
  first_sequence           read via ['_validated_records:terminal']
  gateway_tool             read via ['reduce_research_audit:record']
  last_sequence            read via ['_validated_records:terminal']
  policy_digest            read via ['_validated_records:record']
  record_count             read via ['_validated_records:terminal']
  run_correlation_id       read via ['reduce_research_audit:record']
  seat_correlation_id      read via ['reduce_research_audit:record']
  sequence                 read via ['_validated_records:record']
  source_reference_hash    read via ['reduce_research_audit:record']
  terminal_status          read via ['reduce_research_audit:record']
derived only: []
pinned only:  []
equal: True
== self-test: renamed
derived only: []
pinned only:  []
equal: True
== self-test: helper
derived only: []
pinned only:  []
equal: True
== self-test: dropped
derived only: []
pinned only:  ['seat_correlation_id']
equal: False
```

### `meta_log_probe.py` (rev 4)

Usage as `reader.py` (`probe_gate_audit.py` saved as `probe.py` beside it).
Run on `wip/296-code` @ `6ab3db9` and with `PYTHONPATH=<git archive 959d4d4 src>`:

````python
"""Pre-existing (main too): an undeclared `meta` on a scoped invoke reaches
InvokeInput by alias, and its pydantic error, which echoes the value, is logged."""
import asyncio, io, logging, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from probe import ctx, policy, CORR
from mcp.types import CallToolRequestParams
from pmcp.server import GatewayServer
buf = io.StringIO(); h = logging.StreamHandler(buf); logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)
async def main():
    tmp = Path(tempfile.mkdtemp())
    srv = GatewayServer(policy_path=policy(tmp / "p.json"), audit_jsonl=tmp / "a.jsonl")
    await srv._handle_call_tool(ctx(), CallToolRequestParams(name="gateway.invoke", arguments={"tool_id": "firecrawl::web_search", **CORR, "meta": "caller_chosen_meta_value"}))
    await srv.shutdown()
    print("logged:", "caller_chosen_meta_value" in buf.getvalue(), "| audit:", "caller_chosen_meta_value" in (tmp / "a.jsonl").read_text())
asyncio.run(main())
````

```text
logged: True | audit: False      # patched
logged: True | audit: False      # main 959d4d4
```

### `survey.py`

````python
import json
from pmcp.tools.handlers import get_gateway_tool_definitions
def walk(s, path, out):
    if isinstance(s, dict):
        for k in ("additionalProperties","patternProperties","propertyNames","$ref","$defs","items","prefixItems","unevaluatedProperties","dependentSchemas","if","contains"):
            if k in s: out.append((".".join(path), k, json.dumps(s[k])[:60]))
        for k,v in s.items(): walk(v, path+[k], out)
    elif isinstance(s, list):
        for i,v in enumerate(s): walk(v, path+[str(i)], out)
tools = get_gateway_tool_definitions()
print(len(tools))
for t in tools:
    out=[]; walk(t.input_schema, [], out)
    for o in out: print(t.name, *o)
````

### `mutants.py`

````python
"""Mutation run for Consiliency/pmcp#296, rev 4: rev 1's 14 (M1-M13, M4b), the
rev 1 board seat's X1-X7, rev 2's N1-N8, the rev 2 board seat's Y1, Y3, Y4, Y6,
Y9 and the rev 3 board seat's Z1-Z6. Each mutant is one exact-string
replacement; the file is restored from a saved copy after every run."""
import shutil, subprocess, sys
from pathlib import Path

SERVER = Path("src/pmcp/server.py")
AUDIT = Path("src/pmcp/scoped_advisor_audit.py")
MUTANTS = [
    ("M1 policy not judged before the gate", SERVER,
     "        if tool is not None and allowed:\n", "        if tool is not None:\n"),
    ("M2 call_tool re-reads the policy verdict", SERVER,
     "                if not allowed:\n",
     "                if not self._policy_manager.is_gateway_tool_allowed(name):\n"),
    ("M3 relative .path instead of .absolute_path", AUDIT,
     "    for segment in error.absolute_path:\n", "    for segment in error.path:\n"),
    ("M4 caller keys not checked against declared names", AUDIT,
     "type(segment) is str and segment in declared", "type(segment) is str"),
    ("M4b str subclass accepted by isinstance", AUDIT,
     "type(segment) is str and segment in declared", "isinstance(segment, str) and segment in declared"),
    ("M5 int segment kept without a list container", AUDIT,
     "if isinstance(node, list) and type(segment) is int:", "if type(segment) is int:"),
    ("M6 validator keyword not bounded", AUDIT,
     "    return validator if isinstance(validator, str) and validator in keywords else None\n",
     "    return validator\n"),
    ("M7 gate rejection not recorded", SERVER,
     "                    if self._scoped_advisor_audit is not None:\n",
     "                    if False:\n"),
    ("M8 dead sink not caught at the gate", SERVER,
     "                except ScopedAdvisorAuditError:\n                    logger.error(\"Scoped advisor audit channel failed\")\n                    return CallToolResult(\n",
     "                except KeyError:\n                    logger.error(\"Scoped advisor audit channel failed\")\n                    return CallToolResult(\n"),
    ("M9 correlation copied from unvalidated arguments", AUDIT,
     "                \"event\": REJECTION_EVENT,\n",
     "                \"event\": REJECTION_EVENT,\n                \"run_correlation_id\": arguments.get(\"run_correlation_id\"),\n"),
    ("M10 jsonschema message recorded", AUDIT,
     "                \"rejected_argument_validator\": validator,\n",
     "                \"rejected_argument_validator\": validator,\n                \"rejected_argument_message\": error.message,\n"),
    ("M11 terminal_status failure", AUDIT,
     "INVALID_ARGUMENTS_STATUS = \"invalid_arguments\"\n", "INVALID_ARGUMENTS_STATUS = \"failure\"\n"),
    ("M13 rejection written as an audit.invocation", AUDIT,
     "REJECTION_EVENT = \"audit.rejection\"\n", "REJECTION_EVENT = \"audit.invocation\"\n"),
    ("M12 declared names from the top level only", AUDIT,
     "            for child in node.values():\n                collect(child)\n        elif isinstance(node, list):\n            for child in node:\n                collect(child)\n\n    collect(schema)\n",
     "        elif isinstance(node, list):\n            pass\n\n    collect(schema)\n"),
    # --- the rev 1 board seat's mutants (claude seat, review of f24ede9) ---
    ("X1 dead-sink branch logs the ValidationError", SERVER,
     "                except ScopedAdvisorAuditError:\n                    logger.error(\"Scoped advisor audit channel failed\")\n                    return CallToolResult(\n",
     "                except ScopedAdvisorAuditError:\n                    logger.error(f\"Scoped advisor audit channel failed: {e}\")\n                    return CallToolResult(\n"),
    ("X2 caller key kept when it looks like an identifier", AUDIT,
     "type(segment) is str and segment in declared",
     "type(segment) is str and (segment in declared or segment.isidentifier())"),
    ("X3 validator kept when it looks like an identifier", AUDIT,
     "    return validator if isinstance(validator, str) and validator in keywords else None\n",
     "    return validator if isinstance(validator, str) and (validator in keywords or validator.isidentifier()) else None\n"),
    ("X4 gateway_tool not bounded to the scoped set", AUDIT,
     "                \"gateway_tool\": gateway_tool\n                if gateway_tool in _SCOPED_GATEWAY_TOOLS\n                else None,\n",
     "                \"gateway_tool\": gateway_tool,\n"),
    ("X5 declared names = every dict key in the schema", AUDIT,
     "            properties = node.get(\"properties\")\n            if isinstance(properties, dict):\n                names.update(key for key in properties if isinstance(key, str))\n",
     "            names.update(key for key in node if isinstance(key, str))\n"),
    ("X6 gate logs the rejected arguments at debug", SERVER,
     "                try:\n                    if self._scoped_advisor_audit is not None:\n",
     "                logger.debug(f\"gate rejected {arguments}\")\n                try:\n                    if self._scoped_advisor_audit is not None:\n"),
    ("X7 caller key kept when shorter than 16 chars", AUDIT,
     "type(segment) is str and segment in declared",
     "type(segment) is str and (segment in declared or len(segment) < 16)"),
    # --- rev 2 ---
    ("N1 denied arm records the caller's arguments (rev 1; board B1)", SERVER,
     "                        arguments=audited_arguments,\n                        result=None,\n",
     "                        arguments=arguments,\n                        result=None,\n"),
    ("N2 denied arm digests the payload that echoes the caller's name", SERVER,
     "                        result=None,\n", "                        result=payload,\n"),
    ("N3 gated record reads undeclared keys", SERVER,
     "key: value for key, value in arguments.items() if key in declared",
     "key: value for key, value in arguments.items()"),
    ("N4 audit names the tool by the caller's string", SERVER,
     "        audited_name = tool.name if tool is not None else None\n",
     "        audited_name = name\n"),
    ("N5 except arm records ungated arguments", SERVER,
     "                        arguments=audited_arguments,\n                        result={\"error_type\": type(e).__name__},\n",
     "                        arguments=arguments,\n                        result={\"error_type\": type(e).__name__},\n"),
    ("N6 an exception describing the rejection escapes", AUDIT,
     "        except Exception:\n            raise ScopedAdvisorAuditError(\n",
     "        except KeyError:\n            raise ScopedAdvisorAuditError(\n"),
    ("N7 the describing exception is chained", AUDIT,
     "                \"scoped advisor audit could not describe a rejected call\"\n            ) from None\n",
     "                \"scoped advisor audit could not describe a rejected call\"\n            )\n"),
    ("N8 post-dispatch record reads the caller's arguments", SERVER,
     "                    arguments=audited_arguments,\n                    result=result,\n",
     "                    arguments=arguments,\n                    result=result,\n"),
    # --- the rev 2 board seat's surviving non-equivalent mutants (review of 2911ff5) ---
    ("Y1 declared filter drops evidence_label_digest", SERVER,
     "key: value for key, value in arguments.items() if key in declared\n",
     "key: value for key, value in arguments.items() if key in declared and key != \"evidence_label_digest\"\n"),
    ("Y3 declared filter drops invoke's arguments", SERVER,
     "key: value for key, value in arguments.items() if key in declared\n",
     "key: value for key, value in arguments.items() if key in declared and key != \"arguments\"\n"),
    ("Y4 declared filter applied before the gate", SERVER,
     "        audited_arguments: dict[str, Any] | None = None\n",
     "        audited_arguments: dict[str, Any] | None = (\n            {k: v for k, v in arguments.items() if k in (tool.input_schema.get(\"properties\") or {})}\n            if tool is not None else None\n        )\n"),
    ("Y6 E16 catches only ValueError", AUDIT,
     "        except Exception:\n            raise ScopedAdvisorAuditError(\n",
     "        except ValueError:\n            raise ScopedAdvisorAuditError(\n"),
    ("Y9 except arm records full gate-passed arguments", SERVER,
     "                        terminal_status=failure_status,\n                        arguments=audited_arguments,\n",
     "                        terminal_status=failure_status,\n                        arguments=arguments if audited_arguments is not None else None,\n"),
    # --- the rev 3 board seat's mutants (review of 6343ad8, zmutants.py) ---
    ("Z1 filter applied only to gateway.invoke", SERVER,
     "            audited_arguments = {\n                key: value for key, value in arguments.items() if key in declared\n            }\n",
     "            audited_arguments = {\n                key: value for key, value in arguments.items() if key in declared\n            } if tool.name == \"gateway.invoke\" else dict(arguments)\n"),
    ("Z2 success arm falls back to raw arguments when the filter leaves nothing", SERVER,
     "                    gateway_tool=audited_name,\n                    terminal_status=terminal_status,\n                    arguments=audited_arguments,\n",
     "                    gateway_tool=audited_name,\n                    terminal_status=terminal_status,\n                    arguments=audited_arguments or arguments,\n"),
    ("Z3 underscore-prefixed undeclared keys pass the filter", SERVER,
     "key: value for key, value in arguments.items() if key in declared\n",
     "key: value for key, value in arguments.items() if key in declared or key.startswith(\"_\")\n"),
    ("Z4 dict-valued undeclared keys pass the filter", SERVER,
     "key: value for key, value in arguments.items() if key in declared\n",
     "key: value for key, value in arguments.items() if key in declared or isinstance(value, dict)\n"),
    ("Z5 unregistered blocked name dispatched instead of denied", SERVER,
     "                if not allowed:\n", "                if not allowed and tool is not None:\n"),
    ("Z6 filter keys compared case-insensitively", SERVER,
     "key: value for key, value in arguments.items() if key in declared\n",
     "key: value for key, value in arguments.items() if key.lower() in {d.lower() for d in declared}\n"),
]
scratch = Path(sys.argv[1])
saved = {p: scratch / (p.name + ".orig") for p in (SERVER, AUDIT)}
for p, s in saved.items():
    shutil.copyfile(p, s)
killed = 0
try:
    for label, path, old, new in MUTANTS:
        text = saved[path].read_text()
        assert text.count(old) == 1, f"{label}: anchor found {text.count(old)} times"
        path.write_text(text.replace(old, new))
        r = subprocess.run(["uv", "run", "pytest", "tests/test_scoped_advisor_audit.py",
                            "-p", "no:cacheprovider", "--cov-fail-under=0", "-q"],
                           capture_output=True, text=True)
        failed = sorted({l.split(" ")[1].split("::")[1].split("[")[0] for l in r.stdout.splitlines() if l.startswith("FAILED ")})
        tail = [l for l in r.stdout.splitlines() if " passed" in l or " failed" in l][-1:]
        status = "KILLED" if r.returncode != 0 else "SURVIVED"
        killed += r.returncode != 0
        print(f"{label}: {status} | {tail[0] if tail else r.stdout[-200:]} | {', '.join(failed)}")
        shutil.copyfile(saved[path], path)
finally:
    for p, s in saved.items():
        shutil.copyfile(s, p)
print(f"{killed}/{len(MUTANTS)} killed")
````
