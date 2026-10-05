# Detailed plan: describe validation errors from their structure, never their value — everywhere pmcp turns an exception into text

> **Revision 19 (2026-10-05), on main `6edf8a4`.** Consiliency/pmcp#297, the
> prerequisite for piece B (`extra="forbid"`) of Consiliency/pmcp#236. The
> change is **embedded, not described**. The 47 blocks under *Verbatim
> bodies* are `git apply` patches against `origin/main` @ `6edf8a4`. They are
> byte-identical to the verified code on the branch `wip/297-code` @
> `fc88ea8`. *Embedding proof* extracts them from this file and applies them
> on a fresh `6edf8a4`, then compares every file.
>
> **What rev 19 changes:** it answers round 17 on Consiliency/pmcp#314 @
> `40e2ba4`: claude DISAGREE (F001 blocking, F002, N1–N3), grok DISAGREE
> (F001 blocking), codex PARTIALLY AGREE, gemini AGREE.
> - pmcp's own refusals are raised after their handler and chain nothing,
>   so the operator sees them whole. Rev 18's rule is unchanged (*Rev 19*).
> - Exception-group members are bound by the grid, a binding test and a
>   mutant.
> - On `/mcp`, the SDK's rejection of an unparseable or invalid envelope
>   no longer quotes the request (grok F001; claude N1).
> - The base is unchanged (`6edf8a4`). Main is now `23edd92`:
>   Consiliency/pmcp#364 (docs) and Consiliency/pmcp#371 (nullable
>   arguments as `anyOf`, which touches `server.py`, `tools/schema.py`
>   and the schema fixture). The 47 patches pass
>   `git apply --unidiff-zero --check` there, but are not tested there;
>   re-embedding on it is the next merge.

## History (revs 1–18)

Each revision answered the previous board. The full text is in the plan at
that sha, at `.consiliency/plans/detailed-297-validation-echo-20260928-2127.md`.
The line ranges are that file's.

| Rev | Plan | Board findings it answers | Design |
|---|---|---|---|
| 1 | `c3a604a` | — | 168–305 |
| 2, 2.1 | `752eee8`, `5a67a08` | rev 1: 61–75 | 347–574, 350–580 |
| 3 | `4b5f1d4` | revs 1–2: 84–108 | 420–699 |
| 4 | `a0508da` | revs 1–3: 103–137 | 449–781 |
| 5 | `91562e6` | revs 1–4: 117–160 | 472–825 |
| 6 | `8d03712` | revs 1–5: 132–187 | 437–837 |
| 7 | `4d4b186` | rev 6: 57–75 | 171–624 |
| 8 | `4bafa47` | revs 6–7: 103–132 | 228–754 |
| 9 | `a449dd9` | rev 8: 91–129 | 225–765 |
| 10 | `b7eacbe` | rev 9: 102–136 | 211–720 |
| 11 | `97e6973` | rev 10: 96–129 | 202–604 |
| 12 | `48b7a89` | rev 11: 98–123 | 196–592 (the full design §1–§14) |
| 13 | `8b45ddd` | rev 12: 39–68 | 96–214 |
| 14 | `440d170` | rev 13: 29–58 | 86–139 |
| 15 | `e6c248f` | rev 14: 30–90 | 118–198 (§12, §14, §15) |
| 16 | `360fe3e` | rev 15 summary: 48–59; merge of `2adcd9a` and Consiliency/pmcp#348's auth code: 60–85 | 95–114 |
| 17 | `0dc22a4` | round 15: 51–58; merge of `6edf8a4`: 97–113 | 59–96, 114–121 (§15) |
| 18 | `40e2ba4` | round 16: 55–63 | 64–122 (the registry and chain rule) |

The code for revs 1–17 is at `19dac95`, `929f693`, `026aadc`, `ee644a9`,
`1824a09`, `9b24daa`, `dd3f707`, `2d9e736`, `8d33b49`, `6078419`,
`46c4904`, `0a93265`, `ebcf4fc`, `06a9e01`, `67bd04d`, `403a83a` (rev 16),
`18824c1` (rev 17) and `b34717e` (rev 18, on origin). Rev 17 before the
merge of `6edf8a4` was `9e5cb57`.

## Rev 19: pmcp's own refusals keep their words; the SDK's /mcp rejections are value-free; group members bound

**The rule this builds on (rev 18; full text `40e2ba4` 53–122).**
- One registry, `_value_bearing_types()`, names the value-bearing errors:
  pydantic's and jsonschema's `ValidationError`, jsonschema's
  `SchemaError`, and every parser's error. Its only exemption is
  `_value_free_types()` = `pmcp.parsing.ParseError`, which is value-free by
  construction and chains nothing.
- An exception whose chain holds a registered error renders as
  `<its class>: <that error's description>`, never its own message. The
  chain is `__cause__` and `__context__` (suppressed or not, at any depth)
  plus exception-group members. A third-party or synthetic wrapper is
  therefore withheld whatever built its text.

**Round 17.** Claude: DISAGREE. The seat confirmed that the 47 patches
reproduce `b34717e` and that the declared residuals cannot be reached.
- **F001 (BLOCKING):** rev 18 also withheld pmcp's own wrappers. These are
  built from `exception_text` and the operator's path, and they were raised
  inside a validation error's handler. On the real CLI, an invalid
  `~/.claude/gateway-policy.yaml` printed only
  `Fatal error: ValueError: 1 validation error for GatewayPolicy: …`. The
  file and the fail-closed refusal (Consiliency/pmcp#202) were lost, and
  `--policy` lost its file the same way.
- **F002:** nothing bound the group branch of `_chain`.
- **N1–N3:** see below, *Non-goals and unverified* and *How to apply*.

Gemini: AGREE. Codex: PARTIALLY AGREE, with nothing blocking; all 47
patches match `b34717e`. Grok: DISAGREE.
- **Grok F001 (BLOCKING), the same path as claude's N1:** a POST to `/mcp`
  whose envelope the SDK rejects (`jsonrpc: "1.0"`) got HTTP 400
  `Validation error: … input_value='<the caller's argument>'`.
- The coordinator ruled that this is fixed, not re-listed: proxies and
  client logs record 4xx bodies.

**The construction rule (rev 19).** A pmcp-authored description is raised
after its handler, so it chains nothing.
- The handler binds `failure = exception_text(e)`, and the refusal is
  raised after the `try` statement, with no `from`. This is how
  `pmcp.parsing` already raises `ParseError`.
- Sites: `policy.py` (4: explicit read, explicit parse, explicit schema,
  discovered-policy refusal), `package_approvals.py` (3) and
  `trust_store.py` (2).
- Rev 18's rule is unchanged. No wrapper message is read on trust, and
  third-party wrappers stay withheld. That includes the three rev 18
  re-pins (`RuntimeError('boom')`, `OSError("disk gone")`), which are
  third-party wrappers and stay as they are.
- `test_no_pmcp_description_is_raised_inside_a_handler` enforces the rule
  over `src/pmcp`. It flags a `raise` inside an `except` body whose
  exception holds a renderer's output, whether built in the `raise` or
  bound to a name in the same handler. Nested functions are skipped. Its
  self-test covers 8 spellings. On `b34717e` it finds exactly the 9 sites.

**The SDK's `/mcp` rejections (rev 19).** The MCP SDK's streamable-HTTP
transport answers a request it cannot accept before any pmcp handler
runs. Every non-literal message or `data` its server-transport modules
can put in a rejection was enumerated: 21 sites in 4 of the 5 scanned
modules (`transport_security` has none). Three are
built from the request:
- `Parse error: {str(e)}`;
- `Validation error: {str(e)}`, pydantic's text with every `input_value`;
- an unsupported protocol version's `data.requested`.

The other 18 are plumbing, or are built from SDK constants and the tool's
own schema.
- `handle_mcp`'s send wrapper holds every JSON response the SDK sends
  with a 4xx/5xx status and passes it through `value_free_rejection`.
- A parse or envelope rejection (`id: null`) now reads `Parse error:` or
  `Validation error:` followed by pmcp's structural description of the
  body. pmcp re-reads the body it already holds through `load_json` and
  the SDK's own `jsonrpc_message_adapter`, and renders the result with
  `exception_text`.
- `requested` is returned only when it is a protocol revision
  (`YYYY-MM-DD`); otherwise it is `""`.
- Every other body, including a handler's error, which carries its id,
  is forwarded unchanged.
- `test_every_sdk_rejection_message_is_reviewed` pins the 21 sites, exact
  both ways, so an SDK upgrade that adds one fails until it is reviewed.
- The SDK does not log the envelope's validation error. The grid below
  checks the log as well as the response.

**Inventory.** An instrumented full-suite run on `b34717e` logged every
wrapper raised under `src/pmcp` that reached the renderer with a
registered error in its chain: the discovered-policy refusal (`policy.py:438`) and the explicit-policy
schema refusal (`policy.py:435`), each reached twice: once in-process,
once through the CLI. Nothing else did. Rev 18's suite had pinned neither,
so the same run against `b34717e`'s own tests logged nothing.

**Tests.**
- `test_a_startup_refusal_names_the_file_and_the_refusal` runs the real
  entry point over 9 refusal paths. Each must print the exact operator
  line, with the path, the description and, for a discovered policy,
  "Refusing to start", and no form of the sentinel. The paths are:
  - discovered policy: YAML schema, JSON schema, list root;
  - `--policy`: YAML schema, JSON schema, YAML parse, missing file;
  - a trust store that cannot be parsed;
  - the auth JWKS URL.

  The `.mcp.json`/`--config`, manifest and `.env` paths warn and
  continue, and do not raise. Their log lines are strings built with
  `exception_text`, which this rule does not touch.
- The seat's F001 falsifier,
  `test_a_policy_refusal_still_names_the_file_and_the_refusal`, is kept
  as filed.
- F002:
  - the grid's link axis gains `group`: the previous level becomes the
    only member of an exception group whose own message is the form's
    text, raised outside any handler;
  - the seat's binding test,
    `test_a_group_member_value_never_reaches_a_log_or_traceback`, is
    added;
  - the grid is now 120 cases, 2,280 chains.
- `/mcp`: `test_a_rejected_envelope_echoes_nothing_of_the_request`
  covers 15 malformed-envelope shapes on both SDK request paths (the
  handshake path and the per-request-envelope path, which adds the
  unsupported version) × a long and a 3-character sentinel. That is 62
  requests.
  - It asserts a 4xx JSON-RPC error, and no form of the sentinel in the
    body, the headers or any log record at DEBUG.
  - Grok's falsifier runs through the gateway's own app
    (`test_envelope_rejection_does_not_echo_caller_value`). As filed, it
    drove the SDK's transport class directly, which pmcp serves only
    behind this app.
  - A unit test pins what is forwarded unchanged.
- Mutants: M129–M137 (see *Mutation evidence*).

## Design in one line per section (full text: `48b7a89` 196–592, `e6c248f` 118–198)

- **§1–§2:** a rejection reads `<JSON path>: <reason>`. Caller-chosen keys
  show as `*`, and the reason is a fixed phrase filled only from pmcp's
  schema or model.
- **§3–§5:** one rule for the response, the log and the audit
  (`audit.rejection`).
- **§6:** the sweep's axes come from the code.
- **§7–§8:** every exception-to-text sink goes through `exception_text` /
  `safe_exc_info`, pinned by a dataflow-aware static guard. One registry
  (`_value_bearing_types`) names the value-bearing errors. An exception
  that chains one renders as its class and that error's description,
  never its own message (rev 18). A pmcp-authored description is raised
  after its handler and chains nothing, so it is shown whole (rev 19).
- **§9:** a record-factory scrubber, installed on `import pmcp`.
- **§10–§11:** parse errors are described by format, source, position and
  class (`pmcp.parsing`). An unparseable `Origin` port gets a 403.
- **§12:** nothing in a downstream frame that is not JSON-RPC 2.0 is logged
  or acted on (`jsonrpc_envelope_problem`, `_StrictMessageAdapter`). The
  refresh session is bounded.
- **§13:** SDK messages render as structure.
- **§14:** values pmcp rejects by hand are described, not shown.
- **§15:** the task parser is the only task recogniser, consulted only
  on a task call (rev 17).

## Changes

The patches are `git diff 6edf8a4 fc88ea8 -- <file>`: 47 files, +10241 / −645. This is one
concern applied at every sink, past the bounded-plan threshold on purpose.
Per-file accounts are at the shas above. Rev 19 restructures the refusals
in `policy.py`, `package_approvals.py` and `trust_store.py`, and adds
`value_free_rejection` and the send wrapper to `transport/http.py` with
their tests in `test_http_transport.py`. It adds the
construction rule to `test_exception_text_sinks.py`, the CLI sweep and
the seat's falsifier to `test_parse_error_echo.py`, and the group axis
and binding test to `test_argument_error_echo.py`. It escapes five
literals in `test_downstream_frame_echo.py` and extends the `CHANGELOG.md`
sentence. All 47 patches are one `git apply`: no import cycles,
no migration, no config change.

**Size.** The plan is about 507 KB, and the 47 patches are 484 KB of it.
The prose is 19 KB. The rounds before rev 18 are already one table row
each, pointing at their shas. So the 450 KB target could be met only by
cutting tests out of the embedded change. Rev 19 does not do that.

## Verification

On a fresh `6edf8a4` with the patches applied:
- run `uv sync --all-extras -p 3.10`, then ruff check, ruff format
  `--check` and mypy;
- run the eight modules (`test_exception_text_sinks`,
  `test_argument_error_echo`, `test_downstream_frame_echo`,
  `test_log_record_scrubber`, `test_parse_error_echo`,
  `test_scoped_advisor_audit`, `test_gateway_tool_schemas`,
  `test_http_transport`);
- codex's round-16 falsifier is the grid's `jsonschema` × `fstring_r` ×
  cause × depth 1 cell; claude's round-17 F001 and F002 falsifiers are in
  the suite as filed (*Rev 19*);
- run the full suite `-m 'not live and not slow'` with the npm cache
  variables unset.

## Acceptance criteria — measured on `fc88ea8`

- [x] The eight modules are green: `1085 passed in 466.27s (0:07:46)`.
- [x] Red on main `6edf8a4`, with the eight test files from `fc88ea8`
  (`--tb=line`; the errors are a fixture importing `pmcp.argument_errors`):

```text
 172 tests/test_argument_error_echo.py
  99 tests/test_downstream_frame_echo.py
   2 tests/test_exception_text_sinks.py
   3 tests/test_gateway_tool_schemas.py
   9 tests/test_http_transport.py
  80 tests/test_log_record_scrubber.py
 108 tests/test_parse_error_echo.py
   6 tests/test_scoped_advisor_audit.py
422 failed, 606 passed, 57 errors in 212.98s (0:03:32)
```

- [x] Red on rev 18's code `b34717e`, with the same eight files: 13 tests fail, all of them rev 19's own, and nothing else:
- the construction rule;
- the 4 schema-invalid policy refusals of the CLI sweep (the other 5
  paths already printed whole on rev 18);
- the seat's F001 falsifier (2);
- the `/mcp` grid (4), grok's falsifier and the rewrite unit test.

The group cells of the grid and the seat's F002 binding test pass on
rev 18. F002 was a gap in what the tests bind, not a leak.

```text
   1 tests/test_exception_text_sinks.py
   6 tests/test_http_transport.py
   6 tests/test_parse_error_echo.py
13 failed, 1072 passed in 809.19s (0:13:29)
```

- [x] The full suite, with `npm_config_cache`, `npm_config_store_dir` and
  `pnpm_config_store_dir` unset (dev0 is a team host): `9421 passed, 5 skipped, 80 deselected in 1245.59s (0:20:45)`.
- [x] Gates: ruff check: `All checks passed!`; ruff format --check: `187 files already formatted`; mypy: `Success: no issues found in 55 source files`.

## Mutation evidence

`mutants.py` ran on a worktree of `fc88ea8`. The procedure:
- each mutant's anchor must occur exactly once;
- the eight modules run with `-x`;
- a dirty file is refused;
- each file is restored from a saved copy kept under `/var/tmp`, then
  checked with `cmp` and against HEAD's blob by sha-256;
- `git status` after the run: `0` and `0`.

The purposes of M1–M128 are in the history table's plans (M115–M128:
`40e2ba4`). Rev 19 adds M129–M137:
- M129, M130, M132 and M133 each move a pmcp refusal back inside its
  handler;
- M131 turns off `_chain`'s walk over group members;
- M134–M137 forward the SDK's rejections, or one part of them, as
  sent.

```text
127 mutants applied; 125 killed: M1–M20 M22 M24 M26–M40 M42–M45 M48 M54–M58 M60 M65–M91 M93–M137 G1 S5–S8
survived: M23 SDK parse error keeps its message
survived: M25 malformed error message kept
```

`NO_STATIC=1` deselects the sink guard and the helpers-only rule:

```text
127 mutants applied; 121 killed with both sink checks deselected: M1–M18 M24 M26–M34 M36–M40 M42–M45 M48 M54–M58 M60 M65–M91 M93–M137 G1 S5–S8
survived: M19 tasks_get response uses str(e)
survived: M20 tasks_get audit buffer uses str(e)
survived: M22 installer crash message uses raw exc (static guard)
survived: M23 SDK parse error keeps its message
survived: M25 malformed error message kept
survived: M35 CLI refresh logs the raw exception
```

M23 and M25 are equivalent mutants. Their combined partners, M102 and M90,
die in both passes. M19, M20, M22 and M35 die only on the sink guard, by
design.

Rev 19's mutants all die in both passes:
- M129 and M130 die on the CLI sweep.
- M131 dies on the grid's group link.
- M134–M137 die on the `/mcp` grid and the rewrite unit test.
- M132 and M133 die only on the construction rule. Under rev 18's
  rule they are equivalent: a trust-store parse failure is an exempt
  `ParseError`, and `_require_identity_fields` raises a plain
  `ValueError`, so neither refusal loses its text. `NO_STATIC=1`
  deselects the two older static checks, not the construction rule.

Both passes ran on 6 worktrees each, with `PYTHONDONTWRITEBYTECODE=1`.

## Non-goals and unverified

- **Non-goals:**
  - Consiliency/pmcp#315: echoes of accepted values, and operator and
    config echoes, including the redacted refused-URL auth diagnostics.
  - Consiliency/pmcp#328.
  - Accepted downstream data.
  - The request id. JSON-RPC requires a reply to carry it, so a
    per-request-envelope rejection echoes the caller's id.
- **Unverified:**
  - Piece B's `additionalProperties` paths.
  - Repr forms the static rule cannot see (`{x}`, `str(x)`, a local).
  - Copied inputs are traced through one assignment hop only.
  - The SDK adapter swap is by name (`mcp` 2.0.x).
  - An `MCPError` a handler lets escape keeps rev 12's rule: its message
    and `data` are kept unless they carry a rejected error's text or an
    input of 4 or more characters. A shorter input, or a reformatted
    copy, in an `MCPError`'s own message is not caught. pmcp raises no
    `MCPError` from a validation error.
  - jsonschema's `FormatError` and stdlib conversion errors are not
    registered. pmcp uses no `format_checker`, and it converts through
    `pmcp.parsing` (§10).
  - The rule follows the exception chain. A value-bearing error held in
    an unchained wrapper's `args` or attributes is not seen; pmcp builds
    no such wrapper.
  - The construction rule is lexical. A pmcp exception raised in a
    function that runs while a caller handles a registered error is
    still withheld. This shows its class and the description, not its
    message, and leaks nothing. The known case is
    `scoped_advisor_audit.py`'s "could not describe a rejected call",
    raised `from None` while the rejected call's error is handled.
  - asyncio's default handler puts `repr(task)`, which embeds
    `exception=<repr>`, into the message before the record factory runs.
    The rev 18 full suite saw this shape once, with a `RuntimeError` from
    `_drain_outbound`. No pmcp task can end in a registered error: each
    one is retrieved or catches everything (round 17 N2).
  - Header values in the SDK's own HTTP logs. The session manager logs
    a rejected `mcp-session-id` (first 64 characters) at INFO, and
    `transport_security` logs a refused Host or Origin. pmcp refuses a
    bad Origin or Host itself, before the SDK. None of these is a
    validation or parse error.
  - pydantic serialization warnings go to stderr through
    `warnings.showwarning`. pmcp neither assigns to models without
    validation nor captures warnings (round 17 N2).
- **Execution:** effort=low.
  - A panel CR comes before any PR.
  - Commit and PR text says "see Consiliency/pmcp#297", never a closing
    keyword.

## Embedding proof

From **this file**: on a fresh worktree of `6edf8a4`, each of the 47 patches was extracted with the embedded extractor and applied. "Identical" means `cmp`-identical to `wip/297-code@fc88ea8`. The proof was run again on the final file, with this section in it, and printed the same listing.

```text
$ git -C <proof worktree> rev-parse --short HEAD
6edf8a4
x2.py: 25 lines
extractor self-extract: identical
$ git apply --unidiff-zero --check p/*.patch
check: ok
applied
changed paths == the 47 patched files
$ git diff --name-only 6edf8a4 origin/main (23edd92), against the patched files
origin/main 23edd92: 12 changed paths since 6edf8a4, 5 of them patched here
cmp: 47 of 47 files identical
```

## Verbatim bodies

### How to apply (and the extractor)

From a fresh worktree of `origin/main` @ `6edf8a4`:

```bash
PLAN=.consiliency/plans/detailed-297-validation-echo-20260928-2127.md   # branch plan/297-validation-echo
X=<scratch>/extract_plan_block.py   # bootstrap: see *Extractor*
sed -n 's/^### Patch — `\(.*\)`$/\1/p' $PLAN | while read f; do   # the 47 files
  python3 $X $PLAN "### Patch — \`$f\`" "<scratch>/$(echo $f | tr / _).patch"
done
git apply --unidiff-zero --check <scratch>/*.patch && git apply --unidiff-zero <scratch>/*.patch
```

The patches are `git diff -U0 6edf8a4 fc88ea8 -- <file>`. To fit the size
budget, each is cut to plain unified-diff form: there are no `diff --git`,
`index` or `new file mode` lines, and no function context in the hunk
headers. `git apply` reads them the same way; a new file is created with
mode 644, subject to umask. Applying needs `--unidiff-zero`, which is safe
on the exact base. The patches are fenced with four backticks. The test
source is ASCII: rev 19 escaped the five literals in
`test_downstream_frame_echo.py` (round 17 N3), so a bundle that renders
them as escapes still reproduces the file.

#### Extractor

Bootstrap: `awk '/^#### Extractor/{f=1;next} f&&/^```python/{g=1;next} g&&/^```$/{exit} g' $PLAN > $X`.
It then extracts itself byte-identically (`python3 $X $PLAN "#### Extractor" x2.py && cmp $X x2.py`).

```python
"""Extract one verbatim patch from the Consiliency/pmcp#297 plan, byte for byte.

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
```

### Patch — `CHANGELOG.md`

````diff
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -567,0 +568,17 @@
+- **A value pmcp rejects is no longer echoed into a response, a log line, a traceback or an audit record (Consiliency/pmcp#297).** A rejected gateway-tool argument used to come back with jsonschema's or pydantic's message, which carried the value (`'Bearer sk-…' is not of type 'object'`, `input_value=…`), in the response, the log and the scoped audit. Rejections now read `<JSON path>: <reason>`, for example `Input validation error: $.options: must be of type object or null`. The reason is a fixed phrase filled only from the tool's own schema or model, and a key the caller chose shows as `*`. A call rejected by the argument model is audited as an `audit.rejection`. **Wording change:** a client matching jsonschema phrases such as `is not of type` must match the new form.
+
+  The same rule holds wherever pmcp turns an exception into text: tool responses, logs, tracebacks, the audit-event buffer and `gateway.tasks_*` errors. A validation error reads `N validation error(s) for <Model>: $.<path>: <reason>`. An exception that chains a validation or parse error, as its cause, its context or a group member, shows only its class and that error's description, never its own message; pmcp's own refusals (an invalid policy file, a trust store it cannot parse) chain nothing and still name the file and the refusal. A parse error of YAML, JSON, TOML or a timestamp, in config files or downstream data, reports its format, source, position and class, never the offending text. From `import pmcp` on, a log record whose traceback or arguments carry such an error is rewritten at creation. An `Origin` header with a bad port gets a 403, not a 500. On `/mcp` over HTTP, the MCP SDK's rejection of a body that is not JSON or not a JSON-RPC message no longer quotes it: the error says why from its structure (`Validation error: N validation errors for …: $.<path>: <reason>`), and an unsupported protocol version's `requested` is returned only when it is a protocol revision.
+
+  Downstream frames:
+  - A frame that is not JSON-RPC 2.0 is dropped with a value-free DEBUG record and never settles a request. This holds on stdio, SSE and streamable HTTP.
+  - A non-protocol stdout line is logged as one fixed record.
+  - A `-32700` message is replaced with fixed text.
+  - The MCP SDK's DEBUG traffic logs show each message's structure, not its contents.
+  - `params: null` reads as absent, and a notification with `id: null` is delivered.
+  - `pmcp refresh` bounds each request (30 s) and each server (120 s).
+
+  Values pmcp rejects by hand are described by type, not shown: a pagination cursor, a `gateway.cancel` id (now `null` in the response) and `auth_connect` env vars.
+
+  A task hint pmcp drops as unusable (Consiliency/pmcp#298) is neither returned nor counted in any size. pmcp reads task hints only in a call that runs as a task, and in `tasks/*` answers. Any other answer is returned whole, including one naming an already-recorded task id.
+
+  A downstream's own well-formed error message is still shown.
````

### Patch — `README.md`

````diff
--- a/README.md
+++ b/README.md
@@ -1760,3 +1760,5 @@
-is recorded as a separate `audit.rejection` event (`terminal_status:
-"invalid_arguments"`) carrying only the tool, the failing JSON path
-(caller-chosen keys shown as `null`) and the failing schema keyword. Only
+or its argument model is recorded as a separate `audit.rejection` event
+(`terminal_status: "invalid_arguments"`) carrying only the tool, the failing JSON
+path (caller-chosen keys shown as `null`) and the failing schema keyword (`null`
+for a model rejection). The rejection the caller sees, and the log line, name the
+same path and a reason taken from the schema, never the rejected value. Only
````

### Patch — `src/pmcp/__init__.py`

````diff
--- a/src/pmcp/__init__.py
+++ b/src/pmcp/__init__.py
@@ -2,0 +3,2 @@
+from pmcp.argument_errors import install_log_scrubber as _install_log_scrubber
+
@@ -3,0 +6,7 @@
+
+# Every entry point imports this package first -- the gateway, the `pmcp`
+# CLI (`pmcp refresh` talks to downstreams through the MCP SDK's client),
+# `python -m pmcp` and any embedder -- so the log-record scrubber is in place
+# before any library can log a rejected value (Consiliency/pmcp#297).
+# Idempotent; `pmcp.client.manager` and `GatewayServer` call it too.
+_install_log_scrubber()
````

### Patch — `src/pmcp/argument_errors.py`

````diff
--- /dev/null
+++ b/src/pmcp/argument_errors.py
@@ -0,0 +1,1412 @@
+"""Describe a rejected gateway-tool argument without the value that failed.
+
+A gateway tool's arguments are checked twice: by the advertised JSON Schema
+(``GatewayServer._handle_call_tool``'s gate) and by the tool's pydantic model
+(``pmcp.types.*Input``). The text both libraries render for a failure embeds
+the rejected value -- jsonschema's ``message`` (``'sk-...' is not of type
+'object'``), pydantic's ``input_value=...`` and a custom validator's own
+message -- and it went verbatim into the tool response and the gateway log
+(Consiliency/pmcp#297). A caller can put a secret in any argument, so that
+text may carry one.
+
+These functions render a rejection from its *structure* instead, never from
+its text:
+
+- **where**: the failing location, as a JSON path (``$.options.timeout_ms``).
+  A segment survives only as a list index or as a name an argument model or
+  schema declares; any other key was chosen by the caller and may itself be a
+  secret, so it becomes ``*``.
+- **why**: a fixed phrase for the failing keyword (jsonschema) or error type
+  (pydantic), filled only from the constraint the gateway's own schema or
+  model defines (``must be at most 128 characters``). An unknown keyword or
+  type renders as ``is invalid``.
+
+Neither reads a value: not jsonschema's ``message``, ``instance``,
+``validator_value``, ``context`` or ``cause``, and not pydantic's ``msg``,
+``input`` or ``ctx`` (a constraint is read from the gateway's own schema, so
+a custom error that reuses a pydantic type cannot smuggle a value in through
+its context).
+
+The same rule covers every other place pmcp turns an exception into text
+(Consiliency/pmcp#297, rev 2): :func:`exception_text` is ``str(error)``
+unless the error is, or embeds the text of, a validation error, and
+:func:`safe_exc_info` withholds a traceback whose chain holds one.
+``tests/test_argument_error_echo.py`` checks every ``except`` in ``src/pmcp``
+(bar the CLI) that can catch one renders it only through these.
+"""
+
+from __future__ import annotations
+
+import json
+import logging
+import sys
+import traceback
+from collections.abc import Iterable, Iterator
+from types import ModuleType
+from typing import Any
+
+import jsonschema
+from pydantic import ValidationError
+from pydantic_core import PydanticCustomError
+
+#: Stands in for a path segment the caller chose.
+REDACTED_SEGMENT = "*"
+
+#: What a rejection reads as if describing it fails (an in-process mapping
+#: whose lookup raises, say); the exception itself is never rendered.
+_UNDESCRIBED = "$: is invalid"
+
+#: At most this many pydantic errors are described; the rest are counted.
+_MAX_MODEL_ERRORS = 5
+
+# --- pmcp's own validator errors -------------------------------------------
+# A custom validator raises one of these instead of ``ValueError(...)``: its
+# type selects a fixed message below, so no validator can interpolate the
+# value it rejects into what the caller or the log sees.
+
+CORRELATION_ID_CHARSET = "correlation_id_charset"
+SCOPED_CORRELATION_INCOMPLETE = "scoped_correlation_incomplete"
+PACKAGE_NAME_INVALID = "package_name_invalid"
+
+_PMCP_MESSAGES: dict[str, str] = {
+    CORRELATION_ID_CHARSET: "correlation IDs may contain only alphanumerics and ._:-",
+    SCOPED_CORRELATION_INCOMPLETE: (
+        "scoped advisor correlation fields must be supplied together"
+    ),
+    PACKAGE_NAME_INVALID: (
+        "package must be a valid npm/pypi identifier (no leading dash, "
+        "whitespace, path separators, or shell metacharacters)"
+    ),
+}
+
+
+def argument_error(error_type: str) -> PydanticCustomError:
+    """The error a pmcp validator raises for ``error_type`` (a constant above)."""
+    return PydanticCustomError(error_type, _PMCP_MESSAGES[error_type])
+
+
+# --- pydantic ----------------------------------------------------------------
+
+#: Error types whose phrase needs no constraint.
+_FIXED_PHRASES: dict[str, str] = {
+    "missing": "is required",
+    "extra_forbidden": "is not an accepted argument",
+    "string_type": "must be a string",
+    "int_type": "must be an integer",
+    "int_parsing": "must be an integer",
+    "int_from_float": "must be an integer",
+    "float_type": "must be a number",
+    "float_parsing": "must be a number",
+    "bool_type": "must be a boolean",
+    "bool_parsing": "must be a boolean",
+    "dict_type": "must be an object",
+    "model_type": "must be an object",
+    "model_attributes_type": "must be an object",
+    "list_type": "must be an array",
+    **_PMCP_MESSAGES,
+}
+
+#: Error types whose phrase names a constraint: ``(the JSON Schema keyword
+#: that holds it in the gateway's own schema, the phrase with it, the phrase
+#: without it)``. The constraint is read from the schema node at the error's
+#: location, never from pydantic's ``ctx`` (Consiliency/pmcp#297 rev 2, N2).
+_CONSTRAINED_PHRASES: dict[str, tuple[str, str, str]] = {
+    "string_too_short": ("minLength", "must be at least {characters}", "is too short"),
+    "string_too_long": ("maxLength", "must be at most {characters}", "is too long"),
+    "string_pattern_mismatch": (
+        "pattern",
+        "must match the pattern {text}",
+        "does not match the required pattern",
+    ),
+    "too_short": ("minItems", "must have at least {items}", "has too few items"),
+    "too_long": ("maxItems", "must have at most {items}", "has too many items"),
+    "literal_error": ("enum", "must be one of {json}", "is not an allowed value"),
+    "enum": ("enum", "must be one of {json}", "is not an allowed value"),
+    "greater_than": (
+        "exclusiveMinimum",
+        "must be greater than {number}",
+        "is too small",
+    ),
+    "greater_than_equal": (
+        "minimum",
+        "must be greater than or equal to {number}",
+        "is too small",
+    ),
+    "less_than": ("exclusiveMaximum", "must be less than {number}", "is too large"),
+    "less_than_equal": (
+        "maximum",
+        "must be less than or equal to {number}",
+        "is too large",
+    ),
+}
+
+
+#: `_declared_names` for a given set of loaded modules (its key).
+_declared_cache: tuple[int, frozenset[str]] | None = None
+
+
+def _declared_names() -> frozenset[str]:
+    """Every field name and alias of every pydantic model pmcp defines.
+
+    Written by pmcp's authors, never by a caller or a downstream server, so a
+    location segment equal to one discloses nothing pmcp's own source does not.
+    Read from the ``pmcp.*`` modules' namespaces, and recomputed only when a
+    module has been imported since.
+    """
+    global _declared_cache
+    from pydantic import BaseModel
+
+    import pmcp.types  # noqa: F401 -- the argument models, at least
+
+    key = len(sys.modules)
+    if _declared_cache is not None and _declared_cache[0] == key:
+        return _declared_cache[1]
+    names: set[str] = set()
+    for module_name, module in list(sys.modules.items()):
+        if module is None or not module_name.startswith("pmcp."):
+            continue
+        for value in list(vars(module).values()):
+            if (
+                isinstance(value, type)
+                and issubclass(value, BaseModel)
+                and value.__module__ == module_name
+            ):
+                for name, field in value.model_fields.items():
+                    names.add(name)
+                    if isinstance(field.alias, str):
+                        names.add(field.alias)
+    _declared_cache = (key, frozenset(names))
+    return _declared_cache[1]
+
+
+def _render_path(segments: Iterable[str | int | None]) -> str:
+    path = "$"
+    for segment in segments:
+        if segment is None:
+            path += f".{REDACTED_SEGMENT}"
+        elif isinstance(segment, int):
+            path += f"[{segment}]"
+        else:
+            path += f".{segment}"
+    return path
+
+
+def _model_error_path(
+    loc: tuple[Any, ...], arguments: Any, declared: frozenset[str]
+) -> list[str | int | None]:
+    """``loc`` with every caller-chosen key redacted; reads only container
+    types from ``arguments`` (to tell a list index from an ``int`` key)."""
+    path: list[str | int | None] = []
+    node = arguments
+    for segment in loc:
+        if isinstance(node, list) and type(segment) is int:
+            path.append(segment)
+        elif type(segment) is str and segment in declared:
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
+def model_error_path(
+    error: ValidationError, schema: Any, arguments: Any
+) -> list[str | int | None]:
+    """The first error's location, redacted as :func:`schema_error_path` is."""
+    declared = _declared_names() | declared_property_names(schema)
+    items = error.errors(include_url=False, include_input=False, include_context=False)
+    loc = tuple(items[0]["loc"]) if items else ()
+    return _model_error_path(loc, arguments, declared)
+
+
+def _schema_node_at(schema: Any, loc: tuple[Any, ...]) -> Any:
+    """The node of the gateway's own schema at a pydantic location, or None."""
+    node = schema
+    for segment in loc:
+        if not isinstance(node, dict):
+            return None
+        if type(segment) is int:
+            node = node.get("items")
+        else:
+            properties = node.get("properties")
+            node = properties.get(segment) if isinstance(properties, dict) else None
+    return node if isinstance(node, dict) else None
+
+
+def _constraint_text(kind: str, value: Any) -> str | None:
+    if kind in ("characters", "items"):
+        noun = kind[:-1]
+        return _count(value, noun) if type(value) is int else None
+    if kind == "number":
+        return str(value) if type(value) in (int, float) else None
+    if kind == "text":
+        return value if isinstance(value, str) else None
+    if isinstance(value, list):  # "json": an enum of the schema's literals
+        return json.dumps(value)
+    return None
+
+
+def _model_phrase(error_type: Any, node: Any) -> str:
+    """The phrase for ``error_type``, its constraint read from ``node`` (the
+    gateway's schema at the error's location; ``None`` when there is none)."""
+    if not isinstance(error_type, str):
+        return "is invalid"
+    if error_type in _FIXED_PHRASES:
+        return _FIXED_PHRASES[error_type]
+    if error_type not in _CONSTRAINED_PHRASES:
+        return "is invalid"
+    keyword, with_constraint, without = _CONSTRAINED_PHRASES[error_type]
+    if not isinstance(node, dict) or keyword not in node:
+        return without
+    kind = with_constraint.split("{", 1)[1].split("}", 1)[0]
+    text = _constraint_text(kind, node[keyword])
+    return without if text is None else with_constraint.replace("{" + kind + "}", text)
+
+
+def describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
+    """``$.<loc>: <phrase>`` for each of pydantic's errors, joined by ``; ``."""
+    try:
+        return _describe_model_error(error, schema, arguments)
+    except Exception:
+        return _UNDESCRIBED
+
+
+def _describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
+    declared = _declared_names() | declared_property_names(schema)
+    items = error.errors(include_url=False, include_input=False, include_context=False)
+    parts = []
+    for item in items[:_MAX_MODEL_ERRORS]:
+        loc = tuple(item["loc"])
+        path = _render_path(_model_error_path(loc, arguments, declared))
+        parts.append(
+            f"{path}: {_model_phrase(item['type'], _schema_node_at(schema, loc))}"
+        )
+    if len(items) > _MAX_MODEL_ERRORS:
+        parts.append(f"and {len(items) - _MAX_MODEL_ERRORS} more")
+    return "; ".join(parts)
+
+
+# --- jsonschema --------------------------------------------------------------
+
+
+def declared_property_names(schema: Any) -> frozenset[str]:
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
+def schema_error_path(
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
+    declared = declared_property_names(schema)
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
+def schema_error_keyword(error: jsonschema.ValidationError, schema: Any) -> str | None:
+    """The failing keyword, if it is one the schema's draft defines."""
+    keywords = jsonschema.validators.validator_for(schema).VALIDATORS
+    validator = error.validator
+    return validator if isinstance(validator, str) and validator in keywords else None
+
+
+def _schema_node(schema: Any, schema_path: Iterable[Any]) -> Any:
+    """The node of *our* schema that holds the failing keyword."""
+    node = schema
+    for segment in schema_path:
+        node = node[segment]
+    return node
+
+
+def _count(number: Any, noun: str) -> str:
+    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
+
+
+def _type_names(value: Any) -> str:
+    names = value if isinstance(value, list) else [value]
+    return " or ".join(str(name) for name in names)
+
+
+def _missing_required(
+    required: Any, error: jsonschema.ValidationError, arguments: Any
+) -> str | None:
+    """The first name ``required`` lists that the object lacks. Only the
+    schema's own names are returned; the object is only tested for them."""
+    node = arguments
+    for segment in error.absolute_path:
+        try:
+            node = node[segment]
+        except (KeyError, IndexError, TypeError):
+            return None
+    if not isinstance(node, dict) or not isinstance(required, list):
+        return None
+    for name in required:
+        if isinstance(name, str) and name not in node:
+            return name
+    return None
+
+
+def _schema_phrase(
+    keyword: str | None,
+    error: jsonschema.ValidationError,
+    schema: Any,
+    arguments: Any,
+) -> tuple[str, str | None]:
+    """``(phrase, extra path segment)`` for ``keyword``, read from our schema."""
+    if keyword is None:
+        return "is invalid", None
+    try:
+        node = _schema_node(schema, list(error.absolute_schema_path)[:-1])
+        constraint = node[keyword]
+    except (KeyError, IndexError, TypeError):
+        return "is invalid", None
+    if keyword == "type":
+        return f"must be of type {_type_names(constraint)}", None
+    if keyword == "enum":
+        return f"must be one of {json.dumps(constraint)}", None
+    if keyword == "const":
+        return f"must be {json.dumps(constraint)}", None
+    if keyword == "pattern":
+        return f"must match the pattern {constraint}", None
+    if keyword == "minLength":
+        return f"must be at least {_count(constraint, 'character')}", None
+    if keyword == "maxLength":
+        return f"must be at most {_count(constraint, 'character')}", None
+    if keyword == "minItems":
+        return f"must have at least {_count(constraint, 'item')}", None
+    if keyword == "maxItems":
+        return f"must have at most {_count(constraint, 'item')}", None
+    if keyword == "minimum":
+        return f"must be greater than or equal to {constraint}", None
+    if keyword == "maximum":
+        return f"must be less than or equal to {constraint}", None
+    if keyword == "exclusiveMinimum":
+        return f"must be greater than {constraint}", None
+    if keyword == "exclusiveMaximum":
+        return f"must be less than {constraint}", None
+    if keyword == "required":
+        return "is required", _missing_required(constraint, error, arguments)
+    if keyword in ("additionalProperties", "unevaluatedProperties"):
+        return "has a property that is not accepted", None
+    return f"fails the schema's {keyword} constraint", None
+
+
+def describe_schema_error(
+    error: jsonschema.ValidationError, schema: Any, arguments: Any
+) -> str:
+    """``$.<path>: <phrase>`` for one jsonschema error, from its structure."""
+    try:
+        return _describe_schema_error(error, schema, arguments)
+    except Exception:
+        return _UNDESCRIBED
+
+
+def _describe_schema_error(
+    error: jsonschema.ValidationError, schema: Any, arguments: Any
+) -> str:
+    path = schema_error_path(error, schema, arguments)
+    phrase, missing = _schema_phrase(
+        schema_error_keyword(error, schema), error, schema, arguments
+    )
+    if missing is not None:
+        path = [*path, missing]
+    return f"{_render_path(path)}: {phrase}"
+
+
+def describe_argument_error(
+    error: BaseException, schema: Any, arguments: Any
+) -> str | None:
+    """A value-free description of ``error`` if it is an argument-validation
+    error of either library, checked against ``schema``, else ``None``."""
+    if isinstance(error, ValidationError):
+        return describe_model_error(error, schema, arguments)
+    if isinstance(error, jsonschema.ValidationError):
+        return describe_schema_error(error, schema, arguments)
+    return None
+
+
+# --- any exception pmcp renders ------------------------------------------------
+
+
+def _parse_error_types() -> tuple[type[BaseException], ...]:
+    """The parse errors of every structured-text parser pmcp uses (rev 6).
+
+    Their text can quote what they rejected: PyYAML's ``MarkedYAMLError``
+    renders a snippet of the input around the mark, and a constructor error
+    names the input's tag; ``tomllib``'s message can quote a key. JSON's
+    message is fixed vocabulary, but its ``doc`` holds the input, so it is
+    rendered the same way for one rule. python-dotenv does not raise on bad
+    input (it logs the line number only).
+    """
+    import yaml
+
+    types: list[type[BaseException]] = [yaml.YAMLError, json.JSONDecodeError]
+    try:
+        import tomllib  # Python 3.11+
+
+        types.append(tomllib.TOMLDecodeError)
+    except ImportError:  # pragma: no cover - Python 3.10
+        pass
+    return tuple(types)
+
+
+_PARSE_ERRORS: tuple[type[BaseException], ...] = ()
+
+
+def _is_parse_error(error: BaseException) -> bool:
+    """Whether a value-bearing error is a parser's (and so is described by
+    :func:`_parse_text`). Which errors are value-bearing at all, and the
+    :class:`pmcp.parsing.ParseError` exemption, are the registry's decision
+    alone (:func:`_is_validation_error`, rev 18)."""
+    global _PARSE_ERRORS
+    if not _PARSE_ERRORS:
+        _PARSE_ERRORS = _parse_error_types()
+    return isinstance(error, _PARSE_ERRORS)
+
+
+def _value_bearing_types() -> tuple[type[BaseException], ...]:
+    """The registry of value-bearing exception types (rev 18): every type
+    whose own text, ``repr`` or attributes can carry the input it rejected.
+    It is the one place that decides which exceptions are described from
+    their structure, and which wrappers are never rendered from their own
+    message: pydantic's and jsonschema's validation errors (jsonschema's
+    ``SchemaError`` renders the schema it rejected), and every parser's
+    error (:func:`_parse_error_types`). :data:`_VALUE_FREE_TYPES` is the
+    registry's only exemption."""
+    return (
+        ValidationError,
+        jsonschema.ValidationError,
+        jsonschema.SchemaError,
+        *_parse_error_types(),
+    )
+
+
+_VALUE_BEARING: tuple[type[BaseException], ...] = ()
+
+
+def _value_free_types() -> tuple[type[BaseException], ...]:
+    """Subclasses of registered types whose text is value-free by
+    construction: :class:`pmcp.parsing.ParseError` (rev 7), raised outside
+    the parser's ``except`` so that it chains nothing."""
+    from pmcp.parsing import ParseError
+
+    return (ParseError,)
+
+
+def _is_validation_error(error: BaseException) -> bool:
+    """A value-bearing error: an instance of a registered type
+    (:func:`_value_bearing_types`) that is not exempt."""
+    global _VALUE_BEARING
+    if not _VALUE_BEARING:
+        _VALUE_BEARING = _value_bearing_types()
+    return isinstance(error, _VALUE_BEARING) and not isinstance(
+        error, _value_free_types()
+    )
+
+
+def _parse_text(error: BaseException) -> str:
+    """A parse error without the input: the format, the error's class and,
+    where the parser records it, the line and column (never PyYAML's
+    ``problem``/``context`` text or snippet, JSON's ``msg``/``doc``, or
+    TOML's message)."""
+    kind = "JSON" if isinstance(error, json.JSONDecodeError) else None
+    line = column = None
+    if kind == "JSON":
+        line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
+    elif type(error).__module__.startswith("yaml"):
+        kind = "YAML"
+        mark = getattr(error, "problem_mark", None) or getattr(
+            error, "context_mark", None
+        )
+        if mark is not None:
+            line, column = getattr(mark, "line", None), getattr(mark, "column", None)
+            line = line + 1 if isinstance(line, int) else None
+            column = column + 1 if isinstance(column, int) else None
+    else:
+        kind = "TOML"
+        line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
+    where = (
+        f" at line {line}, column {column}"
+        if isinstance(line, int) and isinstance(column, int)
+        else ""
+    )
+    return f"could not parse {kind} ({type(error).__name__}){where}"
+
+
+def _chain(error: BaseException) -> Iterator[BaseException]:
+    """``error``, its ``__cause__``/``__context__`` chain and every exception
+    in a group, each once."""
+    pending, seen = [error], set()
+    while pending:
+        current = pending.pop()
+        if id(current) in seen:
+            continue
+        seen.add(id(current))
+        yield current
+        for linked in (current.__cause__, current.__context__):
+            if linked is not None:
+                pending.append(linked)
+        members = getattr(current, "exceptions", None)
+        if isinstance(members, (tuple, list)):
+            pending.extend(m for m in members if isinstance(m, BaseException))
+
+
+def _validation_text(error: BaseException) -> str:
+    """A validation error described without the schema that raised it: the
+    path (names pmcp's models declare, list indexes, ``*``) and a phrase
+    without constraints, since the schema is not known here (rev 2, N6).
+    A parse error is described by :func:`_parse_text` (rev 6)."""
+    if _is_parse_error(error):
+        return _parse_text(error)
+    if isinstance(error, ValidationError):
+        count = error.error_count()
+        plural = "" if count == 1 else "s"
+        return (
+            f"{count} validation error{plural} for {error.title}: "
+            f"{describe_model_error(error, None, None)}"
+        )
+    assert isinstance(error, (jsonschema.ValidationError, jsonschema.SchemaError))
+    try:
+        declared = _declared_names()
+        path = [
+            segment
+            if type(segment) is int or (type(segment) is str and segment in declared)
+            else None
+            for segment in error.absolute_path
+        ]
+        keyword = error.validator if isinstance(error.validator, str) else None
+        phrase = (
+            f"fails its {keyword} constraint"
+            if keyword in jsonschema.validators.Draft202012Validator.VALIDATORS
+            else "is invalid"
+        )
+        return f"schema validation error: {_render_path(path)}: {phrase}"
+    except Exception:
+        return f"schema validation error: {_UNDESCRIBED}"
+
+
+def exception_text(error: BaseException) -> str:
+    """``str(error)``, unless ``error``'s chain holds a value-bearing error.
+
+    A value-bearing error (a registered type, :func:`_value_bearing_types`)
+    is described from its structure. An exception whose ``__cause__`` /
+    ``__context__`` chain (or exception group) holds one, at any depth and
+    whether or not the context is suppressed, is never rendered from its own
+    message: it reads ``<its class name>: <that error's description>``.
+    Whatever built the wrapper's message -- ``f"{e}"``, ``{e!r}``,
+    ``format()``, ``%r``, a slice, ``e.message`` or ``e.instance``, a nested
+    wrapper -- nothing of it is shown, so no form of it can carry the value
+    (rev 18; until rev 17 the message was kept unless it contained
+    ``str(e)``, which ``repr(e)`` evades). Every other exception is
+    ``str(error)`` unchanged.
+
+    Text that arrives as a plain string, with no exception chained, is not
+    recognised -- which is why pmcp never builds such a copy
+    (``tests/test_exception_text_sinks.py`` flags the construction site) and
+    replaces the SDK's stringified parse errors where it receives them
+    (``pmcp.client.manager._downstream_error``).
+    """
+    if _is_validation_error(error):
+        return _validation_text(error)
+    linked = _chained_value_bearing(error)
+    if linked is not None:
+        return f"{type(error).__name__}: {_validation_text(linked)}"
+    return str(error)
+
+
+def _chained_value_bearing(error: BaseException) -> BaseException | None:
+    """The first value-bearing error in ``error``'s chain other than
+    ``error`` itself: through ``__cause__`` and ``__context__`` (suppressed
+    or not) and exception-group members, at any depth."""
+    for linked in _chain(error):
+        if linked is not error and _is_validation_error(linked):
+            return linked
+    return None
+
+
+def message_text(message: str, error: BaseException) -> str:
+    """`message` (an error's own message field, such as an `MCPError`'s),
+    unless it embeds the text of a validation or parse error in `error`'s
+    chain; then that error's structural description (rev 11)."""
+    for linked in _chain(error):
+        if _is_validation_error(linked):
+            try:
+                embedded = str(linked)
+            except Exception:
+                embedded = ""
+            if embedded and embedded in message:
+                return _validation_text(linked)
+    return message
+
+
+def _rejected_texts(error: BaseException) -> list[str]:
+    """Texts that would reveal what `error`'s chain rejected: each validation
+    or parse error's own text, and each input value it recorded (pydantic's
+    `input`, jsonschema's `instance`), as text, when it is at least 4
+    characters long."""
+    texts: list[str] = []
+    for linked in _chain(error):
+        if not _is_validation_error(linked):
+            continue
+        try:
+            texts.append(str(linked))
+        except Exception:
+            pass
+        inputs: list[Any] = []
+        if isinstance(linked, ValidationError):
+            try:
+                inputs = [item.get("input") for item in linked.errors()]
+            except Exception:
+                inputs = []
+        elif isinstance(linked, (jsonschema.ValidationError, jsonschema.SchemaError)):
+            inputs = [linked.instance]
+        for value in inputs:
+            # The input whole (as itself, JSON and repr), and every string
+            # inside a container input: a message built from `{'v': '<s>'}`
+            # renders neither form of the whole (rev 13, on Consiliency/
+            # pmcp#298's merge, whose task fields no longer raise).
+            candidates: list[Any] = [value, json.dumps(value, default=str), repr(value)]
+            stack: list[tuple[Any, int]] = [(value, 0)]
+            while stack:
+                item, depth = stack.pop()
+                if depth > 8:
+                    continue
+                if isinstance(item, dict):
+                    for key, inner in item.items():
+                        candidates.append(key)
+                        stack.append((inner, depth + 1))
+                elif isinstance(item, (list, tuple, set)):
+                    stack.extend((inner, depth + 1) for inner in item)
+                elif depth and isinstance(item, str):
+                    candidates.append(item)
+            for text in candidates:
+                if isinstance(text, str) and len(text) >= 4:
+                    texts.append(text)
+    return texts
+
+
+def carries_rejected_value(value: Any, error: BaseException) -> bool:
+    """Whether `value` (an error's message or `data`) carries what `error`'s
+    chain rejected, whole or in part (rev 12): a validation error's text, or
+    any input it recorded, found in the value's text or in any string inside
+    it."""
+    texts = _rejected_texts(error)
+    if not texts:
+        return False
+    haystacks: list[str] = []
+
+    def collect(item: Any, depth: int = 0) -> None:
+        if depth > 8:
+            return
+        if isinstance(item, str):
+            haystacks.append(item)
+        elif isinstance(item, dict):
+            for key, inner in item.items():
+                collect(key, depth + 1)
+                collect(inner, depth + 1)
+        elif isinstance(item, (list, tuple, set)):
+            for inner in item:
+                collect(inner, depth + 1)
+
+    collect(value)
+    try:
+        haystacks.append(json.dumps(value, default=str))
+    except Exception:
+        haystacks.append(str(value))
+    return any(text in hay for text in texts for hay in haystacks)
+
+
+def safe_exc_info(error: BaseException) -> BaseException | None:
+    """``exc_info=`` for a log call: the exception, unless its chain holds a
+    validation error, whose rendered traceback would carry the value."""
+    if any(_is_validation_error(linked) for linked in _chain(error)):
+        return None
+    return error
+
+
+def _qualified_name(kind: type[BaseException]) -> str:
+    """``module.QualName`` as the interpreter prints it (bare for builtins)."""
+    module = getattr(kind, "__module__", None)
+    name = getattr(kind, "__qualname__", kind.__name__)
+    if module in (None, "builtins", "__main__"):
+        return str(name)
+    return f"{module}.{name}"
+
+
+def safe_traceback_text(error: BaseException) -> str:
+    """The formatted traceback. When the chain holds a validation or parse
+    error, every exception in it is rendered as its frames (file, line,
+    source) and ``Type: <text>``, where the text is a value-bearing error's
+    description, a wrapper's description of what it chains (never its own
+    message, rev 18) or else ``str()`` -- the frames never carry an
+    exception's text -- so it stays a usable traceback (rev 6)."""
+    if safe_exc_info(error) is not None:
+        return "".join(
+            traceback.format_exception(type(error), error, error.__traceback__)
+        )
+    parts: list[str] = []
+    seen: set[int] = set()
+
+    def render(current: BaseException) -> None:
+        seen.add(id(current))
+        cause, context = current.__cause__, current.__context__
+        if cause is not None and id(cause) not in seen:
+            render(cause)
+            parts.append(
+                "\nThe above exception was the direct cause of the following "
+                "exception:\n\n"
+            )
+        elif (
+            context is not None
+            and id(context) not in seen
+            and not current.__suppress_context__
+        ):
+            render(context)
+            parts.append(
+                "\nDuring handling of the above exception, another exception "
+                "occurred:\n\n"
+            )
+        if current.__traceback__ is not None:
+            parts.append("Traceback (most recent call last):\n")
+            parts.extend(traceback.format_tb(current.__traceback__))
+        # The class is printed once: a wrapper's line is its qualified name
+        # and the description of what it chains (rev 18).
+        linked = (
+            None if _is_validation_error(current) else _chained_value_bearing(current)
+        )
+        text = exception_text(current) if linked is None else _validation_text(linked)
+        parts.append(f"{_qualified_name(type(current))}: {text}\n")
+
+    render(error)
+    return "".join(parts)
+
+
+# --- every log record, whoever logs it (rev 3, widened in rev 4) ------------
+
+
+# --- JSON-RPC messages rendered as their structure (rev 10) -------------------
+
+_MESSAGE_CLASS_NAMES = (
+    "JSONRPCRequest",
+    "JSONRPCNotification",
+    "JSONRPCResponse",
+    "JSONRPCError",
+)
+_KNOWN_METHODS: frozenset[str] | None = None
+
+
+def _known_methods() -> frozenset[str]:
+    """Every method name the MCP SDK's own types declare (a `Literal` on a
+    model's `method` field). A method outside this set is a downstream's or
+    a caller's string, and is not shown."""
+    global _KNOWN_METHODS
+    if _KNOWN_METHODS is None:
+        import importlib
+        import inspect
+        import pkgutil
+        import typing
+
+        from pydantic import BaseModel
+
+        found: set[str] = set()
+        try:
+            import mcp_types
+
+            for info in pkgutil.walk_packages(mcp_types.__path__, "mcp_types."):
+                module = importlib.import_module(info.name)
+                for value in vars(module).values():
+                    if not (inspect.isclass(value) and issubclass(value, BaseModel)):
+                        continue
+                    field = value.model_fields.get("method")
+                    if (
+                        field is not None
+                        and typing.get_origin(field.annotation) is typing.Literal
+                    ):
+                        found |= {
+                            arg
+                            for arg in typing.get_args(field.annotation)
+                            if isinstance(arg, str)
+                        }
+        except Exception:  # pragma: no cover - no SDK types: show no method
+            found = set()
+        _KNOWN_METHODS = frozenset(found)
+    return _KNOWN_METHODS
+
+
+def describe_value(value: Any) -> str:
+    """A rejected value's structure, never its content (rev 12): an object's
+    key count, an array's length, or a scalar's type. A string is just "a
+    string": its length would follow the value's, which the sweeps' pair
+    differential treats as a channel."""
+    if isinstance(value, str):
+        return "a string"
+    return _shape(value)
+
+
+def _shape(value: Any) -> str:
+    """A value's structure, never its content: an object's key count, an
+    array's length, or a scalar's type."""
+    if isinstance(value, dict):
+        return f"object ({len(value)} key{'' if len(value) == 1 else 's'})"
+    if isinstance(value, (list, tuple)):
+        return f"array ({len(value)} item{'' if len(value) == 1 else 's'})"
+    return "null" if value is None else type(value).__name__
+
+
+def _message_fields(message: Any) -> dict[str, Any] | None:
+    """The JSON-RPC fields of an SDK message object or a dict shaped like
+    one, or None when `message` is neither."""
+    if isinstance(message, dict):
+        if "jsonrpc" in message and any(
+            key in message for key in ("method", "result", "error", "params", "id")
+        ):
+            return message
+        return None
+    if type(message).__name__ in _MESSAGE_CLASS_NAMES and type(
+        message
+    ).__module__.startswith(("mcp", "mcp_types")):
+        return {
+            name: getattr(message, name)
+            for name in ("method", "id", "params", "result", "error")
+            if hasattr(message, name)
+        }
+    return None
+
+
+def describe_jsonrpc_message(message: Any) -> str:
+    """A JSON-RPC message as its structure (Consiliency/pmcp#297, rev 10):
+    the kind, the method when the SDK declares it, the id's type and the
+    shape of `params` / `result` / `error` -- never their content, and never
+    an id's value. Every SDK log line that renders a message, incoming or
+    outgoing, shows this instead of the payload."""
+    fields = _message_fields(message) or {}
+    method = fields.get("method")
+    if method is not None:
+        kind = "request" if fields.get("id") is not None else "notification"
+    elif fields.get("error") is not None:
+        kind = "error"
+    else:
+        kind = "response"
+    parts = []
+    if method is not None:
+        parts.append(
+            f"method {method!r}"
+            if isinstance(method, str) and method in _known_methods()
+            else "an undeclared method"
+        )
+    if fields.get("id") is not None:
+        parts.append(f"id: {_shape(fields['id'])}")
+    for name in ("params", "result"):
+        if fields.get(name) is not None:
+            value = fields[name]
+            dumped = value.model_dump() if hasattr(value, "model_dump") else value
+            parts.append(f"{name}: {_shape(dumped)}")
+    error = fields.get("error")
+    if error is not None:
+        code = getattr(error, "code", None)
+        if code is None and isinstance(error, dict):
+            code = error.get("code")
+        parts.append(f"error code: {_shape(code)}")
+    return f"<JSON-RPC {kind}" + (": " + ", ".join(parts) if parts else "") + ">"
+
+
+def _render_message(self: Any) -> str:
+    try:
+        return describe_jsonrpc_message(self)
+    except Exception:  # a log call must never fail because of the render
+        return "<JSON-RPC message>"
+
+
+def _install_message_rendering() -> None:
+    """Make every rendering of an MCP SDK JSON-RPC message object -- an
+    f-string, `%s`, `%r`, a containing `SessionMessage`'s dataclass repr --
+    its structure. The SDK's transports log each message they receive and
+    send at DEBUG (`mcp/client/sse.py` "Received server message: {message}"
+    and "Sending client message: {session_message}",
+    `mcp/client/streamable_http.py` "SSE message: {message}" and "Sending
+    client message: {message}"): after the envelope is accepted, but before
+    pmcp validates the payload -- and outgoing ones carry the caller's
+    arguments. A preformatted f-string leaves no object for a record
+    scrubber to find, so the object renders itself. Idempotent."""
+    try:
+        import mcp.types as mcp_types_module
+    except Exception:  # pragma: no cover - the SDK is a dependency
+        return
+    for name in _MESSAGE_CLASS_NAMES:
+        cls = getattr(mcp_types_module, name, None)
+        if cls is None or getattr(cls, "pmcp_structural_render", False):
+            continue
+        cls.__repr__ = _render_message  # type: ignore[method-assign]
+        cls.__str__ = _render_message  # type: ignore[method-assign]
+        cls.pmcp_structural_render = True
+    # The transport wrapper and its metadata: `ClientMessageMetadata.headers`
+    # can carry `Mcp-Param-*` headers, which hold tool-argument values
+    # (`mcp/shared/inbound.py`, `x-mcp-header`), and the SDK logs a whole
+    # `SessionMessage` ("Sending client message: {session_message}").
+    try:
+        from mcp.shared import message as session_module
+    except Exception:  # pragma: no cover
+        return
+    for name, render in (
+        ("SessionMessage", _render_session_message),
+        ("ClientMessageMetadata", _render_metadata),
+        ("ServerMessageMetadata", _render_metadata),
+    ):
+        cls = getattr(session_module, name, None)
+        if cls is None or getattr(cls, "pmcp_structural_render", False):
+            continue
+        cls.__repr__ = render  # type: ignore[method-assign]
+        cls.__str__ = render  # type: ignore[method-assign]
+        cls.pmcp_structural_render = True
+
+
+def _render_metadata(self: Any) -> str:
+    """A transport metadata object as its type and header *names*."""
+    try:
+        headers = getattr(self, "headers", None)
+        names = sorted(headers) if isinstance(headers, dict) else []
+        shown = f" headers: {', '.join(names)}" if names else ""
+        return f"<{type(self).__name__}{shown}>"
+    except Exception:  # a log call must never fail because of the render
+        return "<message metadata>"
+
+
+def _render_session_message(self: Any) -> str:
+    try:
+        metadata = getattr(self, "metadata", None)
+        tail = "" if metadata is None else f", {_render_metadata(metadata)}"
+        return (
+            f"SessionMessage({_render_message(getattr(self, 'message', None))}{tail})"
+        )
+    except Exception:  # a log call must never fail because of the render
+        return "SessionMessage(<JSON-RPC message>)"
+
+
+def _scrubbed(value: Any, depth: int = 0) -> Any:
+    """`value` with every exception whose chain holds a validation error
+    replaced by its :func:`exception_text`, and every JSON-RPC message (an
+    SDK object or a dict shaped like one) by
+    :func:`describe_jsonrpc_message`, looking inside tuples, lists, sets and
+    dicts (keys and values)."""
+    if isinstance(value, BaseException):
+        return exception_text(value) if safe_exc_info(value) is None else value
+    if _message_fields(value) is not None:
+        return describe_jsonrpc_message(value)
+    if depth > 8:
+        return value
+    if isinstance(value, tuple):
+        return tuple(_scrubbed(item, depth + 1) for item in value)
+    if isinstance(value, list):
+        return [_scrubbed(item, depth + 1) for item in value]
+    if isinstance(value, (set, frozenset)):
+        return type(value)(_scrubbed(item, depth + 1) for item in value)
+    if isinstance(value, dict):
+        return {
+            _scrubbed(key, depth + 1): _scrubbed(item, depth + 1)
+            for key, item in value.items()
+        }
+    return value
+
+
+def scrub_record(record: logging.LogRecord) -> logging.LogRecord:
+    """Remove a validation error's text from `record`, in place.
+
+    - ``msg`` that is itself such an exception becomes its
+      :func:`exception_text`;
+    - ``args`` -- a tuple, or the mapping of a ``%(name)s`` message -- have
+      every such exception, however nested in containers, replaced;
+    - ``exc_info`` whose chain holds one is dropped, and the message gets
+      the exception's :func:`exception_text` appended, so the record still
+      says what failed.
+
+    ``stack_info`` needs nothing: it renders frames and source lines, never
+    an exception's text. Every other record is returned unchanged.
+    """
+    try:
+        if isinstance(record.msg, BaseException):
+            record.msg = _scrubbed(record.msg)
+        if record.args:
+            if (
+                isinstance(record.args, dict)
+                and _message_fields(record.args) is not None
+            ):
+                # A single mapping argument that is itself a message: keep
+                # `%(name)s` keys working, but with no payload in them.
+                described = describe_jsonrpc_message(record.args)
+                if "%(" in str(record.msg):
+                    record.args = {
+                        key: (
+                            "<omitted>"
+                            if key in ("params", "result", "error", "id")
+                            else value
+                        )
+                        for key, value in record.args.items()
+                    }
+                else:
+                    record.args = (described,)
+            else:
+                record.args = _scrubbed(record.args)
+        error = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
+        if isinstance(error, BaseException) and safe_exc_info(error) is None:
+            try:
+                message = record.getMessage()
+            except Exception:
+                message = str(record.msg)
+            record.msg = f"{message} ({exception_text(error)})"
+            record.args = None
+            record.exc_info = None
+            record.exc_text = None
+    except Exception:
+        pass  # a log call must never fail because of the scrub
+    return record
+
+
+def _scrubbing_factory(previous: Any) -> Any:
+    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
+        return scrub_record(previous(*args, **kwargs))
+
+    factory.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    factory.previous = previous  # type: ignore[attr-defined]
+    return factory
+
+
+# --- rev 13: the JSON-RPC 2.0 envelope (round-12 codex B2) -------------------
+
+
+#: JSON's type names, for a value `json` produced (round-13 claude nit: the
+#: reasons promise JSON type names, not Python's).
+_JSON_TYPE_NAMES = {
+    dict: "object",
+    list: "array",
+    str: "string",
+    int: "number",
+    float: "number",
+    bool: "boolean",
+    type(None): "null",
+}
+
+
+def _type_of(value: Any) -> str:
+    return _JSON_TYPE_NAMES.get(type(value), "value")
+
+
+def _is_request_id(value: Any) -> bool:
+    """MCP's `RequestId`: a string or an integer. Not a bool (`True == 1`
+    would resolve request 1) and not a float (`1.0 == 1` likewise)."""
+    return isinstance(value, (str, int)) and not isinstance(value, bool)
+
+
+def jsonrpc_envelope_problem(frame: Any) -> str | None:
+    """Why `frame` is not a JSON-RPC 2.0 message as MCP defines one, or None.
+
+    Every rule is the specification's, not a list of bad frames
+    (Consiliency/pmcp#297, rev 13): JSON-RPC 2.0 sections 4 and 5, and MCP's
+    `JSONRPCRequest` / `JSONRPCNotification` / `JSONRPCResultResponse` /
+    `JSONRPCErrorResponse`.
+
+    - `jsonrpc` is exactly the string `"2.0"`;
+    - a request has a string `method` and a `RequestId`; a notification has a
+      string `method` and no `id` -- or `id: null`, which main, and the MCP
+      SDK's own parser, read as a notification (rev 15, round-14 grok F004;
+      nothing in it is echoed); `params`, when present, is an object; a
+      request or notification carries neither `result` nor `error`;
+    - a response has an `id` member (a `RequestId`, or null for an error the
+      server could not attribute) and exactly one of `result` and `error`;
+    - `result` is an object (MCP's `Result`);
+    - `error` is an object whose `code` is an integer (not a bool) and whose
+      `message` is a string; `data` is optional and unconstrained.
+
+    Extra members are allowed (neither specification forbids them, and
+    nothing reads them). The reason is value-free: fixed text and JSON type
+    names only, so it can be logged for any frame.
+    """
+    if not isinstance(frame, dict):
+        return f"not a JSON object ({_type_of(frame)})"
+    if frame.get("jsonrpc") != "2.0" or not isinstance(frame.get("jsonrpc"), str):
+        return "the jsonrpc member is not the string 2.0"
+    if "method" in frame:
+        method = frame["method"]
+        if not isinstance(method, str):
+            return f"non-string method ({_type_of(method)})"
+        msg_id = frame.get("id")
+        if msg_id is not None and not _is_request_id(msg_id):
+            return f"id of type {_type_of(msg_id)}"
+        # `params: null` is outside JSON-RPC 2.0, but the SDK's models accept
+        # it and it carries nothing: it reads as absent (round-13 claude N1),
+        # so a `ping` sent that way is still answered.
+        params = frame.get("params")
+        if params is not None and not isinstance(params, dict):
+            return f"params of type {_type_of(params)}"
+        if "result" in frame or "error" in frame:
+            return "a request or notification carrying result or error"
+        return None
+    if "id" not in frame:
+        return "a response without an id"
+    msg_id = frame["id"]
+    if msg_id is not None and not _is_request_id(msg_id):
+        return f"id of type {_type_of(msg_id)}"
+    has_result, has_error = "result" in frame, "error" in frame
+    if has_result == has_error:
+        return "both result and error" if has_result else "neither result nor error"
+    if has_result:
+        if msg_id is None:
+            return "a result with a null id"
+        if not isinstance(frame["result"], dict):
+            return f"result of type {_type_of(frame['result'])}"
+        return None
+    error = frame["error"]
+    if not isinstance(error, dict):
+        return f"error of type {_type_of(error)}"
+    if type(error.get("code")) is not int:
+        return f"error code of type {_type_of(error.get('code'))}"
+    if not isinstance(error.get("message"), str):
+        return f"error message of type {_type_of(error.get('message'))}"
+    return None
+
+
+class _StrictMessageAdapter:
+    """The SDK client transports' `jsonrpc_message_adapter`, with
+    `jsonrpc_envelope_problem` applied first (rev 13, round-12 codex B2).
+
+    The SDK's models are lax where the specification is not: they coerce an
+    `error.code` of `true`, `"5"` or `5.0` to an integer, and ignore an extra
+    member, so a frame with both `result` and `error` validates as an error
+    response. Its `message` then reached pmcp as if it were the downstream's
+    error. A frame this adapter rejects raises a pydantic `ValidationError`
+    whose text is the value-free reason, which each transport already
+    handles as a frame it could not parse: the SSE readers put it on the
+    read stream, where `_read_sse` drops it, and the JSON-response path turns
+    it into a `-32700`, whose message `_downstream_error` replaces.
+    Everything else is the SDK's own adapter.
+    """
+
+    def __init__(self, base: Any) -> None:
+        self._base = base
+
+    def validate_json(self, data: Any, /, *args: Any, **kwargs: Any) -> Any:
+        from pmcp.parsing import JSONParseError, load_json
+
+        problem: str | None
+        try:
+            value = load_json(data, source="downstream JSON-RPC message")
+        except JSONParseError:
+            # Not JSON to Python's parser (or past a parser limit): rejected
+            # here, not handed to the SDK's own parser, so nothing depends on
+            # the two parsers agreeing (round-13 claude N3).
+            problem = "not parseable as JSON"
+        else:
+            problem = jsonrpc_envelope_problem(value)
+        if problem is not None:
+            raise ValidationError.from_exception_data(
+                "JSONRPCMessage",
+                [
+                    {
+                        "type": PydanticCustomError(
+                            "jsonrpc_envelope",
+                            "malformed JSON-RPC envelope: {reason}",
+                            {"reason": problem},
+                        ),
+                        "loc": (),
+                        "input": None,
+                    }
+                ],
+            )
+        return self._base.validate_json(data, *args, **kwargs)
+
+    def __getattr__(self, name: str) -> Any:
+        return getattr(self._base, name)
+
+
+class _ClientTypesView(ModuleType):
+    """`mcp_types` as the SDK's SSE and stdio clients see it: the same module,
+    but with the strict adapter. Those clients read
+    `types.jsonrpc_message_adapter` at call time, and the server side uses the
+    same module, so the module itself is left alone."""
+
+    def __init__(self, base: ModuleType, adapter: Any) -> None:
+        super().__init__(base.__name__)
+        self._base = base
+        self.jsonrpc_message_adapter = adapter
+
+    def __getattr__(self, name: str) -> Any:
+        return getattr(self._base, name)
+
+
+def _install_strict_client_envelopes() -> None:
+    """Point the SDK's three client transports at the strict adapter: the
+    gateway's remote transports, and `stdio_client`, which `pmcp refresh`
+    and the startup description refresh use. Installed on `import pmcp`
+    with the record scrubber. Idempotent; the server-side transports are
+    untouched."""
+    try:
+        import mcp.client.sse as sse_module
+        import mcp.client.stdio as stdio_module
+        import mcp.client.streamable_http as streamable_module
+    except Exception:  # pragma: no cover - the SDK is a dependency
+        return
+
+    for module in (sse_module, stdio_module):
+        current = getattr(module, "types")
+        if not isinstance(current, _ClientTypesView):
+            strict = _StrictMessageAdapter(current.jsonrpc_message_adapter)
+            setattr(module, "types", _ClientTypesView(current, strict))
+    adapter = getattr(streamable_module, "jsonrpc_message_adapter")
+    if not isinstance(adapter, _StrictMessageAdapter):
+        setattr(
+            streamable_module, "jsonrpc_message_adapter", _StrictMessageAdapter(adapter)
+        )
+
+
+def install_log_scrubber() -> None:
+    """Scrub every `LogRecord` at creation, whatever logger creates it.
+
+    Wraps the current ``logging`` record factory, so every record any logger
+    creates -- the MCP SDK's (including ``"client"``, outside ``mcp.*``),
+    uvicorn's, httpx's, any other -- is scrubbed before any handler sees it,
+    regardless of propagation. It scrubs what a record *carries* (its
+    traceback, arguments, an exception as ``msg``); text a library has
+    already formatted into the message string is out of its reach.
+    Idempotent: installing twice keeps one wrapper. Installed on ``import
+    pmcp`` (so by every entry point), and again at ``pmcp.client.manager``
+    import and in ``GatewayServer.__init__``.
+    """
+    current = logging.getLogRecordFactory()
+    if not getattr(current, "pmcp_validation_scrubber", False):
+        logging.setLogRecordFactory(_scrubbing_factory(current))
+    _install_excepthook()
+    _install_threading_excepthook()
+    _install_handle_error()
+    _install_message_rendering()
+    _install_strict_client_envelopes()
+
+
+def _install_handle_error() -> None:
+    """A handler whose ``emit`` raises makes ``logging.Handler.handleError``
+    print "--- Logging error ---" and the exception it is handling, chain and
+    all, to stderr -- past every scrub, and while an ``except`` block for a
+    validation or parse error is active that chain holds it (rev 6; rev 3
+    listed it as unverified). Such a chain is printed by
+    :func:`safe_traceback_text`; everything else by the original method.
+    Idempotent."""
+    original = logging.Handler.handleError
+    if getattr(original, "pmcp_validation_scrubber", False):
+        return
+
+    def handle_error(self: logging.Handler, record: logging.LogRecord) -> None:
+        error = sys.exc_info()[1]
+        if not isinstance(error, BaseException) or safe_exc_info(error) is not None:
+            original(self, record)
+            return
+        if not logging.raiseExceptions or sys.stderr is None:
+            return
+        try:
+            sys.stderr.write(
+                "--- Logging error ---\n"
+                + safe_traceback_text(error)
+                + f"Message: {scrub_record(record).msg!r}\n"
+            )
+        except OSError:  # pragma: no cover - stderr closed, as the original
+            pass
+
+    handle_error.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    handle_error.original = original  # type: ignore[attr-defined]
+    logging.Handler.handleError = handle_error  # type: ignore[method-assign]
+
+
+def _install_excepthook() -> None:
+    """An uncaught exception is printed by ``sys.excepthook``, chain and all:
+    a ``raise ValueError(...) from e`` whose cause is a validation or parse
+    error would print the input past every log scrub (rev 6). Such a chain is
+    printed by :func:`safe_traceback_text` instead; every other exception by
+    the previous hook, unchanged. Idempotent."""
+    previous = sys.excepthook
+    if getattr(previous, "pmcp_validation_scrubber", False):
+        return
+
+    def hook(kind: Any, value: Any, tb: Any) -> None:
+        if isinstance(value, BaseException) and safe_exc_info(value) is None:
+            # The default hook prints nothing when there is no stderr.
+            if sys.stderr is not None:
+                sys.stderr.write(safe_traceback_text(value) + "\n")
+            return
+        previous(kind, value, tb)
+
+    hook.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    hook.previous = previous  # type: ignore[attr-defined]
+    sys.excepthook = hook
+
+
+def _install_threading_excepthook() -> None:
+    """``threading.excepthook`` prints an uncaught exception in a thread the
+    same way (rev 7, N3): such a chain is printed by
+    :func:`safe_traceback_text` under the interpreter's own header; every
+    other exception by the previous hook, unchanged. Idempotent."""
+    import threading
+
+    previous = threading.excepthook
+    if getattr(previous, "pmcp_validation_scrubber", False):
+        return
+
+    def hook(args: Any) -> None:
+        value = getattr(args, "exc_value", None)
+        if (
+            getattr(args, "exc_type", None) is not SystemExit
+            and isinstance(value, BaseException)
+            and safe_exc_info(value) is None
+        ):
+            if sys.stderr is not None:
+                thread = getattr(args, "thread", None)
+                name = getattr(thread, "name", None) or "unknown"
+                sys.stderr.write(
+                    f"Exception in thread {name}:\n" + safe_traceback_text(value) + "\n"
+                )
+            return
+        previous(args)
+
+    hook.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    hook.previous = previous  # type: ignore[attr-defined]
+    threading.excepthook = hook
````

### Patch — `src/pmcp/auth.py`

````diff
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -22,0 +23 @@
+from pmcp.argument_errors import exception_text
@@ -23,0 +25 @@
+from pmcp.parsing import load_json
@@ -776,2 +778,4 @@
-    except ValueError as exc:
-        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_INVALID)) from exc
+    except ValueError:
+        # `from None`: urllib's text quotes the rejected port, and a chained
+        # cause reaches any traceback (Consiliency/pmcp#297).
+        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_INVALID)) from None
@@ -991 +995 @@
-            jwks = json.loads(content.decode("utf-8"))
+            jwks = load_json(content, source="JWKS response", encoding="utf-8")
@@ -994,0 +999,3 @@
+            # (`load_json` already classifies all of them as a value-free
+            # `JSONParseError`, a ValueError; RecursionError stays listed for
+            # the reader.)
@@ -1205 +1212,4 @@
-    text = redact_additive(_sanitize_base(str(value)))
+    # An exception goes through `exception_text`: a validation error's own
+    # text carries the rejected value (Consiliency/pmcp#297).
+    raw = exception_text(value) if isinstance(value, BaseException) else str(value)
+    text = redact_additive(_sanitize_base(raw))
@@ -1424 +1434 @@
-            payload = json.loads(payload_text)
+            payload = load_json(payload_text, source="URL elicitation payload")
@@ -1430 +1440 @@
-                payload = json.loads(match.group(1))
+                payload = load_json(match.group(1), source="URL elicitation payload")
@@ -1518 +1528 @@
-        data = json.loads(body.decode("utf-8"))
+        data = load_json(body, source="auth metadata response", encoding="utf-8")
````

### Patch — `src/pmcp/cli.py`

````diff
--- a/src/pmcp/cli.py
+++ b/src/pmcp/cli.py
@@ -21,0 +22 @@
+from pmcp.argument_errors import exception_text, safe_exc_info
@@ -46,0 +48 @@
+from pmcp.parsing import load_json, load_json_file
@@ -962,2 +964,2 @@
-        logger.error(f"Refresh failed: {e}")
-        print(f"Error: {e}", file=sys.stderr)
+        logger.error(f"Refresh failed: {exception_text(e)}")
+        print(f"Error: {exception_text(e)}", file=sys.stderr)
@@ -1168 +1170 @@
-            parsed = json.loads(text)
+            parsed = load_json(text, source="tool result text")
@@ -1246,2 +1248,2 @@
-    except Exception:
-        logger.debug("Live gateway status query failed", exc_info=True)
+    except Exception as exc:
+        logger.debug("Live gateway status query failed", exc_info=safe_exc_info(exc))
@@ -1899 +1901 @@
-            parsed = json.loads(target_path.read_text())
+            parsed = load_json(target_path.read_text(), source="client config")
@@ -1906 +1908 @@
-                f"Error: Could not parse existing config at {target_path}: {exc}",
+                f"Error: Could not parse existing config at {target_path}: {exception_text(exc)}",
@@ -2019 +2021 @@
-            parsed = json.load(f)
+            parsed = load_json_file(f, source="client config")
@@ -2124 +2126 @@
-                f"{safe_url} unreachable ({exc.__class__.__name__}: {exc})"
+                f"{safe_url} unreachable ({exc.__class__.__name__}: {exception_text(exc)})"
@@ -2373 +2375 @@
-        print(f"error: {exc}", file=sys.stderr)
+        print(f"error: {exception_text(exc)}", file=sys.stderr)
@@ -2562 +2564 @@
-        logger.error(f"Fatal error: {e}")
+        logger.error(f"Fatal error: {exception_text(e)}")
@@ -2793 +2795 @@
-        _trust_fail(str(exc))
+        _trust_fail(exception_text(exc))
@@ -3133 +3135 @@
-        print(f"Fatal error: {e}", file=sys.stderr)
+        print(f"Fatal error: {exception_text(e)}", file=sys.stderr)
````

### Patch — `src/pmcp/client/manager.py`

````diff
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -14 +13,0 @@
-import traceback
@@ -29,0 +29,7 @@
+from pmcp.argument_errors import (
+    describe_value,
+    exception_text,
+    jsonrpc_envelope_problem,
+    safe_traceback_text,
+    install_log_scrubber,
+)
@@ -33,0 +40 @@
+from pmcp.parsing import load_json
@@ -74,0 +82,3 @@
+# The SDK logs rejected frames with a traceback (Consiliency/pmcp#297); every
+# record is scrubbed at creation (`install_log_scrubber`).
+install_log_scrubber()
@@ -139 +149,3 @@
-        f"{type(leaf).__name__}: {leaf}" if str(leaf) else type(leaf).__name__
+        f"{type(leaf).__name__}: {exception_text(leaf)}"
+        if str(leaf)
+        else type(leaf).__name__
@@ -264,3 +276 @@
-    traceback_text = "".join(
-        traceback.format_exception(type(exc), exc, exc.__traceback__)
-    )
+    traceback_text = safe_traceback_text(exc)
@@ -375,0 +386,10 @@
+#: JSON-RPC's parse-error code. The SDK's HTTP transports synthesise it for a
+#: downstream frame their models reject, with `f"Failed to parse ...: {exc}"`
+#: -- pydantic's text, rejected value included -- as the message
+#: (`mcp/client/streamable_http.py:188,407`); it reaches pmcp as a plain
+#: string, where `exception_text` cannot recognise it (Consiliency/pmcp#297).
+_PARSE_ERROR = -32700
+_PARSE_ERROR_MESSAGE = "downstream sent a response that could not be parsed"
+_MALFORMED_ERROR_MESSAGE = "downstream sent a malformed JSON-RPC error"
+
+
@@ -377 +397,8 @@
-    """Build a `DownstreamError` from a JSON-RPC `error` member."""
+    """Build a `DownstreamError` from a JSON-RPC `error` member.
+
+    A parse error's message is replaced by fixed text, whoever wrote it: the
+    SDK's carries the rejected frame, and a downstream's own parse-error
+    prose describes our request, not its failure. So is a malformed `error`
+    (not an object, or a non-string `message`). Every other error keeps the
+    downstream's own message string, returned by design.
+    """
@@ -379,5 +406,79 @@
-        return DownstreamError(str(error))
-    return DownstreamError(
-        str(error.get("message", "Unknown error")),
-        code=error.get("code"),
-        data=error.get("data"),
+        return DownstreamError(_MALFORMED_ERROR_MESSAGE)
+    code = error.get("code")
+    if code == _PARSE_ERROR:
+        return DownstreamError(_PARSE_ERROR_MESSAGE, code=code)
+    message = error.get("message", "Unknown error")
+    if not isinstance(message, str):
+        # JSON-RPC requires a string: anything else is a malformed frame,
+        # whose content is not the downstream's message (the SDK's transports
+        # reject it outright; stdio is parsed without a model).
+        return DownstreamError(_MALFORMED_ERROR_MESSAGE, code=code)
+    return DownstreamError(message, code=code, data=error.get("data"))
+
+
+#: Each task field's wire names, for `_usable_task_raw`.
+_TASK_WIRE_KEYS: dict[str, tuple[str, ...]] = {
+    "status": ("status",),
+    "created_at": ("createdAt", "created_at"),
+    "updated_at": ("updatedAt", "updated_at", "lastUpdatedAt", "last_updated_at"),
+    "ttl": ("ttl",),
+    "poll_interval": ("pollInterval", "poll_interval"),
+}
+
+
+def _usable_task_raw(payload: dict[str, Any]) -> dict[str, Any]:
+    """A downstream task payload as `raw`, without the values pmcp found
+    unusable (Consiliency/pmcp#297 on Consiliency/pmcp#298's drop rule).
+
+    The model drops an unusable hint and names the field in
+    `unusable_fields`; the payload it came from is returned to the caller
+    as `raw` by every `gateway.tasks_*` tool, so the rejected value went back
+    out there. A `null` is kept (`ttl: null` means unlimited), and so is
+    every member pmcp does not read."""
+    unusable = {
+        key
+        for name, keys in _TASK_WIRE_KEYS.items()
+        for key in keys
+        if key in payload
+        and payload[key] is not None
+        and not task_hint_is_usable(name, payload[key])
+    }
+    for key in ("statusMessage", "status_message"):
+        if key in payload and not isinstance(payload[key], (str, type(None))):
+            unusable.add(key)
+    return {key: value for key, value in payload.items() if key not in unusable}
+
+
+def effective_task_mode(
+    tool_info: ToolInfo, task: TaskMetadataInput | dict[str, Any] | None
+) -> bool:
+    """Whether a call runs as an MCP task (the tool requires it, or it was
+    requested and not `enabled: false`): the one derivation for `call_tool`
+    and `gateway.invoke` (rev 17). Otherwise the answer is opaque data."""
+    support = (tool_info.execution or {}).get("taskSupport")
+    if support == "required":
+        return True
+    if task is None:
+        return False
+    parsed = (
+        task
+        if isinstance(task, TaskMetadataInput)
+        else TaskMetadataInput.model_validate(task)
+    )
+    return parsed.enabled
+
+
+def _task_candidate(result: Any) -> dict[str, Any] | None:
+    """Where a downstream answer would carry a task: its nested `task`
+    object if it has one, else the answer itself (flat)."""
+    if not isinstance(result, dict):
+        return None
+    task = result.get("task")
+    return task if isinstance(task, dict) else result
+
+
+def _names_a_task(result: Any) -> bool:
+    """Main's condition for not polling `tasks/get` after `tasks/result`,
+    kept exactly. It decides only that poll (rev 15)."""
+    return isinstance(result, dict) and (
+        isinstance(result.get("task"), dict) or isinstance(result.get("taskId"), str)
@@ -386,0 +488,41 @@
+def task_answer_of(result: Any) -> tuple[dict[str, Any], McpTaskInfo] | None:
+    """The task a downstream answer carries, and its parse -- or None. The
+    one recogniser, and it is the parser (rev 15): the candidate is a task
+    only if `ClientManager._task_info_from_payload` parses it."""
+    payload = _task_candidate(result)
+    if payload is None:
+        return None
+    info = ClientManager._task_info_from_payload(payload)
+    if info is None:
+        return None
+    return payload, info
+
+
+def usable_task_response(result: Any) -> Any:
+    """`result` with its task replaced by that task's parse (`raw`, without
+    the values pmcp dropped), so a dropped hint is neither returned nor sized
+    (Consiliency/pmcp#297). It acts if and only if `task_answer_of` finds a
+    task; anything else is returned unchanged, as data."""
+    found = task_answer_of(result)
+    if found is None:
+        return result
+    payload, info = found
+    if payload is result:
+        return dict(info.raw)
+    return {**result, "task": dict(info.raw)}
+
+
+def parse_request_id(request_id: str) -> tuple[str, int] | None:
+    """`server_name::local_id` as `gateway.cancel` takes it, or None when the
+    id does not have that format (no `::`, or a local id that is not an
+    integer). The handler uses the same parse, so a rejected id is never
+    copied into the response or the audit event (rev 13, round-12 B1)."""
+    if "::" not in request_id:
+        return None
+    server_name, local_id_str = request_id.rsplit("::", 1)
+    try:
+        return server_name, int(local_id_str)
+    except ValueError:
+        return None
+
+
@@ -899 +1041,5 @@
-    return repr(entry)[:120]
+        # No usable identifier: describe the entry's structure, never its
+        # content -- the downstream's data that failed validation
+        # (Consiliency/pmcp#297, rev 10; it was `repr(entry)[:120]`).
+        return f"(an entry with {len(entry)} key{'' if len(entry) == 1 else 's'})"
+    return f"(a {type(entry).__name__} entry)"
@@ -1455 +1601,3 @@
-                error_msg = f"Failed to connect to {config.name}: {result}"
+                error_msg = (
+                    f"Failed to connect to {config.name}: {exception_text(result)}"
+                )
@@ -2065,9 +2213,2 @@
-    def _extract_task_payload(self, result: dict[str, Any]) -> dict[str, Any] | None:
-        task = result.get("task")
-        if isinstance(task, dict):
-            return task
-        if isinstance(result.get("taskId"), str):
-            return result
-        return None
-
-    def _task_info_from_payload(self, payload: dict[str, Any]) -> McpTaskInfo | None:
+    @staticmethod
+    def _task_info_from_payload(payload: dict[str, Any]) -> McpTaskInfo | None:
@@ -2127 +2268,2 @@
-            # `raw` keeps the downstream's own units (Consiliency/pmcp#330).
+            # `raw` keeps the downstream's own units (Consiliency/pmcp#330),
+            # without the values pmcp dropped as unusable (Consiliency/pmcp#297).
@@ -2130 +2272 @@
-            raw=payload,
+            raw=_usable_task_raw(payload),
@@ -2393 +2535,3 @@
-                logger.debug(f"Server {name} doesn't support {kind}: {result}")
+                logger.debug(
+                    f"Server {name} doesn't support {kind}: {exception_text(result)}"
+                )
@@ -2482,2 +2626,2 @@
-                    f"cursor ({raw_cursor!r}); treating as unreadable rather "
-                    f"than as the end of the listing"
+                    f"cursor ({describe_value(raw_cursor)}); treating as "
+                    f"unreadable rather than as the end of the listing"
@@ -2488,2 +2632,3 @@
-                    f"[{managed.config.name}] {kind}/list repeated cursor "
-                    f"{raw_cursor!r}; treating as unreadable rather than looping"
+                    f"[{managed.config.name}] {kind}/list repeated a cursor "
+                    f"({describe_value(raw_cursor)}); treating as unreadable "
+                    f"rather than looping"
@@ -3383,6 +3528,10 @@
-            message = json.loads(text)
-        except json.JSONDecodeError:
-            # Non-JSON output already counted as a heartbeat by the caller.
-            logger.debug(
-                f"[{name}] Non-JSON output: {line.decode(errors='replace').strip()}"
-            )
+            message = load_json(text, source="downstream stdio frame")
+        except json.JSONDecodeError as error:
+            # Already counted as a heartbeat by the caller. A line that is not
+            # a JSON-RPC message is never shown, whatever it holds: a banner,
+            # a log line, or part of a frame the downstream broke across lines
+            # (Consiliency/pmcp#297, rev 12). MCP's stdio transport allows only
+            # newline-delimited messages on stdout; a server's own log belongs
+            # on stderr, which is logged as before. The record is fixed text
+            # plus the parser's value-free description.
+            logger.debug(f"[{name}] non-protocol stdout line: {exception_text(error)}")
@@ -3426,13 +3575,20 @@
-        Each guard below exists because a later access depends on it, and each
-        drops the frame with a value-free debug log, as a non-JSON line is
-        dropped:
-
-        - `frame.get` / `"method" in frame` / `frame["error"]` need a JSON
-          object: `[]`, `42`, `"x"`, `null`, `true` all parse but are not one.
-        - `msg_id in managed.pending_requests` needs a hashable id, and must not
-          match one of our int ids by numeric equality: `True == 1` and
-          `1.0 == 1` both hash equal, so a bool or float id would resolve
-          request 1. JSON-RPC ids are strings, integers or null.
-        - `set_result` / `set_exception` raise `InvalidStateError` on a future
-          that is already settled (a caller cancelled it, and the response
-          raced its `finally` pop).
+        A frame that is not a JSON-RPC 2.0 message (`jsonrpc_envelope_problem`)
+        is dropped with a value-free record, like a line that is not JSON
+        (Consiliency/pmcp#297, rev 13). Before, the guards here were only the
+        ones a later access depended on, so `{"jsonrpc": "1.0", "id": 8,
+        "error": {...}}`, a frame with no `jsonrpc`, one with both `result`
+        and `error`, or one whose `error.code` was an object resolved pending
+        request 8, and its `message` reached the caller's log as the
+        downstream's error. A malformed frame carrying a pending request's id
+        does not settle that request: nothing in a frame that is not a message
+        is acted on, its id included, so the request is answered by a valid
+        frame or times out (idle timeout; `tools/call`'s ceiling). The
+        alternative, failing it with fixed text, would let any line that
+        parses with a colliding id end a caller's request.
+
+        The envelope rules also give the dispatcher what its accesses need: a
+        JSON object; an id that is a string or an integer, so a bool or float
+        cannot resolve request 1 by numeric equality. `set_result` /
+        `set_exception` still raise `InvalidStateError` on a future that is
+        already settled (a caller cancelled it, and the response raced its
+        `finally` pop), which is checked below.
@@ -3440,5 +3596,3 @@
-        if not isinstance(frame, dict):
-            logger.debug(
-                f"[{name}] dropped invalid frame: not a JSON object "
-                f"({type(frame).__name__})"
-            )
+        problem = jsonrpc_envelope_problem(frame)
+        if problem is not None:
+            logger.debug(f"[{name}] dropped invalid frame: {problem}")
@@ -3447,7 +3600,0 @@
-        if msg_id is not None and (
-            isinstance(msg_id, bool) or not isinstance(msg_id, (str, int))
-        ):
-            logger.debug(
-                f"[{name}] dropped invalid frame: id of type {type(msg_id).__name__}"
-            )
-            return
@@ -3462 +3609,2 @@
-                # Notification: no id, nothing to resolve.
+                # Notification: no id (or `id: null`, which main and the MCP
+                # SDK read as a notification; rev 15), nothing to resolve.
@@ -3467,9 +3614,0 @@
-        elif "method" in frame:
-            # A `method` that is present but not a string is not a valid
-            # JSON-RPC request -- and it is not a response either, so it must
-            # not fall through to the pending lookup, where an id colliding with
-            # one of ours would resolve that future.
-            logger.debug(
-                f"[{name}] dropped invalid frame: non-string method "
-                f"({type(method).__name__})"
-            )
@@ -4507,4 +4646,2 @@
-        task_requested = task is not None
-        if support == "required":
-            task_requested = True
-        if task_requested and support == "forbidden":
+        asked = task is not None or support == "required"
+        if asked and support == "forbidden":
@@ -4512 +4649 @@
-        if task_requested and not self._server_supports_tasks(managed):
+        if asked and not self._server_supports_tasks(managed):
@@ -4521,0 +4659 @@
+        task_requested = effective_task_mode(tool_info, task)
@@ -4528,5 +4666,2 @@
-            if not parsed_task.enabled and support != "required":
-                task_requested = False
-            else:
-                params["task"] = self._task_wire_metadata(parsed_task)
-                requestor_context = parsed_task.requestor_context
+            params["task"] = self._task_wire_metadata(parsed_task)
+            requestor_context = parsed_task.requestor_context
@@ -4543,10 +4678,9 @@
-            task_payload = self._extract_task_payload(result)
-            if task_payload is not None:
-                task_info = self._task_info_from_payload(task_payload)
-                if task_info is not None:
-                    self._record_task(
-                        tool_info.server_name,
-                        task_info,
-                        tool_id=tool_id,
-                        requestor_context=requestor_context,
-                    )
+            found = task_answer_of(result)
+            if found is not None:
+                self._record_task(
+                    tool_info.server_name,
+                    found[1],
+                    tool_id=tool_id,
+                    requestor_context=requestor_context,
+                )
+            return usable_task_response(result)
@@ -4618,3 +4752,2 @@
-        payload = self._extract_task_payload(result) or result
-        task_info = self._task_info_from_payload(payload)
-        if task_info is None:
+        found = task_answer_of(result)
+        if found is None:
@@ -4622 +4755 @@
-        return self._record_task(server_name, task_info)
+        return self._record_task(server_name, found[1])
@@ -4643,6 +4776,4 @@
-        task_payload = self._extract_task_payload(result)
-        if task_payload is not None:
-            task_info = self._task_info_from_payload(task_payload)
-            if task_info is not None:
-                self._record_task(server_name, task_info)
-        else:
+        found = task_answer_of(result)
+        if found is not None:
+            self._record_task(server_name, found[1])
+        elif not _names_a_task(result):
@@ -4655 +4786 @@
-        return result
+        return usable_task_response(result)
@@ -4679,3 +4810,8 @@
-        payload = self._extract_task_payload(result) or result
-        task_info = self._task_info_from_payload(payload)
-        if task_info is None:
+        found = task_answer_of(result)
+        if found is not None:
+            task_info = found[1]
+        else:
+            # The answer carries no task pmcp can parse, so nothing in it was
+            # read: `raw` stays empty rather than holding the whole answer
+            # (rev 15, round-14 claude F001: an unparseable task's `ttl` went
+            # back out through `gateway.tasks_cancel`).
@@ -4686 +4821,0 @@
-                raw=result,
@@ -4982 +5117,2 @@
-                f"Invalid request_id format: {request_id}",
+                "Invalid request_id format: expected server_name::local_id "
+                f"({describe_value(request_id)})",
@@ -4986,6 +5122,9 @@
-
-        server_name, local_id_str = request_id.rsplit("::", 1)
-        try:
-            local_id = int(local_id_str)
-        except ValueError:
-            return ("not_found", f"Invalid local_id: {local_id_str}", False, None)
+        parsed_id = parse_request_id(request_id)
+        if parsed_id is None:
+            return (
+                "not_found",
+                "Invalid local_id: expected an integer (a string)",
+                False,
+                None,
+            )
+        server_name, local_id = parsed_id
````

### Patch — `src/pmcp/config/guidance.py`

````diff
--- a/src/pmcp/config/guidance.py
+++ b/src/pmcp/config/guidance.py
@@ -13,0 +14,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml
@@ -170 +172 @@
-            data = yaml.safe_load(f)
+            data = load_yaml(f, source="guidance config")
@@ -178 +180,3 @@
-        print(f"Warning: Failed to load guidance config from {config_path}: {e}")
+        print(
+            f"Warning: Failed to load guidance config from {config_path}: {exception_text(e)}"
+        )
@@ -235 +239 @@
-            loaded = yaml.safe_load(config_path.read_text())
+            loaded = load_yaml(config_path.read_text(), source="guidance config")
@@ -275 +279 @@
-            loaded = yaml.safe_load(config_path.read_text())
+            loaded = load_yaml(config_path.read_text(), source="guidance config")
````

### Patch — `src/pmcp/config/loader.py`

````diff
--- a/src/pmcp/config/loader.py
+++ b/src/pmcp/config/loader.py
@@ -16,0 +17,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json
@@ -282 +284 @@
-        logger.warning(f"Failed to parse config file {file_path}: {e}")
+        logger.warning(f"Failed to parse config file {file_path}: {exception_text(e)}")
@@ -298 +300 @@
-        data = json.loads(content)
+        data = load_json(content, source="config file")
@@ -319 +321 @@
-        logger.warning(f"Failed to parse config file {file_path}: {e}")
+        logger.warning(f"Failed to parse config file {file_path}: {exception_text(e)}")
@@ -357 +359 @@
-        return None, f"invalid_json: {exc}"
+        return None, f"invalid_json: {exception_text(exc)}"
@@ -366 +368 @@
-        data = json.loads(content)
+        data = load_json(content, source="config file")
@@ -368 +370 @@
-        return None, f"invalid_json: {exc}"
+        return None, f"invalid_json: {exception_text(exc)}"
@@ -1151 +1153,3 @@
-        logger.debug(f"Manifest defaults unavailable during config load: {e}")
+        logger.debug(
+            f"Manifest defaults unavailable during config load: {exception_text(e)}"
+        )
````

### Patch — `src/pmcp/env_store.py`

````diff
--- a/src/pmcp/env_store.py
+++ b/src/pmcp/env_store.py
@@ -23 +23,3 @@
-        raise ValueError(f"Env var name must match ^[A-Za-z_][A-Za-z0-9_]*$: {name!r}")
+        # The rejected name is not quoted: through `gateway.auth_connect` it is
+        # a caller's value (Consiliency/pmcp#297, rev 12).
+        raise ValueError("Env var name must match ^[A-Za-z_][A-Za-z0-9_]*$")
````

### Patch — `src/pmcp/feedback_egress.py`

````diff
--- a/src/pmcp/feedback_egress.py
+++ b/src/pmcp/feedback_egress.py
@@ -48,0 +49 @@
+from pmcp.parsing import load_json
@@ -525 +526,3 @@
-        document: Any = json.loads(raw.decode("utf-8"))
+        document: Any = load_json(
+            raw, source="feedback issue response", encoding="utf-8"
+        )
@@ -599 +602,3 @@
-        document: Any = json.loads(raw.decode("utf-8"))
+        document: Any = load_json(
+            raw, source="feedback repository response", encoding="utf-8"
+        )
````

### Patch — `src/pmcp/manifest/code_patterns_loader.py`

````diff
--- a/src/pmcp/manifest/code_patterns_loader.py
+++ b/src/pmcp/manifest/code_patterns_loader.py
@@ -12 +12,2 @@
-import yaml
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml
@@ -43 +44 @@
-                data = yaml.safe_load(f)
+                data = load_yaml(f, source="code patterns file")
@@ -67 +68 @@
-                f"Warning: Failed to load code patterns from {self._patterns_path}: {e}"
+                f"Warning: Failed to load code patterns from {self._patterns_path}: {exception_text(e)}"
````

### Patch — `src/pmcp/manifest/environment.py`

````diff
--- a/src/pmcp/manifest/environment.py
+++ b/src/pmcp/manifest/environment.py
@@ -12,0 +13 @@
+from pmcp.argument_errors import exception_text
@@ -91 +92 @@
-        logger.debug(f"Error checking CLI {name}: {e}")
+        logger.debug(f"Error checking CLI {name}: {exception_text(e)}")
@@ -115 +116 @@
-        logger.debug(f"Error getting help for {name}: {e}")
+        logger.debug(f"Error getting help for {name}: {exception_text(e)}")
````

### Patch — `src/pmcp/manifest/installer.py`

````diff
--- a/src/pmcp/manifest/installer.py
+++ b/src/pmcp/manifest/installer.py
@@ -14,0 +15 @@
+from pmcp.argument_errors import exception_text, safe_exc_info
@@ -241 +242 @@
-            job.error = str(e)[:300]
+            job.error = exception_text(e)[:300]
@@ -251 +252,3 @@
-                logger.error(f"Install job {job.id} task crashed: {exc}")
+                logger.error(
+                    f"Install job {job.id} task crashed: {exception_text(exc)}"
+                )
@@ -254 +257 @@
-                    job.error = f"Monitor task crashed: {exc}"
+                    job.error = f"Monitor task crashed: {exception_text(exc)}"
@@ -264 +267 @@
-                        logger.debug(f"task cleanup error: {e}")
+                        logger.debug(f"task cleanup error: {exception_text(e)}")
@@ -300 +303 @@
-                logger.debug(f"stream reader error: {e}")
+                logger.debug(f"stream reader error: {exception_text(e)}")
@@ -426 +429 @@
-                                        f"Install {job.id}: Error in server detection: {e}"
+                                        f"Install {job.id}: Error in server detection: {exception_text(e)}"
@@ -463 +466,4 @@
-            logger.error(f"Install job {job.id} monitor error: {e}", exc_info=True)
+            logger.error(
+                f"Install job {job.id} monitor error: {exception_text(e)}",
+                exc_info=safe_exc_info(e),
+            )
@@ -465 +471 @@
-            job.error = str(e)
+            job.error = exception_text(e)
@@ -500 +506,3 @@
-            logger.warning(f"Install {job_id}: Error terminating process: {e}")
+            logger.warning(
+                f"Install {job_id}: Error terminating process: {exception_text(e)}"
+            )
````

### Patch — `src/pmcp/manifest/loader.py`

````diff
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -20,0 +21,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml, safe_yaml_loader
@@ -44 +46 @@
-    return getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
+    return safe_yaml_loader(fast=True)
@@ -49 +51,2 @@
-    return yaml.load(content, Loader=_trusted_yaml_loader())  # noqa: S506 - safe loaders only
+    # Through the parse helper (Consiliency/pmcp#297): a failure is value-free.
+    return load_yaml(content, source="shipped manifest", loader=_trusted_yaml_loader())
@@ -1200 +1203 @@
-        data = yaml.safe_load(content)
+        data = load_yaml(content, source="manifest overlay")
@@ -1202 +1205,3 @@
-        logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
+        logger.warning(
+            f"Skipping unreadable manifest overlay {path}: {exception_text(exc)}"
+        )
@@ -1223 +1228 @@
-                    f"Skipping invalid server entry '{name}' in overlay {path}: {exc}"
+                    f"Skipping invalid server entry '{name}' in overlay {path}: {exception_text(exc)}"
@@ -1237 +1242 @@
-                    f"{path}: {exc}"
+                    f"{path}: {exception_text(exc)}"
@@ -1310 +1315 @@
-def _report_cache_failure(what: str, exc: BaseException) -> None:
+def _report_cache_failure(what: str, kind: type[BaseException]) -> None:
@@ -1319,2 +1324,3 @@
-    name = type(exc).__name__
-    if isinstance(exc, RecursionError) or _cache_failure_reported:
+    # The class only, never the exception (Consiliency/pmcp#297's sink rule).
+    name = kind.__name__
+    if issubclass(kind, RecursionError) or _cache_failure_reported:
@@ -1343 +1349 @@
-        _report_cache_failure("storing a result", exc)
+        _report_cache_failure("storing a result", type(exc))
@@ -1352 +1358 @@
-        _report_cache_failure("reading back a result", exc)
+        _report_cache_failure("reading back a result", type(exc))
@@ -1516 +1522,3 @@
-    data = _parse_trusted_document(base) if trusted else yaml.safe_load(base)
+    data = (
+        _parse_trusted_document(base) if trusted else load_yaml(base, source="manifest")
+    )
````

### Patch — `src/pmcp/manifest/npm_resolver.py`

````diff
--- a/src/pmcp/manifest/npm_resolver.py
+++ b/src/pmcp/manifest/npm_resolver.py
@@ -64,0 +65,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json
@@ -377 +379 @@
-            handshake = json.loads(line)
+            handshake = load_json(line, source="npm resolver handshake")
@@ -501 +503,3 @@
-            return _refused(f"npm resolver child died before the query: {exc}")
+            return _refused(
+                f"npm resolver child died before the query: {exception_text(exc)}"
+            )
@@ -511 +515 @@
-            response = json.loads(line)
+            response = load_json(line, source="npm resolver response")
````

### Patch — `src/pmcp/manifest/package_identity.py`

````diff
--- a/src/pmcp/manifest/package_identity.py
+++ b/src/pmcp/manifest/package_identity.py
@@ -32 +31,0 @@
-import json
@@ -42,0 +42,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json
@@ -122 +123 @@
-        data = json.loads(body.decode("utf-8"))
+        data = load_json(body, source="npm packument", encoding="utf-8")
@@ -124 +125 @@
-        logger.debug("npm packument fetch failed for %r: %s", name, exc)
+        logger.debug("npm packument fetch failed for %r: %s", name, exception_text(exc))
@@ -206 +207,3 @@
-        logger.debug("npm packument lookup raised for %r: %s", name, exc)
+        logger.debug(
+            "npm packument lookup raised for %r: %s", name, exception_text(exc)
+        )
````

### Patch — `src/pmcp/manifest/refresher.py`

````diff
--- a/src/pmcp/manifest/refresher.py
+++ b/src/pmcp/manifest/refresher.py
@@ -16,0 +17 @@
+import anyio
@@ -18,0 +20 @@
+from pmcp.argument_errors import exception_text
@@ -29,0 +32 @@
+from pmcp.parsing import load_yaml
@@ -37,0 +41,5 @@
+#: The SDK session's per-request read timeout, and the bound on one server's
+#: whole refresh (connect, `initialize`, `tools/list`), in seconds.
+REFRESH_READ_TIMEOUT_SECONDS = 30.0
+REFRESH_TIMEOUT_SECONDS = 120.0
+
@@ -65 +73 @@
-            data = yaml.safe_load(f)
+            data = load_yaml(f, source="descriptions cache")
@@ -92 +100 @@
-        logger.warning(f"Failed to load descriptions cache: {e}")
+        logger.warning(f"Failed to load descriptions cache: {exception_text(e)}")
@@ -351,21 +359,29 @@
-        # Connect and fetch tools
-        async with stdio_client(server_params) as (read, write):
-            async with ClientSession(read, write) as session:
-                await session.initialize()
-
-                # List tools
-                tools_result = await session.list_tools()
-                tools = tools_result.tools
-
-                logger.info(f"Found {len(tools)} tools for {server_name}")
-
-                # Convert to PrebuiltToolInfo
-                prebuilt_tools = []
-                for tool in tools:
-                    name = tool.name
-                    description = tool.description or ""
-                    short_desc = (
-                        description[:100] + "..."
-                        if len(description) > 100
-                        else description
-                    )
+        # Connect and fetch tools. Bounded (Consiliency/pmcp#297, round-13
+        # claude N2): a reply the strict envelope check drops is never
+        # answered, and this path had no timeout, so a downstream sending one
+        # hung `pmcp refresh` and the startup cache generation. Each request
+        # waits at most the read timeout; the whole refresh at most the
+        # overall bound.
+        with anyio.fail_after(REFRESH_TIMEOUT_SECONDS):
+            async with stdio_client(server_params) as (read, write):
+                async with ClientSession(
+                    read, write, read_timeout_seconds=REFRESH_READ_TIMEOUT_SECONDS
+                ) as session:
+                    await session.initialize()
+
+                    # List tools
+                    tools_result = await session.list_tools()
+                    tools = tools_result.tools
+
+                    logger.info(f"Found {len(tools)} tools for {server_name}")
+
+                    # Convert to PrebuiltToolInfo
+                    prebuilt_tools = []
+                    for tool in tools:
+                        name = tool.name
+                        description = tool.description or ""
+                        short_desc = (
+                            description[:100] + "..."
+                            if len(description) > 100
+                            else description
+                        )
@@ -373,7 +389,8 @@
-                    prebuilt_tools.append(
-                        PrebuiltToolInfo(
-                            name=name,
-                            description=description,
-                            short_description=short_desc,
-                            tags=_extract_tags(name, description),
-                            risk_hint=_infer_risk(name, description),
+                        prebuilt_tools.append(
+                            PrebuiltToolInfo(
+                                name=name,
+                                description=description,
+                                short_description=short_desc,
+                                tags=_extract_tags(name, description),
+                                risk_hint=_infer_risk(name, description),
+                            )
@@ -381 +397,0 @@
-                    )
@@ -383,4 +399,4 @@
-                # Generate capability summary
-                capability_summary = await _generate_capability_summary(
-                    server_name, prebuilt_tools
-                )
+                    # Generate capability summary
+                    capability_summary = await _generate_capability_summary(
+                        server_name, prebuilt_tools
+                    )
@@ -388,8 +404,8 @@
-                return GeneratedServerDescriptions(
-                    package=pkg_name,
-                    package_type=pkg_type,
-                    version=version,
-                    generated_at=datetime.now(timezone.utc).isoformat(),
-                    capability_summary=capability_summary,
-                    tools=prebuilt_tools,
-                )
+                    return GeneratedServerDescriptions(
+                        package=pkg_name,
+                        package_type=pkg_type,
+                        version=version,
+                        generated_at=datetime.now(timezone.utc).isoformat(),
+                        capability_summary=capability_summary,
+                        tools=prebuilt_tools,
+                    )
@@ -398 +414 @@
-        logger.error(f"Failed to refresh {server_name}: {e}")
+        logger.error(f"Failed to refresh {server_name}: {exception_text(e)}")
@@ -496 +512 @@
-            logger.error(f"Error refreshing {name}: {e}")
+            logger.error(f"Error refreshing {name}: {exception_text(e)}")
````

### Patch — `src/pmcp/manifest/registry.py`

````diff
--- a/src/pmcp/manifest/registry.py
+++ b/src/pmcp/manifest/registry.py
@@ -19,0 +20,2 @@
+from pmcp.parsing import load_json
+
@@ -492 +494 @@
-                payload = json.loads(body.decode("utf-8"))
+                payload = load_json(body, source="registry response", encoding="utf-8")
@@ -691 +693 @@
-        payload = json.loads(path.read_text())
+        payload = load_json(path.read_text(), source="registry cache")
````

### Patch — `src/pmcp/manifest/version_checker.py`

````diff
--- a/src/pmcp/manifest/version_checker.py
+++ b/src/pmcp/manifest/version_checker.py
@@ -15,0 +16 @@
+from pmcp.argument_errors import exception_text
@@ -1415 +1416 @@
-        logger.debug(f"npm lookup error for {package_name}: {e}")
+        logger.debug(f"npm lookup error for {package_name}: {exception_text(e)}")
@@ -1457 +1458 @@
-        logger.debug(f"PyPI lookup error for {package_name}: {e}")
+        logger.debug(f"PyPI lookup error for {package_name}: {exception_text(e)}")
@@ -1501 +1502 @@
-        logger.debug(f"crates.io lookup error for {crate_name}: {e}")
+        logger.debug(f"crates.io lookup error for {crate_name}: {exception_text(e)}")
@@ -1555 +1556 @@
-        logger.debug(f"Docker Hub lookup error for {image_name}: {e}")
+        logger.debug(f"Docker Hub lookup error for {image_name}: {exception_text(e)}")
````

### Patch — `src/pmcp/package_approvals.py`

````diff
--- a/src/pmcp/package_approvals.py
+++ b/src/pmcp/package_approvals.py
@@ -43,0 +44,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json, parse_timestamp
@@ -128,0 +131,3 @@
+    # Raised outside the handler, chaining nothing, so the description shows
+    # (Consiliency/pmcp#297 rev 19).
+    failure: str | None = None
@@ -132 +137,3 @@
-        raise PackageApprovalError(f"Invalid package approval entry: {exc}") from exc
+        failure = exception_text(exc)
+    if failure is not None:
+        raise PackageApprovalError(f"Invalid package approval entry: {failure}")
@@ -138 +145 @@
-        parsed_at = datetime.fromisoformat(str(recorded_at))
+        parsed_at = parse_timestamp(str(recorded_at), source="package approval record")
@@ -140,3 +147,3 @@
-        raise PackageApprovalError(
-            f"Unparseable package approval timestamp: {exc}"
-        ) from exc
+        failure = exception_text(exc)
+    if failure is not None:
+        raise PackageApprovalError(f"Unparseable package approval timestamp: {failure}")
@@ -174,0 +182 @@
+    failure: str | None = None
@@ -176 +184 @@
-        data = json.loads(raw)
+        data = load_json(raw, source="package approvals")
@@ -178,3 +186,4 @@
-        raise PackageApprovalError(
-            f"Cannot parse package approvals {path}: {exc}"
-        ) from exc
+        failure = exception_text(exc)
+    if failure is not None:
+        # Outside the handler: chains nothing (Consiliency/pmcp#297 rev 19).
+        raise PackageApprovalError(f"Cannot parse package approvals {path}: {failure}")
````

### Patch — `src/pmcp/parsing.py`

````diff
--- /dev/null
+++ b/src/pmcp/parsing.py
@@ -0,0 +1,173 @@
+"""Parse structured text without ever echoing it (Consiliency/pmcp#297, rev 7).
+
+A parser's failure can quote what it rejected, and not only through its own
+error type: PyYAML's ``MarkedYAMLError`` renders a snippet of the input, and
+a YAML tag makes it run a *constructor* whose plain ``ValueError`` /
+``KeyError`` quotes the value (``k: !!int <value>`` raises
+``ValueError: invalid literal for int() with base 10: '<value>'``). So a
+failure is classified by **where it happened**, not by its type: every parse
+of structured text in pmcp goes through one helper per format below, and any
+exception raised while parsing or constructing becomes a :class:`ParseError`
+that names the format, the source pmcp chose to describe (a path, "npm
+registry response") and, where the parser recorded one, the line and column
+-- never the input. It is raised outside the ``except`` block, so it chains
+nothing (``__cause__`` and ``__context__`` are both ``None``).
+
+Each :class:`ParseError` also subclasses the error type its format's callers
+already catch (``yaml.YAMLError``, ``json.JSONDecodeError`` and so
+``ValueError``), so no caller's ``except`` clause changes meaning.
+``tests/test_parse_error_echo.py`` fails on any direct parser call in
+``src/pmcp`` outside this module.
+"""
+
+from __future__ import annotations
+
+import json
+from datetime import datetime
+from typing import IO, Any
+
+import yaml
+
+
+class ParseError(ValueError):
+    """A structured-text parse failed. Its text is value-free by construction."""
+
+    def __init__(
+        self,
+        kind: str,
+        source: str,
+        line: int | None = None,
+        column: int | None = None,
+        cause: str | None = None,
+    ) -> None:
+        self.kind = kind
+        self.source = source
+        self.line = line
+        self.column = column
+        #: The class of the exception the parser raised (``ParserError``,
+        #: ``ValueError`` from a tag's constructor, ``RecursionError``, ...).
+        self.cause = cause
+        ValueError.__init__(self, self._text())
+
+    def _text(self) -> str:
+        where = (
+            f" at line {self.line}, column {self.column}"
+            if isinstance(self.line, int) and isinstance(self.column, int)
+            else ""
+        )
+        why = f" ({self.cause})" if self.cause else ""
+        return f"could not parse {self.kind} {self.source}{where}{why}"
+
+    def __str__(self) -> str:
+        return self._text()
+
+    def __reduce__(self) -> Any:  # pragma: no cover - pickling support
+        return (
+            type(self),
+            (self.kind, self.source, self.line, self.column, self.cause),
+        )
+
+
+class YAMLParseError(ParseError, yaml.YAMLError):
+    """A YAML parse (or construction) failed."""
+
+
+class JSONParseError(ParseError, json.JSONDecodeError):
+    """A JSON parse failed. ``doc`` is empty: it would be the input."""
+
+    def __init__(
+        self,
+        kind: str,
+        source: str,
+        line: int | None = None,
+        column: int | None = None,
+        cause: str | None = None,
+    ) -> None:
+        ParseError.__init__(self, kind, source, line, column, cause)
+        # `json.JSONDecodeError`'s attributes, without the input.
+        self.msg = "could not parse JSON"
+        self.doc = ""
+        self.pos = 0
+        self.lineno = line if isinstance(line, int) else 0
+        self.colno = column if isinstance(column, int) else 0
+
+
+class TimestampParseError(ParseError):
+    """An ISO-8601 timestamp parse failed."""
+
+
+def _yaml_position(error: BaseException) -> tuple[int | None, int | None]:
+    if isinstance(error, yaml.MarkedYAMLError):
+        mark = error.problem_mark or error.context_mark
+        if mark is not None:
+            return mark.line + 1, mark.column + 1
+    return None, None
+
+
+def safe_yaml_loader(*, fast: bool) -> Any:
+    """A safe YAML loader class for :func:`load_yaml`: with ``fast``, libyaml's
+    ``CSafeLoader`` when PyYAML was built with it, else ``SafeLoader``."""
+    if fast:
+        return getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
+    return yaml.SafeLoader
+
+
+def load_yaml(stream: str | bytes | IO[Any], *, source: str, loader: Any = None) -> Any:
+    """``yaml.safe_load(stream)``; any failure is a :class:`YAMLParseError`.
+
+    ``loader`` picks a safe loader class -- ``yaml.SafeLoader`` or libyaml's
+    ``yaml.CSafeLoader`` (the shipped manifest's fast path) -- and nothing
+    else."""
+    safe = {yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)}
+    if loader is not None and loader not in safe:
+        raise ValueError("load_yaml takes only a safe YAML loader")
+    failure: tuple[int | None, int | None, str] | None = None
+    try:
+        if loader is not None:
+            return yaml.load(stream, Loader=loader)  # noqa: S506 -- safe loaders only
+        return yaml.safe_load(stream)
+    except Exception as error:  # noqa: BLE001 -- classified by origin
+        line, column = _yaml_position(error)
+        failure = (line, column, type(error).__name__)
+    line, column, cause = failure
+    raise YAMLParseError("YAML", source, line, column, cause=cause)
+
+
+def load_json(
+    text: str | bytes | bytearray, *, source: str, encoding: str | None = None
+) -> Any:
+    """``json.loads(text)``; any failure is a :class:`JSONParseError`.
+
+    With ``encoding``, bytes are decoded first (``json.loads(b.decode(enc))``)
+    inside the same classification, so a ``UnicodeDecodeError`` -- whose text
+    quotes the offending byte -- is a parse failure too. Without it, bytes go
+    to ``json.loads`` as they are, which detects UTF-8/16/32 itself.
+    """
+    failure: tuple[int | None, int | None, str] | None = None
+    try:
+        if encoding is not None and isinstance(text, (bytes, bytearray)):
+            text = bytes(text).decode(encoding)
+        return json.loads(text)
+    except Exception as error:  # noqa: BLE001 -- classified by origin
+        if isinstance(error, json.JSONDecodeError):
+            failure = (error.lineno, error.colno, type(error).__name__)
+        else:
+            failure = (None, None, type(error).__name__)
+    line, column, cause = failure
+    raise JSONParseError("JSON", source, line, column, cause=cause)
+
+
+def load_json_file(handle: IO[Any], *, source: str) -> Any:
+    """``json.load(handle)`` through :func:`load_json`."""
+    return load_json(handle.read(), source=source)
+
+
+def parse_timestamp(text: str, *, source: str) -> datetime:
+    """``datetime.fromisoformat(text)``; any failure is a
+    :class:`TimestampParseError`."""
+    failure: str | None = None
+    try:
+        return datetime.fromisoformat(text)
+    except Exception as error:  # noqa: BLE001 -- classified by origin
+        failure = type(error).__name__
+    raise TimestampParseError("timestamp", source, cause=failure)
````

### Patch — `src/pmcp/policy/policy.py`

````diff
--- a/src/pmcp/policy/policy.py
+++ b/src/pmcp/policy/policy.py
@@ -15,2 +15 @@
-import yaml
-
+from pmcp.argument_errors import exception_text
@@ -26,0 +26 @@
+from pmcp.parsing import load_json, load_yaml
@@ -361,7 +361,9 @@
-            if fatal:
-                raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
-                ) from e
-            self._warn_unparseable(policy_path, e)
-            return None
-        return self._parse_policy(content, policy_path, fatal=fatal)
+            if not fatal:
+                self._warn_unparseable(policy_path, e)
+                return None
+            failure = exception_text(e)
+        else:
+            return self._parse_policy(content, policy_path, fatal=fatal)
+        # Raised outside the handler, so it chains nothing and its own
+        # description is shown (Consiliency/pmcp#297 rev 19).
+        raise ValueError(f"Failed to load explicit policy {policy_path}: {failure}")
@@ -383 +385 @@
-            f"Could not parse policy file {policy_path}: {error}. {consequence}"
+            f"Could not parse policy file {policy_path}: {exception_text(error)}. {consequence}"
@@ -413,0 +416,6 @@
+        # Each refusal is raised outside its handler: a pmcp-authored message
+        # built from `exception_text` chains nothing, so the operator sees it
+        # whole (Consiliency/pmcp#297 rev 19). A wrapper raised inside the
+        # handler of a validation error would be shown only as that error's
+        # description.
+        failure: str | None
@@ -416 +424 @@
-                data = yaml.safe_load(content)
+                data = load_yaml(content, source="policy file")
@@ -418 +426 @@
-                data = json.loads(content)
+                data = load_json(content, source="policy file")
@@ -420,6 +428,8 @@
-            if fatal:
-                raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
-                ) from e
-            self._warn_unparseable(policy_path, e)
-            return None
+            if not fatal:
+                self._warn_unparseable(policy_path, e)
+                return None
+            failure = exception_text(e)
+        else:
+            failure = None
+        if failure is not None:
+            raise ValueError(f"Failed to load explicit policy {policy_path}: {failure}")
@@ -434,11 +444,10 @@
-            if fatal:
-                raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
-                ) from e
-            raise ValueError(
-                f"Invalid policy file {policy_path}: {e}. "
-                "Refusing to start rather than fall back to an unrestricted gateway."
-            ) from e
-
-        logger.info(f"Loaded policy from {policy_path}")
-        return policy
+            failure = exception_text(e)
+        else:
+            logger.info(f"Loaded policy from {policy_path}")
+            return policy
+        if fatal:
+            raise ValueError(f"Failed to load explicit policy {policy_path}: {failure}")
+        raise ValueError(
+            f"Invalid policy file {policy_path}: {failure}. "
+            "Refusing to start rather than fall back to an unrestricted gateway."
+        )
@@ -888 +897 @@
-                result = json.loads(final_str)
+                result = load_json(final_str, source="redacted output")
````

### Patch — `src/pmcp/provision_gate.py`

````diff
--- a/src/pmcp/provision_gate.py
+++ b/src/pmcp/provision_gate.py
@@ -42,0 +43 @@
+from pmcp.argument_errors import exception_text
@@ -466 +467 @@
-            exc,
+            exception_text(exc),
````

### Patch — `src/pmcp/redaction_additive.py`

````diff
--- a/src/pmcp/redaction_additive.py
+++ b/src/pmcp/redaction_additive.py
@@ -29 +28,0 @@
-import json
@@ -33,0 +33,2 @@
+from pmcp.parsing import load_json
+
@@ -1359 +1360 @@
-        json.loads(text)
+        load_json(text, source="redaction candidate")
````

### Patch — `src/pmcp/scoped_advisor_audit.py`

````diff
--- a/src/pmcp/scoped_advisor_audit.py
+++ b/src/pmcp/scoped_advisor_audit.py
@@ -16,0 +17,8 @@
+import pydantic
+
+from pmcp.argument_errors import (
+    model_error_path,
+    schema_error_keyword,
+    schema_error_path,
+)
+from pmcp.parsing import load_json
@@ -45 +53 @@
-            json.loads(line)
+            load_json(line, source="scoped advisor audit record")
@@ -141,64 +148,0 @@
-def _declared_property_names(schema: Any) -> frozenset[str]:
-    """Every key of every ``properties`` map anywhere in ``schema``.
-
-    These strings are written by the schema's author, never by a caller, so a
-    path segment equal to one of them discloses nothing the schema does not.
-    """
-    names: set[str] = set()
-
-    def collect(node: Any) -> None:
-        if isinstance(node, dict):
-            properties = node.get("properties")
-            if isinstance(properties, dict):
-                names.update(key for key in properties if isinstance(key, str))
-            for child in node.values():
-                collect(child)
-        elif isinstance(node, list):
-            for child in node:
-                collect(child)
-
-    collect(schema)
-    return frozenset(names)
-
-
-def _rejected_argument_path(
-    error: jsonschema.ValidationError, schema: Any, arguments: Any
-) -> list[str | int | None]:
-    """The failing instance location, with every caller-chosen key redacted.
-
-    Walks ``error.absolute_path`` (not ``.path``, which is relative when
-    ``best_match`` returns an ``anyOf`` child). A segment survives only as an
-    array index (an ``int`` whose container is a list) or as a key the schema
-    declares under some ``properties``. Any other key -- one matched by
-    ``additionalProperties`` or ``patternProperties``, or a non-``str`` key a
-    caller built in-process (an ``int``, or a ``str`` subclass whose ``__eq__``
-    could pass the membership test) -- is chosen by the caller and may itself
-    be a secret, so it becomes ``None``. The walk reads only container types
-    from ``arguments``, never a value.
-    """
-    declared = _declared_property_names(schema)
-    path: list[str | int | None] = []
-    node = arguments
-    for segment in error.absolute_path:
-        if isinstance(node, list) and type(segment) is int:
-            path.append(segment)
-        elif isinstance(node, dict) and type(segment) is str and segment in declared:
-            path.append(segment)
-        else:
-            path.append(None)
-        try:
-            node = node[segment]
-        except (KeyError, IndexError, TypeError):
-            node = None
-    return path
-
-
-def _rejected_argument_validator(
-    error: jsonschema.ValidationError, schema: Any
-) -> str | None:
-    """The failing keyword, if it is one the schema's draft defines."""
-    keywords = jsonschema.validators.validator_for(schema).VALIDATORS
-    validator = error.validator
-    return validator if isinstance(validator, str) and validator in keywords else None
-
-
@@ -307 +251 @@
-        error: jsonschema.ValidationError,
+        error: jsonschema.ValidationError | pydantic.ValidationError,
@@ -311 +255,6 @@
-        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296).
+        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296),
+        or the tool's own argument model did (Consiliency/pmcp#297).
+
+        A model rejection's path is its first error's location, redacted the
+        same way; its validator is ``None`` (a pydantic error type is not a
+        JSON Schema keyword).
@@ -330,2 +279,6 @@
-            argument_path = _rejected_argument_path(error, schema, arguments)
-            validator = _rejected_argument_validator(error, schema)
+            if isinstance(error, jsonschema.ValidationError):
+                argument_path = schema_error_path(error, schema, arguments)
+                validator = schema_error_keyword(error, schema)
+            else:
+                argument_path = model_error_path(error, schema, arguments)
+                validator = None
````

### Patch — `src/pmcp/server.py`

````diff
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -14,0 +15 @@
+import pydantic
@@ -39,0 +41,9 @@
+from pmcp.argument_errors import (
+    describe_model_error,
+    describe_schema_error,
+    exception_text,
+    carries_rejected_value,
+    install_log_scrubber,
+    message_text,
+    safe_exc_info,
+)
@@ -72 +82,5 @@
-from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
+from pmcp.tools.handlers import (
+    GATEWAY_TOOL_INPUT_MODELS,
+    GatewayTools,
+    get_gateway_tool_definitions,
+)
@@ -102,0 +117,65 @@
+def _described_errors(handler: Any) -> Any:
+    """Wrap a request handler so an exception it lets escape never carries a
+    validation or parse error's text to the caller (Consiliency/pmcp#297,
+    rev 10; codes kept in rev 11).
+
+    The SDK maps an escaping exception to the wire
+    (`mcp/shared/jsonrpc_dispatcher.py` `handler_exception_to_error_data`):
+    an `MCPError` carries its own `ErrorData`; a bare pydantic
+    `ValidationError` becomes `-32602 "Invalid request parameters"` with no
+    text; anything else becomes `code=0, message=str(e)`, which is where a
+    wrapper such as `ValueError(f"... {e}") from e` sends the value. When
+    the chain holds a validation or parse error, the replacement keeps that
+    mapping's code and changes only the text:
+    - an `MCPError` keeps its code, with its message (or, if the message
+      embeds the validation text, the structural description) and no
+      `data`;
+    - a bare `ValidationError` keeps `-32602`, now with the structural
+      description;
+    - anything else is a `ValueError` of its description (the SDK's
+      `code=0`).
+
+    The replacement is raised outside the `except`, so it chains nothing.
+    Every other exception passes unchanged.
+    """
+    import functools
+    import inspect
+
+    from mcp.shared.exceptions import MCPError
+    from mcp.types import INVALID_PARAMS
+    from pydantic import ValidationError
+
+    if inspect.isasyncgenfunction(handler):
+        return handler
+
+    @functools.wraps(handler)
+    async def wrapper(*args: Any, **kwargs: Any) -> Any:
+        replacement: Exception | None = None
+        try:
+            return await handler(*args, **kwargs)
+        except Exception as error:
+            if safe_exc_info(error) is not None:
+                raise
+            described = exception_text(error)
+            if isinstance(error, MCPError):
+                # Keep the code; keep message and `data` unless they carry
+                # what was rejected (rev 12: `data` used to be dropped
+                # whenever the chain held a validation error).
+                message = error.error.message
+                if carries_rejected_value(message, error):
+                    message = message_text(message, error)
+                    if carries_rejected_value(message, error):
+                        message = "the request failed on data that did not validate"
+                data = error.error.data
+                if data is not None and carries_rejected_value(data, error):
+                    data = None
+                replacement = MCPError(error.error.code, message, data)
+            elif isinstance(error, ValidationError):
+                replacement = MCPError(INVALID_PARAMS, described)
+            else:
+                replacement = ValueError(described)
+        raise replacement
+
+    return wrapper
+
+
@@ -127,0 +207,3 @@
+        # Idempotent; again here in case a record factory was replaced since
+        # import (Consiliency/pmcp#297).
+        install_log_scrubber()
@@ -226,6 +308,8 @@
-            on_list_tools=self._handle_list_tools,
-            on_call_tool=self._handle_call_tool,
-            on_list_resources=self._handle_list_resources,
-            on_read_resource=self._handle_read_resource,
-            on_list_prompts=self._handle_list_prompts,
-            on_get_prompt=self._handle_get_prompt,
+            on_list_tools=_described_errors(self._handle_list_tools),
+            on_call_tool=_described_errors(self._handle_call_tool),
+            on_list_resources=_described_errors(self._handle_list_resources),
+            on_read_resource=_described_errors(self._handle_read_resource),
+            on_list_prompts=_described_errors(self._handle_list_prompts),
+            on_get_prompt=_described_errors(self._handle_get_prompt),
+            # A `ListenHandler` object the SDK drives as a stream, not a
+            # coroutine; it renders its own failures (Consiliency/pmcp#287).
@@ -341,0 +426,2 @@
+                # Never `e.message`: for `type`, `pattern`, `enum` and length
+                # errors it quotes the rejected value (Consiliency/pmcp#297).
@@ -346 +432,3 @@
-                            type="text", text=f"Input validation error: {e.message}"
+                            type="text",
+                            text="Input validation error: "
+                            + describe_schema_error(e, tool.input_schema, arguments),
@@ -484 +572,35 @@
-                logger.error(f"Tool execution error: {e}")
+                # A `ValidationError`'s text renders the rejected value
+                # (pydantic's `input_value=...`, a validator's own message,
+                # jsonschema's `message`), so it is described from its
+                # structure instead, in the log, the response and the audit
+                # (Consiliency/pmcp#297). The tool's own argument model
+                # rejecting the call is described against the tool's schema;
+                # anything else goes through `exception_text`, which is
+                # `str(e)` for every exception that is not (and does not
+                # embed) a validation error.
+                input_model = GATEWAY_TOOL_INPUT_MODELS.get(audited_name or "")
+                rejected_by_model = (
+                    isinstance(e, pydantic.ValidationError)
+                    and input_model is not None
+                    and e.title == input_model.__name__
+                )
+                if (
+                    isinstance(e, pydantic.ValidationError)
+                    and rejected_by_model
+                    and tool is not None
+                ):
+                    reason = describe_model_error(e, tool.input_schema, arguments)
+                    described = f"Invalid arguments: {reason}"
+                    logger.error(
+                        "Tool execution error: invalid arguments for %s: %s",
+                        audited_name,
+                        reason,
+                    )
+                elif tool is None:
+                    # Only an unregistered name raises here; it is the
+                    # caller's string, so it is not logged (Consiliency/pmcp#297).
+                    described = exception_text(e)
+                    logger.error("Tool execution error: unknown gateway tool")
+                else:
+                    described = exception_text(e)
+                    logger.error(f"Tool execution error: {described}")
@@ -492,6 +614,18 @@
-                    self._record_scoped_invocation(
-                        gateway_tool=audited_name,
-                        terminal_status=failure_status,
-                        arguments=audited_arguments,
-                        result={"error_type": type(e).__name__},
-                    )
+                    if rejected_by_model and tool is not None:
+                        # Like a gate rejection: an `audit.rejection` (tool,
+                        # path, nothing the caller sent), not an invocation
+                        # whose correlations nothing vouched for.
+                        if self._scoped_advisor_audit is not None:
+                            self._scoped_advisor_audit.record_rejected_arguments(
+                                gateway_tool=tool.name,
+                                error=e,
+                                schema=tool.input_schema,
+                                arguments=arguments,
+                            )
+                    else:
+                        self._record_scoped_invocation(
+                            gateway_tool=audited_name,
+                            terminal_status=failure_status,
+                            arguments=audited_arguments,
+                            result={"error_type": type(e).__name__},
+                        )
@@ -514 +648,6 @@
-                        text=json.dumps({"error": True, "message": str(e)[:400]}),
+                        text=json.dumps(
+                            {
+                                "error": True,
+                                "message": described[:400],
+                            }
+                        ),
@@ -740 +879,3 @@
-            logger.warning(f"Failed to load manifest startup configs: {e}")
+            logger.warning(
+                f"Failed to load manifest startup configs: {exception_text(e)}"
+            )
@@ -859 +1000 @@
-                logger.warning(f"Failed to auto-generate cache: {e}")
+                logger.warning(f"Failed to auto-generate cache: {exception_text(e)}")
@@ -1036 +1177 @@
-            logger.error(f"Error during shutdown: {e}")
+            logger.error(f"Error during shutdown: {exception_text(e)}")
````

### Patch — `src/pmcp/subscriptions.py`

````diff
--- a/src/pmcp/subscriptions.py
+++ b/src/pmcp/subscriptions.py
@@ -50,0 +51,2 @@
+from pmcp.argument_errors import safe_exc_info
+
@@ -190 +192 @@
-        except Exception:
+        except Exception as exc:
@@ -194 +196,4 @@
-            logger.exception("subscription bus publish raised; catalog event dropped")
+            logger.error(
+                "subscription bus publish raised; catalog event dropped",
+                exc_info=safe_exc_info(exc),
+            )
````

### Patch — `src/pmcp/templates/code_snippets_loader.py`

````diff
--- a/src/pmcp/templates/code_snippets_loader.py
+++ b/src/pmcp/templates/code_snippets_loader.py
@@ -14 +14,2 @@
-import yaml
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml
@@ -48 +49 @@
-                data = yaml.safe_load(f)
+                data = load_yaml(f, source="code snippet templates")
@@ -65 +66 @@
-                f"Warning: Failed to load code snippets from {self._templates_path}: {e}"
+                f"Warning: Failed to load code snippets from {self._templates_path}: {exception_text(e)}"
````

### Patch — `src/pmcp/tools/handlers.py`

````diff
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -22,0 +23 @@
+from pmcp.argument_errors import describe_value, exception_text, safe_exc_info
@@ -38,0 +40,4 @@
+    parse_request_id,
+    effective_task_mode,
+    task_answer_of,
+    usable_task_response,
@@ -72,0 +78 @@
+    discovered_env_var_refusal_reason,
@@ -207,0 +214 @@
+from pmcp.parsing import load_json, load_json_file
@@ -1023 +1030 @@
-                data = json.load(f)
+                data = load_json_file(f, source="provisioned registry")
@@ -1026 +1033 @@
-            logger.warning(f"Could not load provisioned registry: {e}")
+            logger.warning(f"Could not load provisioned registry: {exception_text(e)}")
@@ -1037 +1044 @@
-            logger.warning(f"Could not save provisioned registry: {e}")
+            logger.warning(f"Could not save provisioned registry: {exception_text(e)}")
@@ -1632,0 +1640 @@
+        task_requested = effective_task_mode(tool_info, parsed.task)
@@ -1664,9 +1671,0 @@
-            # The task this reply carries, recognised by the manager's own
-            # extractor and parser -- wrapped `{task}` or at the top level --
-            # and only when this call ran as a task, as the manager decides
-            # (Consiliency/pmcp#330, board round 3 F001). The record is the
-            # one the manager made from it, in seconds.
-            task_support = (tool_info.execution or {}).get("taskSupport")
-            task_requested = task_support == "required" or (
-                parsed.task is not None and parsed.task.enabled
-            )
@@ -1674,11 +1673,11 @@
-            if task_requested and isinstance(result, dict):
-                task_payload = ClientManager._extract_task_payload(
-                    self._client_manager, result
-                )
-                found = (
-                    ClientManager._task_info_from_payload(
-                        self._client_manager, task_payload
-                    )
-                    if task_payload is not None
-                    else None
-                )
+            # Rev 17 (round-15 codex F001, claude F003; and Consiliency/pmcp#330
+            # round 3 F001, the same gate): task handling follows the call's
+            # effective task mode, derived once before the call. A call that is
+            # not a task returns its answer as opaque data, sized as returned:
+            # no recognition, no registry lookup, no replacement. A task call
+            # returns, and is sized from, the answer reduced to what pmcp
+            # could use (`usable_task_response`, idempotent). The record is the
+            # one the manager made from it, in seconds.
+            if task_requested:
+                result = usable_task_response(result)
+                found = task_answer_of(result)
@@ -1687 +1686 @@
-                        tool_info.server_name, found.task_id
+                        tool_info.server_name, found[1].task_id
@@ -1854 +1853 @@
-            auth_challenge = self._auth_challenge_from_message(str(e))
+            auth_challenge = self._auth_challenge_from_message(exception_text(e))
@@ -1932 +1931,3 @@
-                logger.warning(f"Failed to load manifest startup configs: {e}")
+                logger.warning(
+                    f"Failed to load manifest startup configs: {exception_text(e)}"
+                )
@@ -1938 +1939,3 @@
-                logger.warning(f"Failed to restore provisioned servers: {e}")
+                logger.warning(
+                    f"Failed to restore provisioned servers: {exception_text(e)}"
+                )
@@ -2184 +2187 @@
-                error=str(e),
+                error=exception_text(e),
@@ -2192 +2195 @@
-                errors=[str(e)],
+                errors=[exception_text(e)],
@@ -4301 +4304,3 @@
-                logger.error(f"Failed to connect remote server {server_name}: {e}")
+                logger.error(
+                    f"Failed to connect remote server {server_name}: {exception_text(e)}"
+                )
@@ -4383 +4388,3 @@
-            logger.error(f"Failed to start provisioning {server_name}: {e}")
+            logger.error(
+                f"Failed to start provisioning {server_name}: {exception_text(e)}"
+            )
@@ -4471 +4478 @@
-                        error=str(e),
+                        error=exception_text(e),
@@ -4476 +4483 @@
-                        message=str(e),
+                        message=exception_text(e),
@@ -4612 +4619,4 @@
-                error=f"Env var '{env_var}' is not permitted for this server.",
+                error=(
+                    f"Env var ({describe_value(env_var)}) is not permitted for "
+                    "this server."
+                ),
@@ -4621,2 +4631,2 @@
-                    f"Env var '{env_var}' is not permitted for server "
-                    f"'{server_name}'.{expected} Refusing to store it."
+                    f"Env var ({describe_value(env_var)}) is not permitted for "
+                    f"server '{server_name}'.{expected} Refusing to store it."
@@ -4625 +4635,2 @@
-                env_var=env_var,
+                # Not echoed: the caller's rejected value (rev 12).
+                env_var=None,
@@ -4639 +4650 @@
-                error=str(exc),
+                error=exception_text(exc),
@@ -4644 +4655 @@
-                message=str(exc),
+                message=exception_text(exc),
@@ -4646 +4657,2 @@
-                env_var=env_var,
+                # Not echoed: the caller's rejected value (rev 12).
+                env_var=None,
@@ -4848 +4860 @@
-            logger.warning("Feedback submission raised: %s", exc)
+            logger.warning("Feedback submission raised: %s", exception_text(exc))
@@ -5122 +5134 @@
-                message=f"Failed to run update probe: {e}",
+                message=f"Failed to run update probe: {exception_text(e)}",
@@ -5352 +5364 @@
-                                f"'{server_name}': {e}"
+                                f"'{server_name}': {exception_text(e)}"
@@ -5476 +5488 @@
-                    f"unsafe package identifier {package!r}."
+                    f"unsafe package identifier ({describe_value(package)})."
@@ -5491 +5503,8 @@
-            names = ", ".join(operator_safe(name) for name in disallowed)
+            reasons: dict[str, int] = {}
+            for name in disallowed:
+                reason = discovered_env_var_refusal_reason(name)
+                reasons[reason] = reasons.get(reason, 0) + 1
+            # The rule each name broke, never the name (rev 12, §14).
+            names = "; ".join(
+                f"{count} {reason}" for reason, count in sorted(reasons.items())
+            )
@@ -5521 +5540,5 @@
-            logger.warning("Package identity lookup raised for %r: %s", package, exc)
+            logger.warning(
+                "Package identity lookup raised for %r: %s",
+                package,
+                exception_text(exc),
+            )
@@ -5637,0 +5661,5 @@
+        # Outside the `try`: its arm logs a traceback and renders `str(e)`,
+        # and a `ValidationError`'s text carries the rejected value. Raised,
+        # it is described without it (Consiliency/pmcp#297).
+        parsed = ProvisionStatusInput.model_validate(input_data)
+        job_id = parsed.job_id
@@ -5639,3 +5666,0 @@
-            parsed = ProvisionStatusInput.model_validate(input_data)
-            job_id = parsed.job_id
-
@@ -5709 +5734,4 @@
-            logger.error(f"provision_status handler failed: {e}", exc_info=True)
+            logger.error(
+                f"provision_status handler failed: {exception_text(e)}",
+                exc_info=safe_exc_info(e),
+            )
@@ -5712 +5740 @@
-                job_id=input_data.get("job_id", "unknown"),
+                job_id=job_id,
@@ -5803 +5831,4 @@
-            logger.error(f"Handoff failed for {job_server_name}: {e}", exc_info=True)
+            logger.error(
+                f"Handoff failed for {job_server_name}: {exception_text(e)}",
+                exc_info=safe_exc_info(e),
+            )
@@ -5805 +5836 @@
-            job.error = f"Handoff failed: {e}"
+            job.error = f"Handoff failed: {exception_text(e)}"
@@ -5849 +5880 @@
-            logger.error(f"Failed to refresh after install: {e}")
+            logger.error(f"Failed to refresh after install: {exception_text(e)}")
@@ -6078 +6109 @@
-                error=str(e),
+                error=exception_text(e),
@@ -6122 +6153 @@
-                error=str(e),
+                error=exception_text(e),
@@ -6158 +6189,3 @@
-                        decoded = json.loads(result_payload)
+                        decoded = load_json(
+                            result_payload, source="tool result payload"
+                        )
@@ -6196 +6229 @@
-                error=str(e),
+                error=exception_text(e),
@@ -6300,3 +6333,7 @@
-        server_name = (
-            parsed.request_id.rsplit("::", 1)[0] if "::" in parsed.request_id else None
-        )
+        # A request id `cancel_request` rejected for its format is the
+        # caller's rejected value: neither the response nor the audit event
+        # copies it, or any part of it (rev 13, round-12 B1; the auth_connect
+        # rule of rev 12). A well-formed id is accepted; a lookup that then
+        # misses names it, as every lookup miss does (Consiliency/pmcp#315).
+        parsed_id = parse_request_id(parsed.request_id)
+        server_name = parsed_id[0] if parsed_id is not None else None
@@ -6313 +6350 @@
-            request_id=parsed.request_id,
+            request_id=parsed.request_id if parsed_id is not None else None,
````

### Patch — `src/pmcp/transport/http.py`

````diff
--- a/src/pmcp/transport/http.py
+++ b/src/pmcp/transport/http.py
@@ -24,0 +25 @@
+import re
@@ -48,0 +50,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json
@@ -279,3 +282,14 @@
-    """Return (hostname, port) for an Origin header value, or None if unparseable."""
-    parsed = urlparse(origin)
-    if not parsed.scheme or not parsed.hostname:
+    """Return (hostname, port) for an Origin header value, or None if unparseable.
+
+    ``urlparse`` and ``.port`` raise ``ValueError`` on a malformed authority
+    (``Port could not be cast to integer value as '<port>'``), quoting the
+    caller's header; an unparseable Origin is a rejected one, never a 500
+    (Consiliency/pmcp#297).
+    """
+    try:
+        parsed = urlparse(origin)
+        hostname = parsed.hostname
+        explicit_port = parsed.port
+    except ValueError:
+        return None
+    if not parsed.scheme or not hostname:
@@ -284,2 +298,84 @@
-    port = str(parsed.port) if parsed.port is not None else default_port
-    return parsed.hostname, port
+    port = str(explicit_port) if explicit_port is not None else default_port
+    return hostname, port
+
+
+# --- the SDK's transport rejections, value-free (Consiliency/pmcp#297 rev 19) --
+#
+# The MCP SDK's streamable-HTTP transport answers a request it cannot accept
+# before any pmcp handler runs. Three of its rejections are built from the
+# request itself (round-17 grok F001, claude N1):
+# - a body that is not JSON: `"Parse error: {str(e)}"`, the parser's text;
+# - a JSON body that is not a JSON-RPC message: `"Validation error:
+#   {str(e)}"`, pydantic's text, which quotes every rejected `input_value`;
+# - an unsupported protocol version on the per-request-envelope path:
+#   `data.requested`, the caller's string.
+# `tests/test_http_transport.py` enumerates every non-literal message the
+# SDK's server-transport modules can put in a rejection, and fails on one
+# that is not reviewed here. Every other rejection text is an SDK literal or
+# is built from SDK constants and pmcp's own schema.
+
+_PROTOCOL_REVISION = re.compile(r"\d{4}-\d{2}-\d{2}")
+
+
+def _envelope_problem(request_body: bytes) -> str:
+    """Why `request_body` is not a JSON-RPC message, from its structure: the
+    parse error's format, position and class, or the validation error's
+    paths and phrases -- never the body's text."""
+    from mcp_types import jsonrpc_message_adapter
+    from pydantic import ValidationError
+
+    try:
+        raw = load_json(request_body, source="request body")
+    except ValueError as error:
+        return exception_text(error)
+    try:
+        jsonrpc_message_adapter.validate_python(raw, by_name=False)
+    except ValidationError as error:
+        return exception_text(error)
+    return "the request is not a JSON-RPC message"
+
+
+def value_free_rejection(body: bytes, request_body: bytes | None) -> bytes:
+    """`body` (a JSON-RPC error the SDK's transport sent), with each part built
+    from the request replaced by its structural description. Any other body
+    is returned unchanged."""
+    from mcp_types import INVALID_PARAMS, PARSE_ERROR, UNSUPPORTED_PROTOCOL_VERSION
+
+    try:
+        payload = load_json(body, source="transport rejection")
+    except ValueError:
+        return body
+    error = payload.get("error") if isinstance(payload, dict) else None
+    if (
+        not isinstance(error, dict)
+        or payload.get("id") is not None
+        and not (error.get("code") == UNSUPPORTED_PROTOCOL_VERSION)
+    ):
+        return body
+    code, message = error.get("code"), error.get("message")
+    changed = dict(error)
+    if code == PARSE_ERROR and isinstance(message, str):
+        changed["message"] = "Parse error: " + (
+            _envelope_problem(request_body)
+            if request_body is not None
+            else "the request body is not JSON"
+        )
+    elif code == INVALID_PARAMS and isinstance(message, str):
+        changed["message"] = "Validation error: " + (
+            _envelope_problem(request_body)
+            if request_body is not None
+            else "the request is not a JSON-RPC message"
+        )
+    elif code == UNSUPPORTED_PROTOCOL_VERSION and isinstance(changed.get("data"), dict):
+        data = dict(changed["data"])
+        requested = data.get("requested")
+        data["requested"] = (
+            requested
+            if isinstance(requested, str) and _PROTOCOL_REVISION.fullmatch(requested)
+            else ""
+        )
+        changed["data"] = data
+    else:
+        return body
+    payload = {**payload, "error": changed}
+    return json.dumps(payload, separators=(",", ":")).encode()
@@ -710 +806 @@
-                body_method = json.loads(body_bytes).get("method")
+                body_method = load_json(body_bytes, source="request body").get("method")
@@ -751,0 +848,3 @@
+        request_body = body_bytes if request.method == "POST" else None
+        held_start: MutableMapping[str, Any] | None = None
+        held_body: list[bytes] = []
@@ -754,2 +853,11 @@
-            nonlocal response_started
-            if message.get("type") == "http.response.start":
+            # A JSON response the SDK sends with an error status is held until
+            # complete and passed through `value_free_rejection` (rev 19).
+            nonlocal response_started, held_start
+            kind = message.get("type")
+            if kind == "http.response.start":
+                headers = dict(message.get("headers") or [])
+                if int(message.get("status", 200)) >= 400 and headers.get(
+                    b"content-type", b""
+                ).startswith(b"application/json"):
+                    held_start = message
+                    return
@@ -756,0 +865,18 @@
+            elif kind == "http.response.body" and held_start is not None:
+                held_body.append(message.get("body", b""))
+                if message.get("more_body", False):
+                    return
+                body = value_free_rejection(b"".join(held_body), request_body)
+                start = dict(held_start)
+                start["headers"] = [
+                    (key, value)
+                    for key, value in held_start.get("headers") or []
+                    if key.lower() != b"content-length"
+                ] + [(b"content-length", str(len(body)).encode())]
+                held_start = None
+                response_started = True
+                await original_send(start)
+                await original_send(
+                    {"type": "http.response.body", "body": body, "more_body": False}
+                )
+                return
````

### Patch — `src/pmcp/trust_store.py`

````diff
--- a/src/pmcp/trust_store.py
+++ b/src/pmcp/trust_store.py
@@ -44,0 +45,2 @@
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json, parse_timestamp
@@ -268,0 +271,3 @@
+    # Raised outside the handler, chaining nothing, so the description shows
+    # (Consiliency/pmcp#297 rev 19).
+    failure: str | None = None
@@ -270 +275 @@
-        parsed_at = datetime.fromisoformat(str(recorded_at))
+        parsed_at = parse_timestamp(str(recorded_at), source="trust store record")
@@ -272 +277,3 @@
-        raise TrustStoreError(f"Unparseable trust timestamp: {exc}") from exc
+        failure = exception_text(exc)
+    if failure is not None:
+        raise TrustStoreError(f"Unparseable trust timestamp: {failure}")
@@ -296,0 +304 @@
+    failure: str | None = None
@@ -298 +306 @@
-        data = json.loads(raw)
+        data = load_json(raw, source="trust store")
@@ -300 +308,4 @@
-        raise TrustStoreError(f"Cannot parse trust store {path}: {exc}") from exc
+        failure = exception_text(exc)
+    if failure is not None:
+        # Outside the handler: chains nothing (Consiliency/pmcp#297 rev 19).
+        raise TrustStoreError(f"Cannot parse trust store {path}: {failure}")
````

### Patch — `src/pmcp/types.py`

````diff
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -19,0 +20,7 @@
+from pmcp.argument_errors import (
+    CORRELATION_ID_CHARSET,
+    PACKAGE_NAME_INVALID,
+    SCOPED_CORRELATION_INCOMPLETE,
+    argument_error,
+)
+from pmcp.parsing import parse_timestamp
@@ -615 +622,3 @@
-            return datetime.fromisoformat(candidate).timestamp()
+            # Through the parse helper (Consiliency/pmcp#297): its failure
+            # is value-free; the value itself is dropped, as main drops it.
+            return parse_timestamp(candidate, source="task timestamp").timestamp()
@@ -1116 +1125 @@
-            raise ValueError("correlation IDs may contain only alphanumerics and ._:-")
+            raise argument_error(CORRELATION_ID_CHARSET)
@@ -1129,3 +1138 @@
-            raise ValueError(
-                "scoped advisor correlation fields must be supplied together"
-            )
+            raise argument_error(SCOPED_CORRELATION_INCOMPLETE)
@@ -1322 +1329,3 @@
-    request_id: str
+    #: The id cancelled, or null when it was rejected for its format
+    #: (Consiliency/pmcp#297): a rejected value is not copied back.
+    request_id: str | None
@@ -1624,5 +1633 @@
-            raise ValueError(
-                "package must be a valid npm/pypi identifier "
-                "(no leading dash, whitespace, path separators, or shell "
-                "metacharacters)"
-            )
+            raise argument_error(PACKAGE_NAME_INVALID)
````

### Patch — `src/pmcp/validation.py`

````diff
--- a/src/pmcp/validation.py
+++ b/src/pmcp/validation.py
@@ -258,0 +259,13 @@
+
+
+def discovered_env_var_refusal_reason(name: str) -> str:
+    """Why ``discovered_env_var_allowed`` refuses *name*, in pmcp's own words
+    -- never the name, which is the caller's value (Consiliency/pmcp#297,
+    rev 12)."""
+    if name and not is_dangerous_env_var(name):
+        for prefix in _PACKAGE_MANAGER_ENV_PREFIXES:
+            if name.upper().startswith(prefix):
+                return f"in the {prefix}* family"
+    if not name or is_dangerous_env_var(name):
+        return "a variable pmcp never sets for a server"
+    return "not credential-shaped"
````

### Patch — `tests/test_argument_error_echo.py`

````diff
--- /dev/null
+++ b/tests/test_argument_error_echo.py
@@ -0,0 +1,2579 @@
+"""A rejected gateway-tool argument never echoes its value (Consiliency/pmcp#297).
+
+The oracle is a generated sweep, not hand-picked cases. Its axes come from the
+code:
+
+- every registered gateway tool (``GATEWAY_TOOL_INPUT_MODELS``);
+- every position its advertised schema declares -- nested object properties
+  and list items included -- and, at each, every invalid shape that schema
+  node rejects (wrong type, pattern, length, enum);
+- every custom validator on every argument model the tool parses with, found
+  by introspection (a new validator without a recipe fails the sweep);
+- every field an argument model also accepts by name under
+  ``populate_by_name`` (``InvokeInput.meta``), which the gate never sees;
+- every validation-error type (pydantic and jsonschema) raised from inside a
+  handler.
+
+Each case carries one sentinel in every position the caller controls around
+the invalid value -- the value itself, dict keys, list items, extra keys on
+every object on the path, and every open container (``invoke.arguments``,
+``_meta``, ``task.metadata``, ``requestor_context``). The sweep asserts that
+each case was rejected by argument validation (no vacuous pass), that the
+rejection names the failing field, and that the sentinel -- and its hashes --
+reaches neither the response, the log (every record at DEBUG, rendered by
+pmcp's own text and JSON formatters, tracebacks included) nor the scoped
+audit. It also runs every case twice with sentinels of different length and
+requires the response, log and audit to be identical, so no length, count or
+hash of the value is disclosed either.
+"""
+
+from __future__ import annotations
+
+import copy
+import itertools
+import gc
+import functools
+from unittest import mock
+import hashlib
+import json
+import re
+import logging
+import traceback
+import typing
+from pathlib import Path
+from typing import Any, cast
+
+import jsonschema
+import pytest
+from mcp.types import CallToolRequestParams
+from pydantic import BaseModel, ValidationError, field_validator
+from pydantic_core import PydanticCustomError
+
+from pmcp.server import GatewayServer
+from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_definitions
+from pmcp.types import InvokeInput, McpTaskInfo
+from tests.test_scoped_advisor_audit import (
+    _correlations,
+    _make_ctx,
+    _normalized,
+    _pmcp_formatters,
+    _stable,
+    _write_scoped_policy,
+)
+
+
+def _digest(seed: str, length: int, alphabet: str = "0123456789abcdef") -> str:
+    """`length` deterministic high-entropy characters of `alphabet`."""
+    out, counter = "", 0
+    while len(out) < length:
+        block = hashlib.sha256(f"pmcp-297:{seed}:{counter}".encode()).digest()
+        out += "".join(alphabet[b % len(alphabet)] for b in block)
+        counter += 1
+    return out[:length]
+
+
+_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
+
+#: Sentinel families, two lengths each. A leak can be conditional on the
+#: value's shape (the rev 1 board's mutant S8 echoed only `isalpha()`
+#: values), so the sweep runs every case once per family: hex, letters only,
+#: a provider-token shape, a value with spaces, non-ASCII (written as `\u`
+#: escapes: the test source stays ASCII), and digits only.
+_FAMILIES: dict[str, tuple[str, str]] = {
+    "hex": (
+        "Sq" + _digest("hex-a", 22) + "Zx",
+        "Sq" + _digest("hex-b", 38) + "Zx",
+    ),
+    "alpha": (_digest("alpha-a", 26, _LETTERS), _digest("alpha-b", 42, _LETTERS)),
+    "token": ("sk-proj-" + _digest("token-a", 24), "sk-proj-" + _digest("token-b", 40)),
+    "spaced": (
+        "Bearer " + " ".join(_digest("sp-a", 24)[i : i + 6] for i in range(0, 24, 6)),
+        "Bearer " + " ".join(_digest("sp-b", 42)[i : i + 6] for i in range(0, 42, 6)),
+    ),
+    "unicode": (
+        "\u00e9\u4e2d" + _digest("uni-a", 24) + "\u00fc",
+        "\u00e9\u4e2d" + _digest("uni-b", 40) + "\u00fc",
+    ),
+    "digits": (_digest("dig-a", 26, "0123456789"), _digest("dig-b", 42, "0123456789")),
+}
+#: The hex pair, for the tests that need one family.
+_SENTINELS = _FAMILIES["hex"]
+
+_GATE_PREFIX = "Input validation error: $"
+_MODEL_PREFIX = "Invalid arguments: $"
+
+
+#: pydantic truncates a long `input_value` repr in the middle
+#: (`'Sq3b2ee216cab4bcafa8599...e216cab4bcafa85997Zx'`), so the whole sentinel
+#: never appears in such a leak; every window of this many characters is
+#: searched for instead (48 bits of hex: no accidental match).
+_WINDOW = 12
+
+
+def _forbidden(sentinel: str) -> set[str]:
+    """Every window of the sentinel -- raw, JSON-escaped, `repr`-escaped and
+    `unicode_escape`d -- and each hash of it, a log or record could carry."""
+    forms: set[str] = set()
+    for spelling in {
+        sentinel,
+        json.dumps(sentinel)[1:-1],
+        repr(sentinel)[1:-1],
+        sentinel.encode("unicode_escape").decode("ascii"),
+    }:
+        forms |= {spelling[i : i + _WINDOW] for i in range(len(spelling) - _WINDOW + 1)}
+    for text in (sentinel, json.dumps(sentinel), sentinel.lower()):
+        forms.add(hashlib.sha256(text.encode()).hexdigest())
+        forms.add(hashlib.sha1(text.encode()).hexdigest())
+        forms.add(hashlib.md5(text.encode()).hexdigest())
+    return forms
+
+
+# --- the generator -------------------------------------------------------------
+
+
+def _tools() -> dict[str, Any]:
+    return {tool.name: tool for tool in get_gateway_tool_definitions()}
+
+
+def _models() -> dict[str, type[BaseModel] | None]:
+    return dict(GATEWAY_TOOL_INPUT_MODELS)
+
+
+def _types(node: dict[str, Any]) -> list[str]:
+    declared = node.get("type")
+    if declared is None:
+        return []
+    return declared if isinstance(declared, list) else [declared]
+
+
+def _positions(
+    node: dict[str, Any], path: tuple[str | int, ...] = ()
+) -> list[tuple[tuple[str | int, ...], dict[str, Any]]]:
+    """Every declared position below the root, depth-first."""
+    found: list[tuple[tuple[str | int, ...], dict[str, Any]]] = []
+    for key, child in sorted((node.get("properties") or {}).items()):
+        found.append(((*path, key), child))
+        found.extend(_positions(child, (*path, key)))
+    items = node.get("items")
+    if isinstance(items, dict):
+        found.append(((*path, 0), items))
+        found.extend(_positions(items, (*path, 0)))
+    return found
+
+
+def _invalid_values(node: dict[str, Any], s: str) -> list[tuple[str, Any]]:
+    """Values this schema node rejects, each carrying `s` in every slot."""
+    return [
+        (label, value)
+        for label, value in _candidate_values(node, s)
+        if not jsonschema.validators.validator_for(node)(node).is_valid(value)
+    ]
+
+
+def _candidate_values(node: dict[str, Any], s: str) -> list[tuple[str, Any]]:
+    """The shapes `_invalid_values` tries (unchecked: a case's build reuses
+    the shape its construction already checked)."""
+    types = _types(node)
+    candidates: list[tuple[str, Any]] = []
+    if types and "string" not in types:
+        candidates.append(("type-string", s))
+    if types and "object" not in types:
+        candidates.append(("type-object", {s: s, "k": [s, {s: s}]}))
+    if types and "array" not in types:
+        candidates.append(("type-array", [s, {s: s}, [s]]))
+    if "string" in types:
+        if "pattern" in node:
+            length = max(node.get("minLength", 0), len(s) + 1)
+            candidates.append(("pattern", ("!" + s * 8)[:length]))
+        if "maxLength" in node:
+            candidates.append(("maxLength", s * (node["maxLength"] // len(s) + 1)))
+        if "enum" in node:
+            candidates.append(("enum", s))
+    return candidates
+
+
+def _open_containers(schema: dict[str, Any]) -> list[tuple[str | int, ...]]:
+    """Declared object positions that accept any key."""
+    return [
+        path
+        for path, node in _positions(schema)
+        if "object" in _types(node)
+        and node.get("additionalProperties", True) is not False
+        and not node.get("properties")
+    ]
+
+
+def _open_content(s: str) -> dict[str, Any]:
+    return {s: s, "nested": {s: [s, {s: s}]}, "list": [s, 7, {s: None}]}
+
+
+def _baseline(tool: Any) -> dict[str, Any]:
+    """The smallest arguments the tool's schema accepts, from its schema."""
+    return copy.deepcopy(_checked_baseline(tool.name))
+
+
+@functools.cache
+def _checked_baseline(name: str) -> dict[str, Any]:
+    tool = _tools()[name]
+    schema = tool.input_schema
+    baseline: dict[str, Any] = {}
+    for name in schema.get("required") or []:
+        prop = schema["properties"][name]
+        baseline[name] = (
+            prop["enum"][0]
+            if "enum" in prop
+            else "x" * max(prop.get("minLength", 1), 1)
+        )
+    if tool.name == "gateway.invoke":
+        baseline["tool_id"] = "srv::tool"
+        baseline.update(_correlations())
+    jsonschema.validate(baseline, schema)
+    return baseline
+
+
+def _set(
+    arguments: dict[str, Any], path: tuple[str | int, ...], value: Any, s: str
+) -> None:
+    """Put `value` at `path`, creating containers, and give every object on
+    the way an extra key carrying `s`."""
+    node: Any = arguments
+    for segment, following in zip(path, path[1:]):
+        existing = node[segment] if isinstance(node, dict) and segment in node else None
+        if isinstance(following, int):
+            child: Any = existing if isinstance(existing, list) else []
+            while len(child) <= following:
+                child.append(None)
+        else:
+            child = existing if isinstance(existing, dict) else {}
+            child[f"extra_{s}"] = s
+        if isinstance(node, list):
+            node[segment] = child  # type: ignore[index]
+        else:
+            node[segment] = child
+        node = child
+    node[path[-1]] = value
+
+
+def _decorated(tool: Any, s: str) -> dict[str, Any]:
+    """The baseline with `s` in an extra top-level key and in every open
+    container the schema declares."""
+    arguments = copy.deepcopy(_baseline(tool))
+    arguments[f"extra_{s}"] = {s: [s]}
+    for path in _open_containers(tool.input_schema):
+        _set(arguments, path, _open_content(s), s)
+    return arguments
+
+
+@pytest.mark.parametrize("name", sorted(_tools()))
+def test_every_decorated_baseline_passes_the_gate(name: str) -> None:
+    """Decorations alone are accepted, so a case's rejection is its own."""
+    tool = _tools()[name]
+    for sentinels in _FAMILIES.values():
+        for s in sentinels:
+            jsonschema.validate(_decorated(tool, s), tool.input_schema)
+
+
+def _expected_path(path: tuple[str | int, ...]) -> str:
+    rendered = "$"
+    for segment in path:
+        rendered += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
+    return rendered
+
+
+def _reachable_models(model: type[BaseModel] | None) -> list[type[BaseModel]]:
+    """`model` and every model nested in its fields' annotations."""
+    seen: list[type[BaseModel]] = []
+    pending: list[Any] = [model] if model is not None else []
+    while pending:
+        current = pending.pop()
+        if isinstance(current, type) and issubclass(current, BaseModel):
+            if current in seen:
+                continue
+            seen.append(current)
+            pending.extend(f.annotation for f in current.model_fields.values())
+        else:
+            pending.extend(typing.get_args(current))
+    return seen
+
+
+#: How to fail each custom validator on an argument model, as top-level
+#: arguments merged over the decorated baseline. Keyed by (model, validator,
+#: field); the sweep asserts this table equals what introspection finds.
+_VALIDATOR_RECIPES: dict[tuple[str, str, str], Any] = {
+    ("InvokeInput", "_validate_correlation_id", "run_correlation_id"): (
+        lambda s: {"run_correlation_id": s + "!"},
+        "$.run_correlation_id: correlation IDs may contain only",
+    ),
+    ("InvokeInput", "_validate_correlation_id", "seat_correlation_id"): (
+        lambda s: {"seat_correlation_id": s + "!"},
+        "$.seat_correlation_id: correlation IDs may contain only",
+    ),
+    ("InvokeInput", "_reject_partial_scoped_correlation", ""): (
+        lambda s: {
+            "run_correlation_id": s,
+            "seat_correlation_id": None,
+            "evidence_label_digest": None,
+        },
+        "$: scoped advisor correlation fields must be supplied together",
+    ),
+    ("RegisterDiscoveredServerInput", "_validate_package", "package"): (
+        lambda s: {"package": "-" + s},
+        "$.package: package must be a valid npm/pypi identifier",
+    ),
+}
+
+
+def _validators(model: type[BaseModel]) -> list[tuple[str, str, str]]:
+    decorators = model.__pydantic_decorators__
+    found = [
+        (model.__name__, name, field)
+        for name, decorator in decorators.field_validators.items()
+        for field in decorator.info.fields
+    ]
+    found += [(model.__name__, name, "") for name in decorators.model_validators]
+    return found
+
+
+class _Case(typing.NamedTuple):
+    tool: str
+    label: str
+    build: Any  # sentinel -> arguments
+    layer: str  # "gate" or "model"
+    expected: str  # the rendered location (and phrase) the rejection must name
+
+
+def _cases() -> list[_Case]:
+    tools, models = _tools(), _models()
+    cases: list[_Case] = []
+    discovered: set[tuple[str, str, str]] = set()
+    for name in sorted(tools):
+        tool = tools[name]
+        for path, node in _positions(tool.input_schema):
+            # No silent shrink: every typed position yields a case.
+            assert not _types(node) or _invalid_values(node, _SENTINELS[0]), (
+                name,
+                path,
+            )
+            for label, _ in _invalid_values(node, _SENTINELS[0]):
+
+                def build(s: str, tool=tool, path=path, node=node, label=label) -> dict:
+                    arguments = _decorated(tool, s)
+                    value = dict(_candidate_values(node, s))[label]
+                    _set(arguments, path, value, s)
+                    return arguments
+
+                cases.append(
+                    _Case(
+                        name,
+                        f"{_expected_path(path)}:{label}",
+                        build,
+                        "gate",
+                        _expected_path(path) + ": ",
+                    )
+                )
+        # A missing required key, beside the caller's own extra keys: the
+        # description must name the schema's key, never one of the caller's.
+        assert not [p for p, n in _positions(tool.input_schema) if n.get("required")], (
+            "a nested `required` needs this axis extended",
+            name,
+        )
+        for required in tool.input_schema.get("required") or []:
+
+            def build(s: str, tool=tool, required=required) -> dict:
+                arguments = _decorated(tool, s)
+                del arguments[required]
+                return arguments
+
+            cases.append(
+                _Case(
+                    name,
+                    f"required:{required}",
+                    build,
+                    "gate",
+                    f"$.{required}: is required",
+                )
+            )
+        for model in _reachable_models(models[name]):
+            for key in _validators(model):
+                discovered.add(key)
+                recipe, expected = _VALIDATOR_RECIPES[key]
+
+                def build(s: str, tool=tool, recipe=recipe) -> dict:
+                    return {**_decorated(tool, s), **recipe(s)}
+
+                cases.append(_Case(name, f"validator:{key}", build, "model", expected))
+            if model.model_config.get("populate_by_name") and model is models[name]:
+                for field_name, field in model.model_fields.items():
+                    if not isinstance(field.alias, str) or field.alias == field_name:
+                        continue
+                    node = tool.input_schema["properties"][field.alias]
+                    for label, _ in _invalid_values(node, _SENTINELS[0]):
+
+                        def build(
+                            s: str,
+                            tool=tool,
+                            node=node,
+                            label=label,
+                            field_name=field_name,
+                            alias=field.alias,
+                        ) -> dict:
+                            arguments = _decorated(tool, s)
+                            # By name only: with the alias present too,
+                            # pydantic reads the alias.
+                            assert arguments.pop(alias, None) is not None
+                            arguments[field_name] = dict(_candidate_values(node, s))[
+                                label
+                            ]
+                            return arguments
+
+                        cases.append(
+                            _Case(
+                                name,
+                                f"by-name:{field_name}:{label}",
+                                build,
+                                "model",
+                                f"$.{field_name}: ",
+                            )
+                        )
+    assert discovered == set(_VALIDATOR_RECIPES), (
+        "every custom validator on an argument model needs a recipe, and "
+        "every recipe a validator",
+        discovered ^ set(_VALIDATOR_RECIPES),
+    )
+    return cases
+
+
+_CASES = _cases()
+
+
+def test_the_sweep_covers_every_tool_that_can_reject_an_argument() -> None:
+    """No vacuous pass: every registered tool that declares an argument
+    contributes cases, and the ones that do not declare none at all."""
+    tools = _tools()
+    covered = {case.tool for case in _CASES}
+    for name, tool in tools.items():
+        if tool.input_schema.get("properties"):
+            assert name in covered, name
+        else:
+            assert name not in covered, name
+    assert {case.layer for case in _CASES} == {"gate", "model"}
+    assert len(_CASES) > 100, len(_CASES)
+
+
+# --- running a case --------------------------------------------------------------
+
+
+def _server(tmp_path: Path, *, audited: bool) -> tuple[GatewayServer, Path | None]:
+    audit_path = tmp_path / "audit.jsonl" if audited else None
+    if audited:
+        server = GatewayServer(
+            policy_path=_write_scoped_policy(tmp_path / "policy.json"),
+            audit_jsonl=audit_path,
+            cache_dir=tmp_path / "cache",
+        )
+    else:
+        policy = tmp_path / "policy.json"
+        policy.write_text("{}")
+        server = GatewayServer(policy_path=policy, cache_dir=tmp_path / "cache")
+    server._create_server()
+    # Every registered tool reaches its real handler.
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: True
+    )
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
+def _rejection(result: Any, layer: str) -> str:
+    """The rejection text, asserting the call was rejected by argument
+    validation on `layer` (an `isError` gate result, or the `{"error": true}`
+    payload of `call_tool`'s `except` arm) and not, say, accepted."""
+    text = "".join(block.text for block in result.content)
+    if layer == "gate":
+        assert result.is_error is True, text
+        return text
+    payload = json.loads(text)
+    assert payload.get("error") is True, text
+    return str(payload["message"])
+
+
+def _useful(response: str, layer: str, expected: str) -> None:
+    """The rejection says where, and why, in the value-free form."""
+    prefix = _GATE_PREFIX if layer == "gate" else _MODEL_PREFIX
+    assert response.startswith(prefix), response
+    assert expected in response, response
+
+
+#: The attributes every `LogRecord` has; anything else came in via `extra=`.
+_STANDARD_RECORD_KEYS = frozenset(
+    logging.LogRecord("x", logging.INFO, "x", 1, "x", None, None).__dict__
+) | {"message", "asctime"}
+
+
+def _record_text(record: logging.LogRecord) -> str:
+    """Everything a log handler could see in `record`, whatever formats it:
+    pmcp's text and JSON formatters, the raw message and args, every
+    attribute (`extra=` fields included), and the traceback of `exc_info`
+    and `stack_info` rendered in full (pmcp's JSON formatter drops them)."""
+    parts = [formatter.format(record) for formatter in _pmcp_formatters()]
+    parts.append(repr(record.msg))
+    parts.append(repr(record.args))
+    parts.append(record.getMessage())
+    for key, value in sorted(record.__dict__.items()):
+        if key == "exc_info" and value and value[1] is not None:
+            parts.append("".join(traceback.format_exception(*value)))
+        elif key not in ("msg", "args"):
+            parts.append(f"{key}={value!r}")
+    return "\n".join(parts)
+
+
+def _record_stable(record: logging.LogRecord) -> str:
+    """`record` for the pair differential: both formatters at a fixed time,
+    plus every `extra=` attribute."""
+    extras = {
+        key: repr(value)
+        for key, value in sorted(record.__dict__.items())
+        if key not in _STANDARD_RECORD_KEYS
+    }
+    rendered = [_normalized(record, formatter) for formatter in _pmcp_formatters()]
+    text = "\n".join(rendered) + (f"\nextra={extras!r}" if extras else "")
+    # The one volatile token a downstream call logs: its wall-clock latency.
+    return _ELAPSED.sub("elapsed_ms=N", text)
+
+
+#: Exactly `elapsed_ms=<digits>` in `handlers.py`'s `tool_call` line.
+_ELAPSED = re.compile(r"\belapsed_ms=[0-9]+\b")
+
+
+class _Tap:
+    """Every channel a call can leak into: the response (given), the log
+    (`caplog` at DEBUG, root), stdout/stderr (`capfd`), warnings
+    (`recwarn`), the gateway's in-memory audit-event buffer (what
+    `gateway.health` exposes), and the scoped-audit JSONL."""
+
+    def __init__(
+        self,
+        server: GatewayServer,
+        audit_path: Path | None,
+        caplog: pytest.LogCaptureFixture,
+        capfd: pytest.CaptureFixture[str],
+        recwarn: pytest.WarningsRecorder,
+    ) -> None:
+        self.server, self.audit_path = server, audit_path
+        self.caplog, self.capfd, self.recwarn = caplog, capfd, recwarn
+        # A server an earlier test left open is finalised by the collector at
+        # an arbitrary moment, and its `ResourceWarning` would land inside
+        # some pair. Collect now; each test here shuts its own servers down.
+        gc.collect()
+
+    def _audit_text(self) -> str:
+        path = self.audit_path
+        return path.read_text() if path is not None and path.exists() else ""
+
+    def _buffer(self) -> list[str]:
+        events = getattr(self.server._gateway_tools, "__dict__", {}).get(
+            "_audit_events", []
+        )
+        return [event.model_dump_json() for event in events]
+
+    def start(self) -> tuple[int, int, str, list[str]]:
+        self.capfd.readouterr()
+        return (
+            len(self.caplog.records),
+            len(self.recwarn),
+            self._audit_text(),
+            self._buffer(),
+        )
+
+    def since(self, mark: tuple[int, int, str, list[str]], response: str) -> _Observed:
+        records = self.caplog.records[mark[0] :]
+        captured = self.capfd.readouterr()
+        warned = [
+            f"{w.category.__name__}: {w.message} @ {w.filename}:{w.lineno}"
+            for w in list(self.recwarn)[mark[1] :]
+        ]
+        raw_audit = self._audit_text()[len(mark[2]) :]
+        buffer = self._buffer()
+        new_events = [event for event in buffer if event not in mark[3]]
+        return _Observed(
+            response=response,
+            log="\n".join(_record_stable(record) for record in records),
+            raw_log="\n".join(_record_text(record) for record in records),
+            streams=captured.out + captured.err,
+            warnings="\n".join(warned),
+            audit=[
+                _stable(json.loads(line)) for line in raw_audit.splitlines() if line
+            ],
+            raw_audit=raw_audit,
+            events="\n".join(new_events),
+        )
+
+
+class _Observed(typing.NamedTuple):
+    response: str
+    log: str
+    raw_log: str
+    streams: str
+    warnings: str
+    audit: list[dict[str, Any]]
+    raw_audit: str
+    events: str
+
+    def leaks(self, sentinel: str) -> list[str]:
+        """The channels that carry `sentinel` in any form."""
+        channels = {
+            "response": self.response,
+            "log": self.raw_log,
+            "stdout/stderr": self.streams,
+            "warnings": self.warnings,
+            "audit": self.raw_audit,
+            "audit-event buffer": self.events,
+        }
+        forms = _forbidden(sentinel)
+        return [
+            name for name, text in channels.items() if any(f in text for f in forms)
+        ]
+
+    def stable(self) -> tuple[Any, ...]:
+        """What must be identical for two sentinels of different length. A
+        task time pmcp itself records (the current time, for a timestamp it
+        dropped as unusable; Consiliency/pmcp#298) is not the pair's."""
+        response = re.sub(
+            r'"(created_at|updated_at)": [0-9.eE+-]+', r'"\1": <time>', self.response
+        )
+        # ...and so is the digest of a result holding that time.
+        audit = [
+            {k: v for k, v in entry.items() if k != "redacted_result_digest"}
+            for entry in self.audit
+        ]
+        return (response, self.log, self.streams, self.warnings, audit)
+
+    def differs(self, other: _Observed) -> list[tuple[str, Any, Any]]:
+        """The channels where `self` and `other` differ, for a readable failure."""
+        names = ("response", "log", "streams", "warnings", "audit")
+        return [
+            (name, mine, theirs)
+            for name, mine, theirs in zip(names, self.stable(), other.stable())
+            if mine != theirs
+        ]
+
+
+def _event_shape(events: str) -> list[dict[str, Any]]:
+    return [
+        {
+            k: v
+            for k, v in json.loads(line).items()
+            if k not in ("timestamp", "duration_ms", "latency_ms")
+        }
+        for line in events.splitlines()
+        if line
+    ]
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
+async def test_no_rejected_argument_value_reaches_a_response_log_or_audit(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    audited: bool,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    server, audit_path = _server(tmp_path, audited=audited)
+    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
+    for family, sentinels in _FAMILIES.items():
+        for case in _CASES:
+            seen = []
+            for sentinel in sentinels:
+                mark = tap.start()
+                result = await _call(server, case.tool, case.build(sentinel))
+                seen.append(tap.since(mark, _rejection(result, case.layer)))
+            for sentinel, observed in zip(sentinels, seen):
+                assert observed.leaks(sentinel) == [], (family, case.label, observed)
+            if family == "hex":
+                # Useful: the rejection names where, and why.
+                _useful(seen[0].response, case.layer, case.expected)
+            # Nothing else about the value -- length, count or hash -- either.
+            assert seen[0].stable() == seen[1].stable(), (family, case.label)
+            assert _event_shape(seen[0].events) == _event_shape(seen[1].events)
+    await server.shutdown()
+    # The audit oracle is live, not empty by accident.
+    if audit_path is not None:
+        assert '"audit.rejection"' in audit_path.read_text()
+    assert caplog.records, "nothing was logged, so the log oracle proves nothing"
+
+
+# --- every handler, called past the gate ------------------------------------------
+
+
+@pytest.mark.asyncio
+async def test_every_handler_rejects_what_the_gate_rejects_without_logging_it(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture
+) -> None:
+    """In-process callers reach a handler without the gate. Each handler must
+    then raise the model's `ValidationError` (which the server describes)
+    rather than catch it and log or render its text -- the shape
+    `provision_status` had, with a traceback, before Consiliency/pmcp#297."""
+    caplog.set_level(logging.DEBUG)
+    server, _ = _server(tmp_path, audited=False)
+    tools = server._gateway_tools
+    checked = 0
+    for case in _CASES:
+        if case.layer != "gate":
+            continue
+        handler = getattr(tools, case.tool.removeprefix("gateway."))
+        for s in _SENTINELS:
+            start = len(caplog.records)
+            raised: BaseException | None = None
+            try:
+                await handler(case.build(s))
+            except Exception as error:  # noqa: BLE001 - inspected below
+                raised = error
+            logged = "\n".join(
+                formatter.format(record)
+                for record in caplog.records[start:]
+                for formatter in _pmcp_formatters()
+            )
+            for form in _forbidden(s):
+                assert form not in logged, (case.label, logged)
+            assert isinstance(raised, ValidationError), (case.label, raised)
+        checked += 1
+    await server.shutdown()
+    assert checked > 100, checked
+
+
+# --- validation errors raised inside a handler --------------------------------------
+
+
+def _handler_validation_errors(s: str) -> list[BaseException]:
+    """One of each validation-error type a handler can raise, built from a
+    value carrying `s`: a pydantic error from a downstream-data model, a
+    pydantic error from an argument model, and a jsonschema error."""
+    errors: list[BaseException] = []
+    for model, data in (
+        (McpTaskInfo, {"task_id": {s: s}, "raw": [s]}),
+        (InvokeInput, {"tool_id": {s: s}, "options": s, "_meta": [s]}),
+    ):
+        try:
+            model.model_validate(data)
+        except ValidationError as error:
+            errors.append(error)
+    try:
+        jsonschema.validate({"x": {s: s}}, {"properties": {"x": {"type": "string"}}})
+    except jsonschema.ValidationError as error:
+        errors.append(error)
+    assert len(errors) == 3
+    for error in errors:
+        # the raw text does carry it (pydantic truncates it in the middle)
+        assert any(form in str(error) for form in _forbidden(s)), error
+    return errors
+
+
+class _RaisingTools:
+    def __init__(self, error: BaseException) -> None:
+        self.error = error
+
+    def __getattr__(self, name: str) -> Any:
+        async def handler(*args: Any, **kwargs: Any) -> dict:
+            raise self.error
+
+        return handler
+
+
+#: What `exception_text` makes of each of `_handler_validation_errors`.
+_HANDLER_ERROR_TEXT = (
+    re.compile(
+        r"2 validation errors for McpTaskInfo: \$\.task_id: must be a string; \$\.raw: "
+    ),
+    re.compile(r"3 validation errors for InvokeInput: \$\.tool_id: must be a string; "),
+    # `x` is no name pmcp's models declare, so it reads as `*`.
+    re.compile(r"schema validation error: \$\.\*: fails its type constraint$"),
+)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize(
+    "index", [0, 1, 2], ids=["downstream-model", "argument-model", "jsonschema"]
+)
+async def test_a_validation_error_raised_by_a_handler_is_described_not_echoed(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    index: int,
+) -> None:
+    """Not the tool's own argument model, so not "Invalid arguments": the
+    server's arm renders it with `exception_text`, against no schema (rev 2,
+    N6: a jsonschema error names its keyword, never a constraint)."""
+    caplog.set_level(logging.DEBUG)
+    server, _ = _server(tmp_path, audited=False)
+    tap = _Tap(server, None, caplog, capfd, recwarn)
+    seen = []
+    for family, sentinels in _FAMILIES.items():
+        for s in sentinels:
+            server._gateway_tools = _RaisingTools(_handler_validation_errors(s)[index])  # type: ignore[assignment]
+            mark = tap.start()
+            result = await _call(server, "gateway.catalog_search", {"query": "q"})
+            payload = json.loads("".join(block.text for block in result.content))
+            observed = tap.since(mark, json.dumps(payload))
+            assert observed.leaks(s) == [], (family, observed)
+            assert payload["error"] is True
+            assert _HANDLER_ERROR_TEXT[index].match(payload["message"]), payload
+            assert f"Tool execution error: {payload['message']}" in observed.raw_log
+            seen.append(observed.stable())
+    await server.shutdown()
+    assert all(item == seen[0] for item in seen[1:])
+
+
+# --- the renderers, directly ---------------------------------------------------------
+
+
+class _PoisonedSchemaError(jsonschema.ValidationError):
+    """Every attribute that can carry the rejected value raises on access."""
+
+    def _poisoned(self: Any) -> Any:
+        raise AssertionError("the renderer read a value-bearing error attribute")
+
+    message = property(_poisoned)  # type: ignore[assignment]
+    instance = property(_poisoned)  # type: ignore[assignment]
+    validator_value = property(_poisoned)  # type: ignore[assignment]
+    context = property(_poisoned)  # type: ignore[assignment]
+    cause = property(_poisoned)  # type: ignore[assignment]
+    schema = property(_poisoned)  # type: ignore[assignment]
+
+
+@pytest.mark.parametrize(
+    ("arguments", "expected"),
+    [
+        # The issue's example, verbatim shape.
+        (
+            {"tool_id": "a::b", "options": "Bearer sk-SECRET-VALUE"},
+            "$.options: must be of type object or null",
+        ),
+        (
+            {"tool_id": "a::b", "evidence_label_digest": "sk-" * 30},
+            "$.evidence_label_digest: must be at most 64 characters",
+        ),
+        (
+            {"tool_id": "a::b", "evidence_label_digest": "sk-SECRET".ljust(64, "!")},
+            "$.evidence_label_digest: must match the pattern ^[0-9a-f]{64}$",
+        ),
+        (
+            {"tool_id": "a::b", "task": {"ttl": "sk-SECRET"}},
+            "$.task.ttl: must be of type integer or null",
+        ),
+        ({"arguments": {"sk-SECRET": 1}}, "$.tool_id: is required"),
+    ],
+)
+def test_a_schema_rejection_names_the_field_and_the_reason_only(
+    arguments: dict, expected: str
+) -> None:
+    from pmcp.argument_errors import describe_schema_error
+
+    schema = _tools()["gateway.invoke"].input_schema
+    error = jsonschema.exceptions.best_match(
+        jsonschema.validators.validator_for(schema)(schema).iter_errors(arguments)
+    )
+    assert error is not None
+    error.__class__ = _PoisonedSchemaError
+    described = describe_schema_error(error, schema, arguments)
+    assert described == expected
+    assert "sk-" not in described and "SECRET" not in described
+
+
+def test_a_caller_chosen_key_in_a_path_is_redacted() -> None:
+    from pmcp.argument_errors import describe_schema_error
+
+    schema = {
+        "type": "object",
+        "properties": {
+            "env": {"type": "object", "additionalProperties": {"type": "integer"}},
+            "names": {"type": "array", "items": {"type": "string"}},
+        },
+    }
+    for arguments, expected in (
+        ({"env": {"sk-KEY-SECRET": "v"}}, "$.env.*: must be of type integer"),
+        ({"names": ["ok", {"sk-SECRET": 1}]}, "$.names[1]: must be of type string"),
+    ):
+        error = jsonschema.exceptions.best_match(
+            jsonschema.validators.validator_for(schema)(schema).iter_errors(arguments)
+        )
+        assert error is not None
+        assert describe_schema_error(error, schema, arguments) == expected
+    closed = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
+    error = jsonschema.exceptions.best_match(
+        jsonschema.validators.validator_for(closed)(closed).iter_errors({"sk-KEY": 1})
+    )
+    assert error is not None
+    assert (
+        describe_schema_error(error, closed, {"sk-KEY": 1})
+        == "$: has a property that is not accepted"
+    )
+
+
+def test_a_model_rejection_names_the_field_and_the_reason_only() -> None:
+    from pmcp.argument_errors import describe_model_error
+
+    schema = _tools()["gateway.invoke"].input_schema
+    secret = "sk-" + "S" * 77  # the issue's 80-character secret
+    cases = [
+        (
+            {"tool_id": "a::b", "run_correlation_id": secret + "!"},
+            "$.run_correlation_id: correlation IDs may contain only alphanumerics and ._:-",
+        ),
+        (
+            {"tool_id": "a::b", "run_correlation_id": "r", "arguments": {"q": secret}},
+            "$: scoped advisor correlation fields must be supplied together",
+        ),
+        ({"tool_id": "a::b", "meta": secret}, "$.meta: must be an object"),
+        (
+            {
+                "tool_id": "a::b",
+                "_meta": {secret: 1},
+                "options": {"timeout_ms": secret},
+            },
+            "$.options.timeout_ms: must be an integer",
+        ),
+    ]
+    for arguments, expected in cases:
+        with pytest.raises(ValidationError) as raised:
+            InvokeInput.model_validate(arguments)
+        assert secret[-21:] in str(raised.value) or secret in str(raised.value)
+        described = describe_model_error(raised.value, schema, arguments)
+        assert described == expected
+        assert "sk-" not in described and "SSS" not in described
+
+
+def test_a_dict_key_in_a_model_location_is_redacted() -> None:
+    from pmcp.argument_errors import describe_model_error
+
+    class _Keyed(BaseModel):
+        env: dict[str, int]
+
+    with pytest.raises(ValidationError) as raised:
+        _Keyed.model_validate({"env": {"sk-KEY-SECRET": "x"}})
+    assert "sk-KEY-SECRET" in str(raised.value)
+    described = describe_model_error(
+        raised.value, {"properties": {"env": {}}}, {"env": {"sk-KEY-SECRET": "x"}}
+    )
+    assert described == "$.env.*: must be an integer"
+
+
+class _CollidingErrors(BaseModel):
+    """A validator author reusing pydantic's own error types with the value
+    in the very `ctx` keys a naive renderer would fill a phrase from (the rev
+    1 board's N2)."""
+
+    literal: str | None = None
+    short: str | None = None
+    bounded: int | None = None
+    nested: dict[str, str] | None = None
+
+    @field_validator("literal")
+    @classmethod
+    def _literal(cls, value: str) -> str:
+        raise PydanticCustomError("literal_error", "{expected}", {"expected": value})
+
+    @field_validator("short")
+    @classmethod
+    def _short(cls, value: str) -> str:
+        raise PydanticCustomError(
+            "string_too_short", "{min_length}", {"min_length": value}
+        )
+
+    @field_validator("bounded", mode="before")
+    @classmethod
+    def _bounded(cls, value: Any) -> int:
+        raise PydanticCustomError("greater_than", "{gt}", {"gt": value})
+
+    @field_validator("nested")
+    @classmethod
+    def _nested(cls, value: dict[str, str]) -> dict[str, str]:
+        raise PydanticCustomError("missing", "{input}", {"input": value})
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_a_custom_error_cannot_fill_a_phrase_from_its_context(family: str) -> None:
+    """A constraint is read from the gateway's own schema, never from `ctx`,
+    so a colliding custom error renders the schema's constraint -- or none."""
+    from pmcp.argument_errors import describe_model_error, exception_text
+
+    s = _FAMILIES[family][1]
+    arguments = {"literal": s, "short": s, "bounded": s, "nested": {"k": s}}
+    with pytest.raises(ValidationError) as raised:
+        _CollidingErrors.model_validate(arguments)
+    assert any(form in str(raised.value) for form in _forbidden(s))
+    schema = {
+        "type": "object",
+        "properties": {
+            "literal": {"type": "string", "enum": ["a", "b"]},
+            "short": {"type": "string", "minLength": 3},
+            "bounded": {"type": "integer"},
+            "nested": {"type": "object"},
+        },
+    }
+    for rendered, expected in (
+        (
+            describe_model_error(raised.value, schema, arguments),
+            '$.literal: must be one of ["a", "b"]; $.short: must be at least 3 '
+            "characters; $.bounded: is too small; $.nested: is required",
+        ),
+        (
+            exception_text(raised.value),
+            # No schema here, and a test model's fields are no names pmcp
+            # declares, so the locations read as `*`.
+            "4 validation errors for _CollidingErrors: $.*: is not an "
+            "allowed value; $.*: is too short; $.*: is too small; "
+            "$.*: is required",
+        ),
+    ):
+        assert rendered == expected
+        assert not any(form in rendered for form in _forbidden(s))
+
+
+def test_every_constrained_phrase_names_a_schema_keyword() -> None:
+    """Each constraint comes from a JSON Schema keyword of the gateway's own
+    schema; none from pydantic's `ctx`."""
+    import jsonschema.validators
+
+    from pmcp.argument_errors import _CONSTRAINED_PHRASES
+
+    keywords = jsonschema.validators.Draft202012Validator.VALIDATORS
+    for error_type, (keyword, with_constraint, without) in _CONSTRAINED_PHRASES.items():
+        assert keyword in keywords, error_type
+        assert "{" not in without, error_type
+        assert with_constraint.count("{") == 1, error_type
+
+
+def test_exception_text_describes_a_wrapper_that_embeds_a_validation_error() -> None:
+    """`RuntimeError(f"... {e}") from e` carries the value in its own text."""
+    from pmcp.argument_errors import exception_text, safe_exc_info
+
+    s = _SENTINELS[1]
+    try:
+        try:
+            McpTaskInfo.model_validate({"task_id": {"v": s}})
+        except ValidationError as inner:
+            raise RuntimeError(f"task parse failed: {inner}") from inner
+    except RuntimeError as outer:
+        text = exception_text(outer)
+        assert text.startswith(
+            "RuntimeError: 1 validation error for McpTaskInfo: $.task_id: must be a string"
+        ), text
+        assert not any(form in text for form in _forbidden(s))
+        assert safe_exc_info(outer) is None
+    plain = RuntimeError("no validation here")
+    assert exception_text(plain) == "no validation here"
+    assert safe_exc_info(plain) is plain
+
+
+# --- rev 18: a wrapper of a value-bearing error is never rendered from its
+# own message (round-16 codex F001: `f"{e!r}"` evaded the substring check).
+# The grid is every leaf type in the registry x every way a wrapper's text
+# can be built from it x `__cause__` / `__context__` / `from None` x chain
+# depth 1-3 x every rendering surface. Two sentinels: one long, and one
+# shorter than any value-matching floor, so the rule cannot be value matching.
+
+
+def _leaf_pydantic(s: str) -> BaseException:
+    try:
+        McpTaskInfo.model_validate({"task_id": {"v": s}})
+    except ValidationError as error:
+        return error
+    raise AssertionError("no error")
+
+
+def _leaf_jsonschema(s: str) -> BaseException:
+    try:
+        jsonschema.validate(s, {"type": "integer"})
+    except jsonschema.ValidationError as error:
+        return error
+    raise AssertionError("no error")
+
+
+def _leaf_schema(s: str) -> BaseException:
+    try:
+        jsonschema.Draft202012Validator.check_schema({"type": s})
+    except jsonschema.SchemaError as error:
+        return error
+    raise AssertionError("no error")
+
+
+def _leaf_yaml(s: str) -> BaseException:
+    import yaml
+
+    try:
+        yaml.safe_load(f"a: b: {s}\n")
+    except yaml.YAMLError as error:
+        return error
+    raise AssertionError("no error")
+
+
+def _leaf_json(s: str) -> BaseException:
+    try:
+        json.loads("{" + s)
+    except json.JSONDecodeError as error:
+        return error
+    raise AssertionError("no error")
+
+
+_GRID_LEAVES: dict[str, Any] = {
+    "pydantic": _leaf_pydantic,
+    "jsonschema": _leaf_jsonschema,
+    "schema_error": _leaf_schema,
+    "yaml": _leaf_yaml,
+    "json": _leaf_json,
+}
+
+
+def _value_attr(error: BaseException) -> Any:
+    """The attribute of `error` that holds what it rejected."""
+    for name in ("instance", "doc"):
+        if hasattr(error, name):
+            return getattr(error, name)
+    if isinstance(error, ValidationError):
+        return error.errors()[0]["input"]
+    mark = getattr(error, "problem_mark", None)
+    return getattr(mark, "buffer", None)
+
+
+def _message_attr(error: BaseException) -> str:
+    """The error's own message field, as a wrapper would read it."""
+    if isinstance(error, ValidationError):
+        return str(error.errors())
+    for name in ("message", "msg"):
+        if isinstance(getattr(error, name, None), str):
+            return str(getattr(error, name))
+    return f"{getattr(error, 'problem', '')} {getattr(error, 'problem_mark', '')}"
+
+
+class _LateStr(Exception):
+    """A wrapper whose text is computed when rendered, from its cause."""
+
+    def __str__(self) -> str:
+        return f"late: {self.__cause__ or self.__context__!r}"
+
+
+_GRID_FORMS: dict[str, Any] = {
+    "fstring": lambda e: f"failed: {e}",
+    "fstring_r": lambda e: f"failed: {e!r}",
+    "fstring_s": lambda e: f"failed: {e!s}",
+    "fstring_a": lambda e: f"failed: {e!a}",
+    "percent_s": lambda e: "failed: %s" % (e,),
+    "percent_r": lambda e: "failed: %r" % (e,),
+    "format": lambda e: "failed: {}".format(e),
+    "format_r": lambda e: "failed: {!r}".format(e),
+    "builtin_format": lambda e: format(e),
+    "ascii": lambda e: ascii(e),
+    "slice": lambda e: str(e)[:60],
+    "repr_slice": lambda e: repr(e)[1:80],
+    "value_attr": lambda e: f"bad value {_value_attr(e)!r}",
+    "value_attr_s": lambda e: f"bad value {_value_attr(e)}",
+    "message_attr": lambda e: f"invalid: {_message_attr(e)}",
+    "args": lambda e: ("failed", e),
+    "late_str": None,
+    "empty": lambda e: "",
+    "unrelated": lambda e: "the request failed",
+}
+
+
+def _exception_group() -> type[Exception]:
+    """`ExceptionGroup` (3.11+), or anyio's backport on Python 3.10."""
+    import builtins
+
+    group = getattr(builtins, "ExceptionGroup", None)
+    if group is None:  # Python 3.10
+        from exceptiongroup import ExceptionGroup as group
+    return cast(type[Exception], group)
+
+
+def _wrap(form: str, inner: BaseException, link: str) -> BaseException:
+    """`inner` wrapped once: raised from it, inside its handler, from None,
+    or (rev 19, round-17 claude F002) held as the only member of an exception
+    group whose own message is the form's text, raised outside any handler,
+    so only the group's members reach it."""
+    kind: type[BaseException] = _LateStr if form == "late_str" else RuntimeError
+    message = None if form == "late_str" else _GRID_FORMS[form](inner)
+    args = (
+        message
+        if isinstance(message, tuple)
+        else (() if message is None else (message,))
+    )
+    if link == "group":
+        text = message if isinstance(message, str) else f"failed: {inner!r}"
+        group = _exception_group()(text, [cast(Exception, inner)])
+        try:
+            raise group
+        except BaseException as raised:
+            assert raised.__context__ is None and raised.__cause__ is None
+            return raised
+    try:
+        try:
+            raise inner
+        except BaseException as caught:
+            if link == "cause":
+                raise kind(*args) from caught
+            if link == "suppressed":
+                raise kind(*args) from None
+            raise kind(*args)
+    except BaseException as wrapped:
+        return wrapped
+    raise AssertionError("not raised")
+
+
+def _grid_surfaces(outer: BaseException) -> dict[str, str]:
+    """Every surface that renders `outer`, rendered."""
+    import asyncio
+
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.server import _described_errors
+
+    factory = logging.getLogRecordFactory()
+    exc_info = (type(outer), outer, outer.__traceback__)
+    records = {
+        "log_msg": factory("pmcp.grid", logging.ERROR, __file__, 1, outer, (), None),
+        "log_args": factory(
+            "pmcp.grid", logging.ERROR, __file__, 1, "failed: %r", (outer,), None
+        ),
+        "log_mapping": factory(
+            "pmcp.grid",
+            logging.ERROR,
+            __file__,
+            1,
+            "failed: %(e)s",
+            ({"e": [outer]},),
+            None,
+        ),
+        "log_exc_info": factory(
+            "pmcp.grid", logging.ERROR, __file__, 1, "failed", (), exc_info
+        ),
+    }
+    out = {name: _record_text(record) for name, record in records.items()}
+    out["exception_text"] = exception_text(outer)
+    out["safe_traceback_text"] = safe_traceback_text(outer)
+
+    async def reject() -> None:
+        raise outer
+
+    try:
+        asyncio.run(_described_errors(reject)())
+    except Exception as raised:
+        out["described_errors"] = f"{type(raised).__name__}: {raised}"
+    return out
+
+
+@pytest.mark.parametrize("sentinel", ["long", "short"])
+@pytest.mark.parametrize("depth", [1, 2, 3])
+@pytest.mark.parametrize("link", ["cause", "context", "suppressed", "group"])
+@pytest.mark.parametrize("leaf", sorted(_GRID_LEAVES))
+def test_a_wrapper_of_a_value_bearing_error_is_never_rendered_from_its_message(
+    leaf: str, link: str, depth: int, sentinel: str
+) -> None:
+    """Whatever form built the wrapper's text, however deep the chain and
+    whichever link holds the error, every surface renders the outermost
+    exception as `<class>: <the leaf's structural description>` and nothing
+    of the value."""
+    from pmcp.argument_errors import (
+        _qualified_name,
+        _validation_text,
+        exception_text,
+    )
+
+    s = _SENTINELS[1] if sentinel == "long" else "Qx7"
+    failures = []
+    for form in _GRID_FORMS:
+        inner = _GRID_LEAVES[leaf](s)
+        description = _validation_text(inner)
+        for _ in range(depth):
+            inner = _wrap(form, inner, link)
+        outer = inner
+        expected = f"{type(outer).__name__}: {description}"
+        surfaces = _grid_surfaces(outer)
+        if exception_text(outer) != expected:
+            failures.append((form, "exception_text", exception_text(outer)))
+        if surfaces.get("described_errors") != f"ValueError: {expected}":
+            failures.append(
+                (form, "described_errors", surfaces.get("described_errors"))
+            )
+        for surface, text in surfaces.items():
+            leaked = (
+                s in text
+                if sentinel == "short"
+                else any(piece in text for piece in _forbidden(s))
+            )
+            if leaked:
+                failures.append((form, surface, text[:200]))
+        last = surfaces["safe_traceback_text"].rstrip("\n").rsplit("\n", 1)[-1]
+        if last != f"{_qualified_name(type(outer))}: {description}":
+            failures.append((form, "traceback last line", last))
+    assert not failures, failures
+
+
+def test_the_value_bearing_registry_is_the_one_decision() -> None:
+    """Every leaf the grid raises is registered; pmcp's own `ParseError` is
+    the exemption, and a wrapper of it -- or of nothing registered -- keeps
+    its own message."""
+    import yaml
+
+    from pmcp.argument_errors import (
+        _is_validation_error,
+        _value_bearing_types,
+        _value_free_types,
+        exception_text,
+    )
+    from pmcp.parsing import ParseError, load_yaml
+
+    registered = _value_bearing_types()
+    for make in _GRID_LEAVES.values():
+        leaf = make("Qx7")
+        assert isinstance(leaf, registered) and _is_validation_error(leaf), leaf
+    assert _value_free_types() == (ParseError,)
+    with pytest.raises(ParseError) as raised:
+        load_yaml("a: b: Qx7\n", source="grid file")
+    assert isinstance(raised.value, yaml.YAMLError)
+    assert not _is_validation_error(raised.value)
+    for link in ("cause", "context", "suppressed"):
+        wrapped = _wrap("fstring", raised.value, link)
+        assert exception_text(wrapped) == f"failed: {raised.value}", link
+        plain = _wrap("fstring", KeyError("missing"), link)
+        assert exception_text(plain) == "failed: 'missing'", link
+
+
+@pytest.mark.parametrize("wrapped", [False, True])
+def test_a_group_member_value_never_reaches_a_log_or_traceback(wrapped: bool) -> None:
+    """Round-17 claude F002's binding test, as filed: a validation error that
+    is a member of an exception group -- bare, or inside a `{e!r}` wrapper --
+    reaches neither `logger.exception`, `exception_text` nor the traceback.
+    Only `_chain`'s walk over group members finds it."""
+    import io
+
+    import pmcp  # noqa: F401 - installs the record scrubber
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+
+    s = "SENTINEL_GROUP_MEMBER_VALUE_9137"
+    group_type = _exception_group()
+    try:
+        McpTaskInfo.model_validate({"task_id": {"v": s}})
+    except ValidationError as error:
+        member: BaseException = error
+    if wrapped:
+        try:
+            raise member
+        except BaseException as caught:
+            try:
+                raise RuntimeError(f"failed: {caught!r}") from caught
+            except RuntimeError as wrapper:
+                member = wrapper
+    try:
+        raise group_type("unhandled errors in a TaskGroup", [member])
+    except Exception as group:
+        stream = io.StringIO()
+        handler = logging.StreamHandler(stream)
+        logger = logging.getLogger("pmcp.finding_f002")
+        logger.addHandler(handler)
+        try:
+            logger.exception("transport owner failed")
+        finally:
+            logger.removeHandler(handler)
+        rendered = {
+            "log": stream.getvalue(),
+            "exception_text": exception_text(group),
+            "traceback": safe_traceback_text(group),
+        }
+    leaked = [name for name, text in rendered.items() if s in text]
+    assert not leaked, leaked
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_describe_exception_renders_a_grouped_validation_error_structurally(
+    family: str,
+) -> None:
+    """`describe_exception` flattens an anyio task group into its leaves --
+    the remote-transport paths' shape -- and each leaf goes through
+    `exception_text`, so a validation error inside a group is described,
+    not echoed."""
+    import builtins
+
+    from pmcp.client.manager import describe_exception
+
+    group_type = getattr(builtins, "ExceptionGroup", None)
+    if group_type is None:  # Python 3.10: anyio's backport
+        from exceptiongroup import ExceptionGroup as group_type
+    s = _FAMILIES[family][1]
+    with pytest.raises(ValidationError) as raised:
+        McpTaskInfo.model_validate({"task_id": {"v": s}})
+    leaf = raised.value
+    for group in (
+        group_type("unhandled errors in a TaskGroup", [leaf]),
+        group_type("unhandled errors in a TaskGroup", [RuntimeError("boom"), leaf]),
+    ):
+        text = describe_exception(group)
+        assert "validation error for McpTaskInfo: $.task_id: must be a string" in text
+        assert not any(form in text for form in _forbidden(s)), text
+
+
+# --- downstream data, through the real handlers (rev 2, board finding B1) ------
+#
+# A downstream server's payload is validated by pmcp's own models
+# (`McpTaskInfo` for every task payload; `ToolInfo`/`ResourceInfo`/
+# `PromptInfo`/`PromptArgumentInfo` for listings). The handlers that catch the
+# failure used to render `str(e)` -- pydantic text with `input_value` -- into
+# the response, the log and the audit-event buffer `gateway.health` exposes.
+# Only the transport is replaced here: `ClientManager._send_request`.
+
+_DOWNSTREAM = "svc"
+
+
+def _payload_keys(function: Any) -> list[str]:
+    """Every literal key `function` reads from a mapping it is handed:
+    `x.get("k")`, `x["k"]`, and `helper(x, "k")` -- derived from its source,
+    so a new field the parser reads is swept without editing this test."""
+    import ast
+    import inspect
+    import textwrap
+
+    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
+    keys: set[str] = set()
+    for node in ast.walk(tree):
+        if isinstance(node, ast.Call):
+            if (
+                isinstance(node.func, ast.Attribute)
+                and node.func.attr == "get"
+                and node.args
+                and isinstance(node.args[0], ast.Constant)
+                and isinstance(node.args[0].value, str)
+            ):
+                keys.add(node.args[0].value)
+            if (
+                len(node.args) == 2
+                and isinstance(node.args[1], ast.Constant)
+                and isinstance(node.args[1].value, str)
+            ):
+                keys.add(node.args[1].value)
+        elif (
+            isinstance(node, ast.Subscript)
+            and isinstance(node.slice, ast.Constant)
+            and isinstance(node.slice.value, str)
+        ):
+            keys.add(node.slice.value)
+    return sorted(keys)
+
+
+def _bad_values(s: str) -> dict[str, Any]:
+    return {
+        "string": s,
+        "object": {s: s, "k": [s]},
+        "array": [s, {s: s}],
+        "number-in-object": {"n": 7, s: [s]},
+    }
+
+
+def _task_positions() -> list[tuple[str, str]]:
+    """(payload key, bad-value shape) pairs the real task parser rejects with
+    a `ValidationError` -- found by running it, not by listing fields."""
+    from pmcp.client.manager import ClientManager
+
+    manager = ClientManager()
+    keys = _payload_keys(_task_parser_source())
+    assert {"ttl", "pollInterval", "createdAt", "status"} <= set(keys), keys
+    positions = []
+    for key in keys:
+        for shape, value in _bad_values(_SENTINELS[0]).items():
+            payload = (
+                {"taskId": "t", key: value}
+                if key not in ("taskId", "task_id")
+                else {key: value}
+            )
+            if _task_outcome(manager._task_info_from_payload, payload):
+                positions.append((key, shape))
+    assert len(positions) > 10, positions
+    return positions
+
+
+def _task_outcome(parser: Any, payload: dict) -> str | None:
+    """How the task parser refuses a value in `payload`: it raises
+    (`rejected`), or -- since Consiliency/pmcp#298 -- drops it and names the
+    field in `unusable_fields` (`dropped`). None if it accepts the value, or
+    finds no task at all (no string `taskId`: the answer is then not a task,
+    and is returned as the downstream's data, as before)."""
+    try:
+        task = parser(payload)
+    except ValidationError:
+        return "rejected"
+    if task is not None and task.unusable_fields:
+        return "dropped"
+    return None
+
+
+def _task_parser_source() -> Any:
+    """The task parser: the manager's `_task_info_from_payload` (static from
+    rev 15, when it became the only recogniser)."""
+    from pmcp.client.manager import ClientManager
+
+    return ClientManager._task_info_from_payload
+
+
+def _parse_task(payload: dict[str, Any]) -> Any:
+    """Run that parser (before rev 15 a method that reads nothing from
+    `self`)."""
+    import inspect
+
+    from pmcp.client.manager import ClientManager
+
+    raw = inspect.getattr_static(ClientManager, "_task_info_from_payload")
+    if isinstance(raw, staticmethod):
+        return ClientManager._task_info_from_payload(payload)
+    return ClientManager._task_info_from_payload(cast(Any, None), payload)
+
+
+def _task_id_aliases() -> list[str]:
+    """Every wire name the parser takes a task's id from, found by RUNNING it
+    on each key it reads (rev 15, round-14: derived from the parser's
+    grammar, not from a recogniser's)."""
+    return [k for k in _payload_keys(_task_parser_source()) if _parse_task({k: "t"})]
+
+
+def _renamed_id(payload: dict[str, Any], alias: str) -> dict[str, Any]:
+    if alias == "taskId" or "taskId" not in payload:
+        return dict(payload)
+    return {(alias if key == "taskId" else key): v for key, v in payload.items()}
+
+
+def _task_wrap(shape: str, alias: str) -> Any:
+    def wrap(p: dict[str, Any], method: str) -> Any:
+        task = _renamed_id(p, alias)
+        if shape == "flat":
+            return task
+        if shape == "nested" and method == "tasks/result":
+            return {"task": task, "result": {"content": []}}
+        return {"task": task}
+
+    return wrap
+
+
+#: Every shape in which a downstream answer carries a task, as the PARSER's
+#: grammar accepts it (rev 15): each id alias the parser reads, nested (with a
+#: sibling `result` on `tasks/result`), nested without one, and flat.
+_TASK_WRAPS: dict[str, Any] = {
+    f"{shape}-{alias}": _task_wrap(shape, alias)
+    for alias in _task_id_aliases()
+    for shape in ("nested", "nested-bare", "flat")
+}
+
+
+def _task_server(
+    tmp_path: Path, *, audited: bool
+) -> tuple[GatewayServer, Path | None, dict]:
+    """A server whose one downstream (`svc`) is task-capable and answers every
+    request with `state["payload"]` as its task."""
+    from unittest.mock import MagicMock
+
+    from pmcp.client.manager import ManagedClient
+    from pmcp.config.loader import make_tool_id
+    from pmcp.types import (
+        LocalMcpServerConfig,
+        ResolvedServerConfig,
+        RiskHint,
+        ServerStatus,
+        ServerStatusEnum,
+        ToolInfo,
+    )
+
+    server, audit_path = _server(tmp_path, audited=audited)
+    # The scoped policy allows two research servers; this one stands in.
+    policy = server._policy_manager
+    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
+    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
+    manager = server._client_manager
+    tool = ToolInfo(
+        tool_id=make_tool_id(_DOWNSTREAM, "run"),
+        server_name=_DOWNSTREAM,
+        tool_name="run",
+        description="run",
+        short_description="run",
+        input_schema={"type": "object", "properties": {}},
+        execution={"taskSupport": "required"},
+        tags=["svc"],
+        risk_hint=RiskHint.LOW,
+    )
+    manager._tools[tool.tool_id] = tool
+    status = ServerStatus(
+        name=_DOWNSTREAM,
+        status=ServerStatusEnum.ONLINE,
+        tool_count=1,
+        server_capabilities={"tasks": {"listChanged": True}},
+        protocol_version="2025-11-25",
+    )
+    manager._clients[_DOWNSTREAM] = ManagedClient(
+        config=ResolvedServerConfig(
+            name=_DOWNSTREAM,
+            source="custom",
+            config=LocalMcpServerConfig(command="svc"),
+        ),
+        is_remote=True,
+        write_stream=MagicMock(),
+        status=status,
+    )
+    manager._servers[_DOWNSTREAM] = status
+    state: dict[str, Any] = {"payload": {}, "methods": []}
+
+    async def send_request(managed: Any, method: str, params: Any, **_: Any) -> Any:
+        state["methods"].append(method)
+        payload = state["payload"]
+        wrap = state.get("wrap", "nested-taskId")
+        if method == "tasks/list":
+            return {"tasks": [_renamed_id(payload, wrap.rsplit("-", 1)[-1])]}
+        return _TASK_WRAPS[wrap](payload, method)
+
+    manager._send_request = send_request  # type: ignore[method-assign]
+    return server, audit_path, state
+
+
+def _task_calls() -> list[tuple[str, dict[str, Any], str]]:
+    """(gateway tool, arguments, downstream method) for every gateway tool
+    that parses a downstream task payload."""
+    target = {"server_name": _DOWNSTREAM, "task_id": "t"}
+    return [
+        ("gateway.tasks_list", {"server_name": _DOWNSTREAM}, "tasks/list"),
+        ("gateway.tasks_get", target, "tasks/get"),
+        ("gateway.tasks_result", target, "tasks/result"),
+        ("gateway.tasks_cancel", target, "tasks/cancel"),
+        (
+            "gateway.invoke",
+            {
+                "tool_id": f"{_DOWNSTREAM}::run",
+                "task": {"enabled": True},
+                **_correlations(),
+            },
+            "tools/call",
+        ),
+    ]
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
+async def test_no_downstream_value_reaches_a_response_log_or_audit(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    audited: bool,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    server, audit_path, state = _task_server(tmp_path, audited=audited)
+    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
+    positions = _task_positions()
+    parser = server._client_manager._task_info_from_payload
+
+    def rejects(key: str, value: Any) -> bool:
+        payload = {"taskId": "t", "status": "working", key: value}
+        return _task_outcome(parser, payload) is not None
+
+    rejected = expected = 0
+    for name, arguments, method in _task_calls():
+        for key, shape in positions:
+            for family, sentinels in _FAMILIES.items():
+                # A value the parser accepts (a digits-only `createdAt` is a
+                # number) is downstream data pmcp returns by design, not a
+                # rejection; only rejected values are this sweep's subject.
+                if not all(rejects(key, _bad_values(s)[shape]) for s in sentinels):
+                    continue
+                expected += 1
+                seen = []
+                for s in sentinels:
+                    state["payload"] = {
+                        "taskId": "t",
+                        "status": "working",
+                        key: _bad_values(s)[shape],
+                    }
+                    state["methods"].clear()
+                    server._client_manager._tasks.clear()
+                    if method == "tasks/cancel":
+                        # Cancel asks downstream only for a live, known task.
+                        server._client_manager._record_task(
+                            _DOWNSTREAM, McpTaskInfo(task_id="t", status="working")
+                        )
+                    mark = tap.start()
+                    result = await _call(server, name, arguments)
+                    response = "".join(block.text for block in result.content)
+                    observed = tap.since(mark, response)
+                    assert method in state["methods"], (name, state["methods"])
+                    assert observed.leaks(s) == [], (name, key, shape, family, observed)
+                    seen.append(observed)
+                # No vacuous pass: the payload was rejected, and said so --
+                # or (Consiliency/pmcp#298) the value was dropped, and the
+                # field is named as unusable.
+                assert re.search(
+                    r"validation errors? for McpTaskInfo: \$|\"unusable_fields\": \[\s*\"|"
+                    r"[Tt]ask not found",
+                    seen[0].response,
+                ), (
+                    name,
+                    key,
+                    seen[0].response,
+                )
+                assert not seen[0].differs(seen[1]), (
+                    name,
+                    key,
+                    shape,
+                    family,
+                    seen[0].differs(seen[1]),
+                )
+                assert _event_shape(seen[0].events) == _event_shape(seen[1].events)
+                rejected += 1
+    assert rejected == expected > len(_task_calls()) * len(positions), (
+        rejected,
+        expected,
+    )
+    # What `gateway.health` exposes (the audit-event buffer, among the rest).
+    health = "".join(
+        block.text for block in (await _call(server, "gateway.health", {})).content
+    )
+    await server.shutdown()
+    assert "audit_events" in health
+    for sentinels in _FAMILIES.values():
+        for s in sentinels:
+            assert not any(form in health for form in _forbidden(s))
+
+
+def _listing_parsers() -> list[tuple[str, Any, dict[str, Any], str | None]]:
+    """(label, real parser, a valid entry, the nested list its keys may also
+    sit in) for every downstream listing pmcp parses into its own models."""
+    from pmcp.client import manager
+
+    return [
+        (
+            "tools",
+            lambda entries: manager._parse_tool_entries("svc", entries, 100),
+            {"name": "t", "inputSchema": {"type": "object"}},
+            None,
+        ),
+        (
+            "resources",
+            lambda entries: manager._parse_resource_entries("svc", entries),
+            {"uri": "x://r"},
+            None,
+        ),
+        (
+            "prompts",
+            lambda entries: manager._parse_prompt_entries("svc", entries),
+            {"name": "p", "arguments": [{"name": "a"}]},
+            "arguments",
+        ),
+    ]
+
+
+@pytest.mark.parametrize("label", ["tools", "resources", "prompts"])
+def test_no_downstream_listing_value_reaches_the_log(
+    label: str,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+) -> None:
+    """The listing parsers skip an entry pmcp's model rejects and log why."""
+    from pmcp.client import manager
+
+    caplog.set_level(logging.DEBUG)
+    function = {
+        "tools": manager._parse_tool_entries,
+        "resources": manager._parse_resource_entries,
+        "prompts": manager._parse_prompt_entries,
+    }[label]
+    _, parse, valid, nested = next(p for p in _listing_parsers() if p[0] == label)
+    keys = [key for key in _payload_keys(function) if key]
+    tap = _Tap(typing.cast(Any, MagicMockServer()), None, caplog, capfd, recwarn)
+
+    def rejection(entry: dict[str, Any]) -> BaseException | None:
+        """What the real parser raised for `entry` (its `except` hands it to
+        `describe_exception`, recorded here and rendered as its type only)."""
+        with mock.patch.object(
+            manager, "describe_exception", side_effect=lambda exc: type(exc).__name__
+        ) as seen:
+            parse([entry])
+        return seen.call_args.args[0] if seen.call_args else None
+
+    rejected = 0
+    for key in keys:
+        for into_nested in [False, True] if nested else [False]:
+            for shape in _bad_values(_SENTINELS[0]):
+
+                def build(s: str) -> dict[str, Any]:
+                    entry = copy.deepcopy(valid)
+                    target = entry[nested][0] if into_nested else entry
+                    target[key] = _bad_values(s)[shape]
+                    return entry
+
+                # This sweep's subject is a validation error's text. An entry
+                # whose identity is unusable is skipped earlier, by
+                # `_required_identity`, and logged with `_entry_label` -- the
+                # downstream entry itself, by design (see the plan's
+                # Non-goals); an accepted value is indexed by design.
+                if not all(
+                    isinstance(rejection(build(s)), ValidationError)
+                    for sentinels in _FAMILIES.values()
+                    for s in sentinels
+                ):
+                    continue
+                for family, sentinels in _FAMILIES.items():
+                    pair = []
+                    for s in sentinels:
+                        mark = tap.start()
+                        parse([build(s)])
+                        observed = tap.since(mark, "")
+                        assert observed.leaks(s) == [], (
+                            label,
+                            key,
+                            shape,
+                            family,
+                            observed,
+                        )
+                        assert "validation error" in observed.log, observed.log
+                        pair.append(observed)
+                    assert pair[0].stable() == pair[1].stable(), (
+                        label,
+                        key,
+                        shape,
+                        family,
+                    )
+                rejected += 1
+    # No vacuous pass: pmcp's own model rejected some of the generated fields.
+    assert rejected > 3, (label, rejected)
+
+
+class MagicMockServer:
+    """A stand-in with no gateway tools, for `_Tap` outside a server."""
+
+    _gateway_tools = None
+
+
+@pytest.mark.asyncio
+async def test_a_connect_failure_carrying_a_validation_error_is_described(
+    tmp_path: Path,
+    monkeypatch: pytest.MonkeyPatch,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+) -> None:
+    """Dynamic half of the rev 2 board seat's surviving regression (a log
+    line in `_connect_with_retry` rendering `last_error`): a connect that
+    fails with a validation error, through retries, `connect_server`'s
+    result, the log and `gateway.health`."""
+    from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
+
+    caplog.set_level(logging.DEBUG)
+    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
+    server, _ = _server(tmp_path, audited=False)
+    manager = server._client_manager
+    tap = _Tap(server, None, caplog, capfd, recwarn)
+    config = ResolvedServerConfig(
+        name="flaky", source="custom", config=LocalMcpServerConfig(command="flaky")
+    )
+    seen = []
+    for family, sentinels in _FAMILIES.items():
+        for s in sentinels:
+
+            async def connect(_config: Any, s: str = s) -> None:
+                McpTaskInfo.model_validate({"task_id": {"v": s}})
+
+            monkeypatch.setattr(manager, "_connect_server", connect)
+            mark = tap.start()
+            errors = await manager.connect_server(config)
+            health = "".join(
+                b.text for b in (await _call(server, "gateway.health", {})).content
+            )
+            observed = tap.since(mark, json.dumps(errors) + health)
+            assert observed.leaks(s) == [], (family, observed)
+            assert (
+                "validation error for McpTaskInfo: $.task_id: must be a string"
+                in (errors[0])
+            ), errors
+            seen.append((json.dumps(errors), observed.log))
+    await server.shutdown()
+    assert all(item == seen[0] for item in seen[1:])
+
+
+# --- rev 12: values pmcp rejects by hand, without an exception ---------------
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("family", ("hex", "alpha", "token"))
+async def test_a_value_rejected_by_hand_is_described(
+    tmp_path: Path,
+    monkeypatch: pytest.MonkeyPatch,
+    fake_npm_registry: dict[str, str],
+    family: str,
+) -> None:
+    """A caller value a handler rejects by hand (a cancel id, an `auth_connect`
+    env var, a server's undeclarable env vars) is described, never echoed:
+    response text, structured fields, audit buffer."""
+    from pmcp.manifest.loader import Manifest, ServerConfig
+    from pmcp.policy.policy import PolicyManager
+    from pmcp.tools.handlers import GatewayTools
+    from tests.conftest import MockClientManager
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s)
+    seen: list[str] = []
+
+    # Rev 13 (round-12 B1): through the real `gateway.cancel`, not
+    # `cancel_request` alone -- the response's `request_id` field and the
+    # audit event's `server_name` copied the id the format check rejected.
+    # Each rejection: the id has no `::`, or its local id is not an integer
+    # (with the value on either side of the separator).
+    server, audit_path = _server(tmp_path, audited=True)
+    for request_id in (s, f"{s}::notint", f"srv::{s}", f"{s}::{s}"):
+        result = await _call(server, "gateway.cancel", {"request_id": request_id})
+        text = "".join(block.text for block in result.content)
+        payload = json.loads(text)
+        assert payload["status"] == "not_found", text
+        assert payload["request_id"] is None, text
+        event = server._gateway_tools._audit_events[-1]
+        assert event.method == "gateway.cancel" and event.server_name is None, event
+        seen.append(text)
+        seen.append(event.model_dump_json())
+    # The control: a well-formed id is accepted and kept (its lookup miss is
+    # Consiliency/pmcp#315's), so the nulling above is the format rule's.
+    result = await _call(server, "gateway.cancel", {"request_id": "nosuch::5"})
+    payload = json.loads("".join(block.text for block in result.content))
+    assert payload["request_id"] == "nosuch::5", payload
+    assert server._gateway_tools._audit_events[-1].server_name == "nosuch"
+    await server.shutdown()
+    assert audit_path is not None
+    seen.append(audit_path.read_text() if audit_path.exists() else "")
+
+    manifest = Manifest(
+        version="1.0",
+        cli_alternatives={},
+        servers={
+            "keyed": ServerConfig(
+                name="keyed",
+                description="d",
+                keywords=[],
+                install={},
+                command="uvx",
+                args=["keyed"],
+                requires_api_key=True,
+                env_var="KEYED_API_KEY",
+                env_instructions="Set KEYED_API_KEY",
+            )
+        },
+        discovery_queue_path=".mcp-gateway/discovery_queue.json",
+    )
+    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
+    monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
+    monkeypatch.setenv("HOME", str(tmp_path))
+    monkeypatch.chdir(tmp_path)
+    tools = GatewayTools(
+        client_manager=MockClientManager(),  # type: ignore[arg-type]
+        policy_manager=PolicyManager(),
+    )
+    upper = "".join(c for c in s.upper() if c.isalnum())
+    for env_var in (f"OTHER_{upper}", f"BAD-{upper}"):
+        result = await tools.auth_connect(
+            {
+                "server_name": "keyed",
+                "env_var": env_var,
+                "credential": "test-token",
+                "scope": "user",
+            }
+        )
+        assert result.ok is False
+        seen.append(result.model_dump_json())
+        forbidden |= _forbidden(env_var)
+    fake_npm_registry["@example/discovered-mcp"] = "1.0.0"
+    registered = await tools.register_discovered_server(
+        {
+            "server_name": "disc",
+            "package": "@example/discovered-mcp",
+            "env_vars": [f"NPM_CONFIG_{upper}"],
+        }
+    )
+    assert registered.registered is False
+    seen.append(registered.model_dump_json())
+    forbidden |= _forbidden(f"NPM_CONFIG_{upper}")
+    events = (await tools.health()).audit_events or []
+    seen.extend(event.model_dump_json() for event in events)
+    text = "\n".join(seen)
+    assert not any(form in text for form in forbidden), text[:600]
+
+
+# --- rev 13: every output or audit field that copies a caller input -----------
+
+#: Why each field that copies a caller input may hold it (round-12 B1, as a
+#: class). The rule is rev 12's: a value pmcp *rejects* is not copied back,
+#: into the response or the audit event. A value it accepts may be, and a
+#: lookup of an accepted value that misses is Consiliency/pmcp#315's.
+_NULLED = "rejected for its format: null / None on that path (rev 13, B1)"
+_REFUSED = "refused: null on the refusal paths (rev 12); copied on success"
+_LOOKUP = (
+    "accepted (any non-empty string); a miss is a lookup echo, Consiliency/pmcp#315"
+)
+_ACCEPTED = "copied only on the path that accepted it"
+_NEVER = "never rejected by hand"
+_DERIVED = "a value pmcp computed from the input, not the input"
+_COPIED_INPUT_TRIAGE: dict[tuple[str, str], str] = {
+    ("cancel", "CancelOutput.request_id"): _NULLED,
+    ("cancel", "GatewayAuditEvent.server_name"): _NULLED,
+    ("auth_connect", "AuthConnectOutput.env_var"): _REFUSED,
+    ("auth_connect", "AuthConnectOutput.server"): _LOOKUP,
+    ("auth_connect", "GatewayAuditEvent.server_name"): _LOOKUP,
+    ("auth_connect", "AuthConnectOutput.url_elicitation"): _ACCEPTED,
+    ("auth_connect", "UrlElicitationInfo.elicitation_id"): _ACCEPTED,
+    ("auth_connect", "UrlElicitationInfo.next_step"): _ACCEPTED,
+    ("auth_connect", "UrlElicitationInfo.url"): _ACCEPTED,
+    ("auth_connect", "UrlElicitationInfo.url_verified"): _DERIVED,
+    ("connect_server", "LifecycleServerOutput.server"): _LOOKUP,
+    ("connect_server", "_lifecycle_output.server"): _LOOKUP,
+    ("connect_server", "_lifecycle_output.message"): _LOOKUP,
+    ("disconnect_server", "LifecycleServerOutput.server"): _LOOKUP,
+    ("disconnect_server", "_lifecycle_output.server"): _LOOKUP,
+    ("disconnect_server", "_lifecycle_output.active_task_count"): _DERIVED,
+    ("disconnect_server", "_lifecycle_output.cancelled_task_count"): _DERIVED,
+    ("restart_server", "LifecycleServerOutput.server"): _LOOKUP,
+    ("restart_server", "_lifecycle_output.server"): _LOOKUP,
+    ("invoke", "InvokeOutput.tool_id"): _LOOKUP,
+    ("invoke", "GatewayAuditEvent.tool_id"): _LOOKUP,
+    ("invoke", "GatewayAuditEvent.error"): _LOOKUP,
+    ("invoke", "InvokeOutput.task"): _DERIVED,
+    ("invoke", "process_output.redact"): _DERIVED,
+    ("provision", "ProvisionOutput.server"): _LOOKUP,
+    ("update_server", "UpdateServerOutput.server"): _LOOKUP,
+    ("provision_status", "ProvisionJobStatus.job_id"): _LOOKUP,
+    ("register_discovered_server", "RegisterDiscoveredServerOutput.server_name"): (
+        "accepted; the refusals are of the package and env vars, described (rev 12)"
+    ),
+    ("register_discovered_server", "ServerConfig.env_var"): _ACCEPTED,
+    ("register_discovered_server", "ServerConfig.name"): _ACCEPTED,
+    ("register_discovered_server", "ServerConfig.package"): _ACCEPTED,
+    ("request_capability", "CapabilityResolution.candidates"): _DERIVED,
+    **{
+        ("request_capability", f"CLIResolution.{field}"): _DERIVED
+        for field in (
+            "available",
+            "check_command",
+            "description",
+            "examples",
+            "help_command",
+            "name",
+            "path",
+            "prefer_mcp_for",
+            "reason",
+        )
+    },
+    ("search_registry", "SearchRegistryOutput.query"): _NEVER,
+    ("submit_feedback", "SubmitFeedbackOutput.issue_title"): (
+        "never rejected; a refusal is of the destination or credential, and "
+        "the text is returned for filing by hand"
+    ),
+    ("submit_feedback", "SubmitFeedbackOutput.issue_body"): (
+        "never rejected; a refusal is of the destination or credential, and "
+        "the text is returned for filing by hand"
+    ),
+    ("submit_feedback", "SubmitFeedbackOutput.issue_url"): _DERIVED,
+    ("submit_feedback", "SubmitFeedbackOutput.repository"): _DERIVED,
+    ("sync_environment", "SyncEnvironmentOutput.platform"): _ACCEPTED,
+    ("sync_environment", "SyncEnvironmentOutput.detected_clis"): _NEVER,
+    ("tasks_list", "GatewayAuditEvent.server_name"): _LOOKUP,
+    ("tasks_get", "GatewayAuditEvent.server_name"): _LOOKUP,
+    ("tasks_get", "GatewayAuditEvent.task_id"): _LOOKUP,
+    ("tasks_result", "GatewayAuditEvent.server_name"): _LOOKUP,
+    ("tasks_result", "GatewayAuditEvent.task_id"): _LOOKUP,
+    ("tasks_result", "process_output.redact"): _DERIVED,
+    ("tasks_cancel", "GatewayAuditEvent.server_name"): _LOOKUP,
+    ("tasks_cancel", "GatewayAuditEvent.task_id"): _LOOKUP,
+}
+
+
+def _gateway_tools_class() -> Any:
+    import ast
+
+    import pmcp.tools.handlers as handlers_module
+
+    tree = ast.parse(Path(handlers_module.__file__).read_text())
+    return next(
+        node
+        for node in tree.body
+        if isinstance(node, ast.ClassDef) and node.name == "GatewayTools"
+    )
+
+
+def _mirrored_fields() -> set[tuple[str, str]]:
+    """By the models: each tool's return model's fields named as one of its
+    input model's fields (`server_name` is returned as `server`)."""
+    import ast
+
+    import pmcp.types as types_module
+
+    found: set[tuple[str, str]] = set()
+    for fn in _gateway_tools_class().body:
+        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name.startswith("_"):
+            continue
+        output = getattr(types_module, ast.unparse(fn.returns), None)
+        if output is None:
+            continue
+        for call in ast.walk(fn):
+            if not (
+                isinstance(call, ast.Call)
+                and isinstance(call.func, ast.Attribute)
+                and call.func.attr == "model_validate"
+                and isinstance(call.func.value, ast.Name)
+                and call.func.value.id.endswith("Input")
+            ):
+                continue
+            names = set(getattr(types_module, call.func.value.id).model_fields)
+            if "server_name" in names:
+                names.add("server")
+            found |= {
+                (fn.name, f"{output.__name__}.{field}")
+                for field in output.model_fields
+                if field in names
+            }
+    return found
+
+
+def _copied_inputs() -> set[tuple[str, str]]:
+    """By the code: each keyword of a model construction, an `*_output`
+    helper call or an `_audit` call whose value is the input itself
+    (`parsed.<field>`), a conditional on it, or a local bound from it by an
+    attribute, boolean or conditional expression or a module-level parse
+    function (`parse_request_id(parsed.request_id)`)."""
+    import ast
+
+    def reads(node: ast.AST, bound: dict[str, str]) -> set[str]:
+        out: set[str] = set()
+        for sub in ast.walk(node):
+            if (
+                isinstance(sub, ast.Attribute)
+                and isinstance(sub.value, ast.Name)
+                and sub.value.id == "parsed"
+            ):
+                out.add(sub.attr)
+            elif isinstance(sub, ast.Name) and sub.id in bound:
+                out.add(bound[sub.id])
+        return out
+
+    found: set[tuple[str, str]] = set()
+    for fn in _gateway_tools_class().body:
+        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name.startswith("_"):
+            continue
+        bound: dict[str, str] = {}
+        for node in ast.walk(fn):
+            if not (
+                isinstance(node, ast.Assign)
+                and len(node.targets) == 1
+                and isinstance(node.targets[0], ast.Name)
+            ):
+                continue
+            value = node.value
+            if isinstance(value, (ast.Attribute, ast.BoolOp, ast.IfExp)) or (
+                isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
+            ):
+                for name in reads(value, dict(bound)):
+                    bound[node.targets[0].id] = name
+        for node in ast.walk(fn):
+            if not isinstance(node, ast.Call):
+                continue
+            if isinstance(node.func, ast.Name) and node.func.id[:1].isupper():
+                label = node.func.id
+            elif isinstance(node.func, ast.Attribute) and node.func.attr == "_audit":
+                label = "GatewayAuditEvent"
+            elif isinstance(node.func, ast.Attribute) and node.func.attr.endswith(
+                "_output"
+            ):
+                label = node.func.attr
+            else:
+                continue
+            for keyword in node.keywords:
+                value = keyword.value
+                if keyword.arg is None or not isinstance(
+                    value, (ast.Attribute, ast.Name, ast.IfExp, ast.Subscript)
+                ):
+                    continue
+                if reads(value, bound):
+                    found.add((fn.name, f"{label}.{keyword.arg}"))
+    return found
+
+
+def test_every_field_that_copies_a_caller_input_is_triaged() -> None:
+    """Round-12 B1 as a class: `gateway.cancel` copied a request id it had
+    rejected into its response and its audit event. Every output or audit
+    field that copies a caller input -- found two ways, by the models and by
+    the code -- has a reason it may hold the value, and the table has no
+    stale entry. A new copy fails here until it is triaged."""
+    found = _mirrored_fields() | _copied_inputs()
+    assert found == set(_COPIED_INPUT_TRIAGE), (
+        sorted(found - set(_COPIED_INPUT_TRIAGE)),
+        sorted(set(_COPIED_INPUT_TRIAGE) - found),
+    )
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("wrap", sorted(_TASK_WRAPS))
+async def test_no_dropped_task_hint_reaches_the_answer_in_any_task_shape(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    wrap: str,
+) -> None:
+    """A task hint pmcp drops as unusable is in no channel of any task tool's
+    answer, in every shape the parser reads (each id alias, nested or flat),
+    at every position; two sentinel lengths give one size."""
+    caplog.set_level(logging.DEBUG)
+    probe = {"taskId": "t", "status": "working"}
+    for method in ("tools/call", "tasks/get", "tasks/result", "tasks/cancel"):
+        answer = _TASK_WRAPS[wrap](probe, method)
+        candidate = answer["task"] if "task" in answer else answer
+        assert _parse_task(candidate) is not None, (wrap, method)
+    server, audit_path, state = _task_server(tmp_path, audited=False)
+    state["wrap"] = wrap
+    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
+    parser = server._client_manager._task_info_from_payload
+    dropped = [
+        (key, shape)
+        for key, shape in _task_positions()
+        if _task_outcome(
+            parser, {"taskId": "t", "status": "working", key: _bad_values("v")[shape]}
+        )
+        == "dropped"
+    ]
+    assert dropped, "no dropped position"
+    cases = 0
+    for name, arguments, method in _task_calls():
+        for key, shape in dropped:
+            for family, sentinels in _FAMILIES.items():
+                # A value the parser accepts (a digits-only `createdAt` is a
+                # number) is data pmcp returns by design, not a dropped hint.
+                if not all(
+                    _task_outcome(
+                        parser,
+                        {
+                            "taskId": "t",
+                            "status": "working",
+                            key: _bad_values(s)[shape],
+                        },
+                    )
+                    == "dropped"
+                    for s in sentinels
+                ):
+                    continue
+                seen = []
+                for s in sentinels:
+                    state["payload"] = {
+                        "taskId": "t",
+                        "status": "working",
+                        key: _bad_values(s)[shape],
+                    }
+                    server._client_manager._tasks.clear()
+                    if method == "tasks/cancel":
+                        server._client_manager._record_task(
+                            _DOWNSTREAM, McpTaskInfo(task_id="t", status="working")
+                        )
+                    mark = tap.start()
+                    result = await _call(server, name, arguments)
+                    response = "".join(block.text for block in result.content)
+                    observed = tap.since(mark, response)
+                    assert observed.leaks(s) == [], (wrap, name, key, shape, family)
+                    seen.append(observed)
+                assert not seen[0].differs(seen[1]), (
+                    wrap,
+                    name,
+                    key,
+                    shape,
+                    family,
+                    seen[0].differs(seen[1]),
+                )
+                cases += 1
+    await server.shutdown()
+    assert cases >= 5 * len(dropped) * (len(_FAMILIES) - 1), cases
+
+
+# ---------------------------------------------------------------------------
+# Rev 15 (round-14 board): the recogniser IS the parser
+# ---------------------------------------------------------------------------
+
+
+def _drops_alone(key: str, value: Any) -> bool:
+    """The parser's own verdict: it drops `value` at `key`, the only hint."""
+    info = _parse_task({"taskId": "t", key: value})
+    return info is not None and key not in info.raw
+
+
+def _usable_probe(key: str) -> Any:
+    for value in (5, 5.0, "working", "a message"):
+        info = _parse_task({"taskId": "t", key: value})
+        if info is not None and not info.unusable_fields and key in info.raw:
+            return value
+    return None
+
+
+def _generated_task_answers() -> list[tuple[str, dict[str, Any]]]:
+    """Answers from the parser's grammar: each id it reads, and ids it does
+    not (absent, empty, integer, null, object); each other key it reads with
+    a dropped value, a null, a usable value, and a usable value beside a
+    dropped one; nested beside result data, nested bare, and flat. Plus
+    codex's round-14 falsifier (data under `task`, no id)."""
+    ids = [(a, {a: "t"}) for a in _task_id_aliases()] + [
+        ("no-id", {}),
+        ("empty-id", {"taskId": ""}),
+        ("int-id", {"taskId": 5}),
+        ("null-id", {"taskId": None}),
+        ("object-id", {"task_id": {"x": 1}}),
+    ]
+    keys = [k for k in _payload_keys(_task_parser_source()) if k not in dict(ids)]
+    hints: list[tuple[str, dict[str, Any]]] = [("none", {})]
+    hints.append(("business", {"statusMessage": {"detail": "business-data"}}))
+    for key in keys:
+        hints += [(f"{key}={s}", {key: v}) for s, v in _bad_values("S").items()]
+        hints.append((f"{key}=null", {key: None}))
+        usable = _usable_probe(key)
+        if usable is not None:
+            hints.append((f"{key}=usable", {key: usable}))
+            hints += [
+                (f"{key}=usable+{o}=S", {key: usable, o: "S"})
+                for o in keys
+                if o != key and _usable_probe(o) is not None
+            ]
+    answers = []
+    for id_name, id_part in ids:
+        for hint_name, hint_part in hints:
+            task = {**id_part, "status": "working", **hint_part}
+            name = f"{id_name}/{hint_name}"
+            answers.append((f"nested/{name}", {"task": task, "content": []}))
+            answers.append((f"nested-bare/{name}", {"task": dict(task)}))
+            answers.append((f"flat/{name}", dict(task)))
+    return answers
+
+
+@pytest.mark.asyncio
+async def test_the_normaliser_acts_iff_the_parser_accepts_on_every_task_op(
+    tmp_path: Path,
+) -> None:
+    """For every generated answer, through every manager task op: the
+    normaliser acts iff the parser parses; it removes exactly the keys the
+    parser drops (per wire key) and leaves the rest."""
+    from pmcp.client.manager import _TASK_WIRE_KEYS
+    from pmcp.types import McpTaskInfo
+
+    server, _, _ = _task_server(tmp_path, audited=False)
+    manager = server._client_manager
+    reply: dict[str, Any] = {}
+
+    async def send_request(managed: Any, method: str, params: Any, **_: Any) -> Any:
+        task = reply.get("task") if isinstance(reply.get("task"), dict) else reply
+        return copy.deepcopy({"tasks": [task]} if method == "tasks/list" else reply)
+
+    manager._send_request = send_request  # type: ignore[method-assign]
+    answers = _generated_task_answers()
+    acted = 0
+    for name, answer in answers:
+        reply.clear()
+        reply.update(copy.deepcopy(answer))
+        nested = isinstance(answer.get("task"), dict)
+        candidate = answer["task"] if nested else answer
+        info = _parse_task(candidate)
+        expected: Any = answer
+        if info is not None:
+            acted += 1
+            expected = {**answer, "task": info.raw} if nested else info.raw
+            assert set(candidate) - set(info.raw) == {
+                k for k in candidate if _drops_alone(k, candidate[k])
+            }, name
+            for field in info.unusable_fields:
+                for key in _TASK_WIRE_KEYS.get(field, ()):
+                    assert candidate.get(key) is None or key not in info.raw, name
+        manager._tasks.clear()
+        got = await manager.call_tool(f"{_DOWNSTREAM}::run", {}, task={"enabled": True})
+        assert got == expected and bool(manager._tasks) is (info is not None), name
+        manager._tasks.clear()
+        if info is None:
+            with pytest.raises(KeyError):
+                await manager.get_task(_DOWNSTREAM, "t")
+        else:
+            assert (await manager.get_task(_DOWNSTREAM, "t")).raw == info.raw, name
+        listed = await manager.list_tasks(_DOWNSTREAM)
+        raws = [t["raw"] for t in listed["tasks"]]
+        assert raws == ([info.raw] if info is not None else []), name
+        if nested or info is not None:  # otherwise main's rule polls tasks/get
+            assert await manager.get_task_result(_DOWNSTREAM, "t") == expected, name
+        manager._tasks.clear()
+        manager._record_task(_DOWNSTREAM, McpTaskInfo(task_id="t", status="working"))
+        _, record, _ = await manager.cancel_task(_DOWNSTREAM, "t")
+        assert record is not None
+        assert record.raw == (info.raw if info is not None else {}), name
+    await server.shutdown()
+    assert 0 < acted < len(answers) and len(answers) > 1000, (acted, len(answers))
+
+
+async def _answer_text(root: Path, tool: str, reply: Any) -> str:
+    from pmcp.types import McpTaskInfo
+
+    root.mkdir()
+    server, _, _ = _task_server(root, audited=False)
+
+    async def send_request(managed: Any, method: str, params: Any, **_: Any) -> Any:
+        return copy.deepcopy(reply)
+
+    server._client_manager._send_request = send_request  # type: ignore[method-assign]
+    if tool == "gateway.invoke":
+        args = {"tool_id": f"{_DOWNSTREAM}::run", "task": {"enabled": True}}
+        args.update(_correlations())
+    else:
+        args = {"server_name": _DOWNSTREAM, "task_id": "t"}
+        task = McpTaskInfo(task_id="t", status="working")
+        server._client_manager._record_task(_DOWNSTREAM, task)
+    result = await _call(server, tool, args)
+    if tool == "gateway.tasks_cancel":
+        record = server._client_manager.get_task_record(_DOWNSTREAM, "t")
+        assert record is not None and record.raw == {}, record
+    await server.shutdown()
+    return "".join(block.text for block in result.content)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("tool", ["gateway.invoke", "gateway.tasks_result"])
+async def test_task_result_preserves_non_task_data(tmp_path: Path, tool: str) -> None:
+    """Round-14 codex F024-F027: a `task` the parser does not read (no id) is
+    the downstream's data, kept whole as on main; rev 14 deleted from it."""
+    reply = {"task": {"statusMessage": {"detail": "business-data"}}, "content": []}
+    assert "business-data" in await _answer_text(tmp_path / "a", tool, reply)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize(
+    ("tool", "reply"),
+    [
+        # round-14 grok F002/F003, claude F002: a flat snake-case task.
+        ("gateway.invoke", {"task_id": "t", "status": "working", "ttl": "@S@"}),
+        ("gateway.tasks_result", {"task_id": "t", "status": "working", "ttl": "@S@"}),
+        # round-14 claude F001: the cancel fallback copied the whole answer.
+        ("gateway.tasks_cancel", {"taskId": 5, "status": "cancelled", "ttl": "@S@"}),
+        ("gateway.tasks_cancel", {"task": {"taskId": 5, "ttl": "@S@"}}),
+    ],
+)
+async def test_a_dropped_task_hint_leaves_by_no_alias_or_fallback(
+    tmp_path: Path, tool: str, reply: dict[str, Any]
+) -> None:
+    sizes = []
+    for s in ("violetcanaryrejected", "violetcanaryrejected12345678"):
+        sent = json.loads(json.dumps(reply).replace("@S@", s))
+        text = await _answer_text(tmp_path / s, tool, sent)
+        assert s not in text, text
+        sizes.append(json.loads(text).get("raw_size_estimate"))
+    assert sizes[0] == sizes[1], sizes
+
+
+# ---------------------------------------------------------------------------
+# Rev 17 (round-15 board): task handling gated on the call's effective mode
+# ---------------------------------------------------------------------------
+
+
+async def _invoke_as(
+    root: Path, support: Any, requested: bool, answer: Any, recorded: bool = True
+) -> Any:
+    """`gateway.invoke` on `svc::run` (taskSupport `support`, None: absent)
+    with task "t" recorded by another call if `recorded`; the downstream answers
+    `answer`. Returns (the output, the server's policy manager)."""
+    from pmcp.types import McpTaskInfo
+
+    root.mkdir()
+    server, _, _ = _task_server(root, audited=False)
+    manager = server._client_manager
+    tool = next(iter(manager._tools.values()))
+    tool.execution = {} if support is None else {"taskSupport": support}
+
+    async def send(managed: Any, method: str, params: Any, **_: Any) -> Any:
+        return copy.deepcopy(answer)
+
+    manager._send_request = send  # type: ignore[method-assign]
+    if recorded:
+        task = McpTaskInfo(task_id="t", status="working")
+        manager._record_task(_DOWNSTREAM, task, tool_id="o")
+    args: dict[str, Any] = {"tool_id": f"{_DOWNSTREAM}::run", **_correlations()}
+    if requested:
+        args["task"] = {"enabled": True}
+    out = await server._gateway_tools.invoke(args)
+    await server.shutdown()
+    return out, server._policy_manager
+
+
+def _sized_as_returned(out: Any, policy: Any) -> bool:
+    return (
+        out.raw_size_estimate
+        == policy.process_output(out.result, redact=False)["raw_size"]
+    )
+
+
+@pytest.mark.asyncio
+async def test_task_handling_follows_the_calls_effective_task_mode(
+    tmp_path: Path,
+) -> None:
+    """Round-15 codex F001, claude F003: over taskSupport x requested x shape
+    (nested, flat, not a task): a call that is not a task gets its answer
+    back whole, sized as returned; a task call's dropped hint is neither
+    returned nor sized; content is never lost to a registry lookup."""
+    kept = [{"type": "text", "text": "fresh report"}]
+    cases = 0
+    for support, requested, shape, recorded in itertools.product(
+        ("required", "optional", "forbidden", None),
+        (True, False),
+        ("nested", "flat", "data"),
+        (True, False),
+    ):
+        name, outs = (support, requested, shape, recorded), []
+        for s in ("violetcanaryrejected", "violetcanaryrejected12345"):
+            answer = {
+                "nested": {"task": {"taskId": "t", "ttl": s}, "content": kept},
+                "flat": {"task_id": "t", "ttl": s, "content": kept},
+                "data": {"content": kept, "structuredContent": {"ttl": s}},
+            }[shape]
+            root = tmp_path / f"{support}{requested}{shape}{recorded}{len(s)}"
+            out = await _invoke_as(root, support, requested, answer, recorded)
+            outs.append((s, answer, *out))
+        if requested and support in ("forbidden", None):
+            assert not any(o.ok for _, _, o, _ in outs), name
+            continue
+        as_task = (support == "required" or requested) and shape != "data"
+        for s, answer, out, policy in outs:
+            if as_task:
+                assert out.result is None and out.task is not None, name
+                assert s not in out.model_dump_json(), name
+            else:
+                assert out.result == answer and out.task is None, name
+                assert _sized_as_returned(out, policy), name
+        if as_task:
+            assert len({o.raw_size_estimate for _, _, o, _ in outs}) == 1, name
+        cases += 1
+    assert cases == 36, cases
+
+
+@pytest.mark.asyncio
+async def test_sync_result_survives_cached_task_id(tmp_path: Path) -> None:
+    """Round-15 codex F001's falsifier: a `forbidden` tool's synchronous
+    answer naming a recorded task id keeps its content (as main does)."""
+    payload = {"task_id": "t", "content": [{"type": "text", "text": "fresh report"}]}
+    out, _ = await _invoke_as(tmp_path / "f", "forbidden", False, payload)
+    assert out.ok and out.result == payload and out.task is None
+
+
+@pytest.mark.asyncio
+async def test_an_unrequested_task_answer_is_sized_as_returned(tmp_path: Path) -> None:
+    """Round-15 claude F003, restated for rev 17's rule: an `optional` tool,
+    no task requested, a colliding task answer: not a task to pmcp, returned
+    whole and sized from exactly what is returned."""
+    for s in ("ghp_a", "ghp_a" + "x" * 40):
+        answer = {"task": {"taskId": "t", "status": "working", "ttl": s}}
+        out, policy = await _invoke_as(
+            tmp_path / str(len(s)), "optional", False, answer
+        )
+        assert out.ok and out.task is None and out.result == answer
+        assert _sized_as_returned(out, policy)
````

### Patch — `tests/test_auth_operator_messages.py`

````diff
--- a/tests/test_auth_operator_messages.py
+++ b/tests/test_auth_operator_messages.py
@@ -1078 +1078,4 @@
-    assert hits == []
+    # Consiliency/pmcp#297's record scrubber is a record factory, but it only
+    # rewrites a record that carries a validation or parse error; the sink
+    # test above shows every auth message passes through it intact.
+    assert hits == ["argument_errors.py: setLogRecordFactory("]
````

### Patch — `tests/test_downstream_frame_echo.py`

````diff
--- /dev/null
+++ b/tests/test_downstream_frame_echo.py
@@ -0,0 +1,1799 @@
+"""A downstream's malformed JSON-RPC reply never echoes into pmcp's output
+(Consiliency/pmcp#297, rev 3).
+
+The MCP SDK's client transports validate every frame a downstream sends. For
+one they reject, the streamable-HTTP transport synthesises a JSON-RPC
+`-32700` whose message is pydantic's text, rejected value included
+(`mcp/client/streamable_http.py:188,407`), and every SDK transport logs the
+failure with a traceback. The rev 2 board seat showed that value reaching the
+`gateway.invoke` response, `gateway.health` and the log.
+
+This sweep runs a **real** downstream on every transport pmcp speaks -- the
+streamable-HTTP transport answering with JSON or with an event stream, the
+legacy SSE transport, and stdio -- and connects it through the real
+`ClientManager`. The downstream answers every request normally except one,
+which it answers with a malformed frame carrying a sentinel. That covers
+every request pmcp sends (`initialize`, the three listings, `tools/call`,
+`resources/read`, `prompts/get`, `tasks/*`) and every envelope member the
+SDK validates, made wrong: `result` not an object, `error.code` not an
+integer, `error.message` not a string, `jsonrpc` not `"2.0"`, and a body
+that is not JSON at all.
+
+The value must not reach the response (unless pmcp accepted the frame and
+returns its result as the product, which only stdio does, because pmcp parses
+stdio frames without a model), the log (raw records, tracebacks,
+`extra=`), `gateway.health`, stdout/stderr or warnings. One exact exemption
+applies: stdio's `Non-JSON output` DEBUG line, which logs the downstream's
+own non-protocol output by design (see the plan's *Non-goals*).
+"""
+
+from __future__ import annotations
+
+import asyncio
+import json
+import logging
+import queue
+import re
+import sys
+import textwrap
+import threading
+import typing
+from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
+from pathlib import Path
+from typing import Any
+
+import pytest
+from mcp.types import (
+    GetPromptRequestParams,
+    ReadResourceRequestParams,
+)
+
+from pmcp.types import (
+    LocalMcpServerConfig,
+    RemoteMcpServerConfig,
+    ResolvedServerConfig,
+)
+from tests.test_argument_error_echo import (
+    _FAMILIES,
+    _call,
+    _forbidden,
+    _record_stable,
+    _record_text,
+    _server,
+    _Tap,
+)
+from tests.test_scoped_advisor_audit import _make_ctx
+
+_SHAPES = (
+    "result-type",
+    "error-code",
+    "error-message",
+    "jsonrpc",
+    "not-json",
+    # rev 8 (round-7 codex F028-F034): JSON-RPC-shaped frames a parser
+    # rejects -- on a limit (nesting depth, an integer's digit count) or on
+    # syntax. Each fails BEFORE the sentinel, so the failure's position does
+    # not depend on the sentinel's length (the pair differential holds).
+    "json-deep",
+    "json-bigint",
+    "json-syntax",
+    # rev 10 (round-9 codex P1): a VALID envelope whose payload carries the
+    # sentinel where the handler's own model rejects it -- the transport
+    # accepts it, the SDK used to log it at DEBUG, then pmcp rejects it.
+    "payload",
+    # rev 12 (round-11 codex P1): a pagination cursor pmcp rejects by hand,
+    # on every listing kind -- an object under `nextCursor`, a list under
+    # `next_cursor`, and a string cursor the downstream repeats.
+    "cursor-object",
+    "cursor-list",
+    "cursor-repeat",
+    # rev 13 (round-12 codex B2): an error envelope that breaks one rule of
+    # JSON-RPC 2.0 elsewhere, with the sentinel as its `message` -- the text a
+    # caller renders. `jsonrpc` "1.0", no `jsonrpc`, both `result` and
+    # `error`, and a bool `code` (which the SDK's model coerces to 1). The
+    # property test below takes every rule; these four take every caller and
+    # transport.
+    "envelope-version",
+    "envelope-unversioned",
+    "envelope-both",
+    "envelope-code",
+)
+#: The shapes whose frame pmcp rejects -- the parser, or (rev 13) the
+#: envelope check, which leaves the request waiting: stdio then also sends the
+#: real reply, so the request completes instead of timing out.
+_REJECTED_SHAPES = (
+    # rev 13: the envelope check drops these on stdio too (rev 12's
+    # dispatcher acted on them), so they are followed by the real reply.
+    "result-type",
+    "error-code",
+    "error-message",
+    "jsonrpc",
+    "not-json",
+    "json-deep",
+    "json-bigint",
+    "json-syntax",
+    "envelope-version",
+    "envelope-unversioned",
+    "envelope-both",
+    "envelope-code",
+)
+_METHODS = (
+    "initialize",
+    "tools/list",
+    "resources/list",
+    "prompts/list",
+    "tools/call",
+    "resources/read",
+    "prompts/get",
+    "tasks/list",
+    "tasks/get",
+    "tasks/result",
+    "tasks/cancel",
+)
+_TRANSPORTS = ("http-json", "http-sse", "legacy-sse", "stdio")
+#: Three sentinel families: the shape-conditional axis is the argument
+#: sweep's; here the transports and envelope positions are.
+_FRAME_FAMILIES = ("hex", "alpha", "unicode")
+
+#: The downstream's logic, shared by the HTTP handler and the stdio script:
+#: answer `request` normally, unless it is the targeted method.
+_DOWNSTREAM_LOGIC = textwrap.dedent(
+    '''
+    import json
+
+    def normal(method, params):
+        if method == "initialize":
+            return {
+                "protocolVersion": params.get("protocolVersion", "2025-11-25"),
+                "capabilities": {"tools": {}, "resources": {}, "prompts": {}, "tasks": {}},
+                "serverInfo": {"name": "frames", "version": "1"},
+            }
+        if method == "tools/list":
+            return {"tools": [{"name": "run", "inputSchema": {"type": "object"},
+                               "execution": {"taskSupport": "optional"}}]}
+        if method == "resources/list":
+            return {"resources": [{"uri": "x://r", "name": "r"}]}
+        if method == "prompts/list":
+            return {"prompts": [{"name": "p"}]}
+        if method == "tools/call":
+            if params.get("task"):
+                return {"task": {"taskId": "t", "status": "working"}}
+            return {"content": [{"type": "text", "text": "ok"}]}
+        if method == "resources/read":
+            return {"contents": [{"uri": "x://r", "text": "ok"}]}
+        if method == "prompts/get":
+            return {"messages": []}
+        if method == "tasks/list":
+            return {"tasks": [{"taskId": "t", "status": "working"}]}
+        if method in ("tasks/get", "tasks/cancel"):
+            return {"task": {"taskId": "t", "status": "working"}}
+        if method == "tasks/result":
+            return {"task": {"taskId": "t", "status": "completed"}, "result": {"content": []}}
+        return {}
+
+    def invalid_payload(method, s, params):
+        """A result the envelope accepts but the handler rejects: the
+        sentinel where pmcp's model for that method requires another type."""
+        bad = {"x": s}
+        if method == "initialize":
+            result = normal(method, params)
+            result["serverInfo"] = {"name": bad, "version": "1"}
+            return result
+        return {
+            "tools/list": {"tools": [{"name": "run", "inputSchema": s}]},
+            "resources/list": {"resources": [{"uri": "x://r", "name": bad}]},
+            "prompts/list": {"prompts": [{"name": bad}]},
+            "tools/call": {"content": [{"type": "text", "text": bad}]},
+            "resources/read": {"contents": [{"uri": "x://r", "text": bad}]},
+            "prompts/get": {"messages": [{"role": bad, "content": {"type": "text", "text": "m"}}]},
+            "tasks/list": {"tasks": [{"taskId": "t", "status": "working", "ttl": s}]},
+            "tasks/get": {"task": {"taskId": "t", "status": "working", "ttl": s}},
+            "tasks/cancel": {"task": {"taskId": "t", "status": "working", "ttl": s}},
+            "tasks/result": {"task": {"taskId": "t", "status": "completed", "ttl": s}, "result": {"content": []}},
+        }[method]
+
+    def reply(request, state):
+        """The raw reply body (bytes) for `request`, or None for a notification."""
+        method, rid = request.get("method"), request.get("id")
+        if rid is None:
+            return None
+        s = state["s"]
+        if method != state["method"]:
+            frame = {"jsonrpc": "2.0", "id": rid, "result": normal(method, request.get("params") or {})}
+        elif state["shape"] == "result-type":
+            frame = {"jsonrpc": "2.0", "id": rid, "result": s}
+        elif state["shape"] == "error-code":
+            frame = {"jsonrpc": "2.0", "id": rid, "error": {"code": s, "message": "m"}}
+        elif state["shape"] == "error-message":
+            frame = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": {s: s}}}
+        elif state["shape"] == "jsonrpc":
+            frame = {"jsonrpc": s, "id": rid, "result": {}}
+        elif state["shape"] == "json-deep":
+            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m","data":' % json.dumps(rid)
+            return (head + "[" * 1500 + "0" + "]" * 1500 + ',"code":%s}}' % json.dumps(s)).encode()
+        elif state["shape"] == "json-bigint":
+            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m","data":' % json.dumps(rid)
+            return (head + "7" * 5000 + ',"code":%s}}' % json.dumps(s)).encode()
+        elif state["shape"].startswith("cursor-") and method in ("tools/list", "resources/list", "prompts/list"):
+            result = normal(method, request.get("params") or {})
+            if state["shape"] == "cursor-object":
+                result["nextCursor"] = {"token": s}
+            elif state["shape"] == "cursor-list":
+                result["next_cursor"] = [s]
+            else:
+                result["nextCursor"] = s
+            frame = {"jsonrpc": "2.0", "id": rid, "result": result}
+        elif state["shape"].startswith("cursor-"):
+            frame = {"jsonrpc": "2.0", "id": rid, "result": normal(method, request.get("params") or {})}
+        elif state["shape"] == "payload":
+            frame = {"jsonrpc": "2.0", "id": rid, "result": invalid_payload(method, s, request.get("params") or {})}
+        elif state["shape"].startswith("envelope-"):
+            error = {"code": -32000, "message": s}
+            frame = {"jsonrpc": "2.0", "id": rid, "error": error}
+            if state["shape"] == "envelope-version":
+                frame["jsonrpc"] = "1.0"
+            elif state["shape"] == "envelope-unversioned":
+                del frame["jsonrpc"]
+            elif state["shape"] == "envelope-both":
+                frame["result"] = {}
+            else:
+                error["code"] = True
+        elif state["shape"] == "json-syntax":
+            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m",,' % json.dumps(rid)
+            return (head + '"code":%s}}' % json.dumps(s)).encode()
+        else:
+            return (s + " is not JSON {").encode()
+        return json.dumps(frame).encode()
+    '''
+)
+_logic: dict[str, Any] = {}
+exec(_DOWNSTREAM_LOGIC, _logic)  # noqa: S102 -- the test's own downstream logic
+
+
+class _Downstream:
+    """A streamable-HTTP (`/mcp`) and legacy-SSE (`/sse`, `/messages`)
+    downstream in one local server; `state` picks the malformed reply."""
+
+    def __init__(self) -> None:
+        self.state: dict[str, Any] = {
+            "method": None,
+            "shape": None,
+            "s": "",
+            "sse": False,
+        }
+        self.streams: list[queue.Queue[bytes | None]] = []
+        self.seen: set[str | None] = set()
+        downstream = self
+
+        class Handler(BaseHTTPRequestHandler):
+            protocol_version = "HTTP/1.1"
+
+            def log_message(self, *args: Any) -> None:
+                pass
+
+            def _body(self) -> dict[str, Any]:
+                return json.loads(self.rfile.read(int(self.headers["content-length"])))
+
+            def do_POST(self) -> None:  # noqa: N802
+                request = self._body()
+                downstream.seen.add(request.get("method"))
+                data = _logic["reply"](request, downstream.state)
+                if self.path.startswith("/messages"):
+                    if data is not None and downstream.streams:
+                        downstream.streams[-1].put(data)
+                        if request.get("method") == downstream.state["method"]:
+                            # The read loop drops a frame that fails JSON-RPC
+                            # validation and keeps reading (Consiliency/pmcp#287),
+                            # as stdio does: follow it with the real reply, or
+                            # the request only times out.
+                            downstream.streams[-1].put(
+                                _logic["reply"](
+                                    request, {**downstream.state, "method": None}
+                                )
+                            )
+                    self.send_response(202)
+                    self.send_header("content-length", "0")
+                    self.end_headers()
+                    return
+                if data is None:
+                    self.send_response(202)
+                    self.send_header("content-length", "0")
+                    self.end_headers()
+                    return
+                if downstream.state["sse"]:
+                    data = b"event: message\ndata: " + data + b"\n\n"
+                    kind = "text/event-stream"
+                else:
+                    kind = "application/json"
+                self.send_response(200)
+                self.send_header("content-type", kind)
+                self.send_header("content-length", str(len(data)))
+                self.end_headers()
+                self.wfile.write(data)
+
+            def do_GET(self) -> None:  # noqa: N802
+                if not self.path.startswith("/sse"):
+                    self.send_response(405)
+                    self.send_header("content-length", "0")
+                    self.end_headers()
+                    return
+                stream: queue.Queue[bytes | None] = queue.Queue()
+                downstream.streams.append(stream)
+                self.send_response(200)
+                self.send_header("content-type", "text/event-stream")
+                self.send_header("cache-control", "no-cache")
+                self.end_headers()
+                self.wfile.write(b"event: endpoint\ndata: /messages?session=1\n\n")
+                self.wfile.flush()
+                while (data := stream.get()) is not None:
+                    try:
+                        self.wfile.write(b"event: message\ndata: " + data + b"\n\n")
+                        self.wfile.flush()
+                    except OSError:
+                        return
+
+            def do_DELETE(self) -> None:  # noqa: N802
+                self.send_response(200)
+                self.send_header("content-length", "0")
+                self.end_headers()
+
+        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
+        self.httpd.daemon_threads = True
+        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
+        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
+
+    def close(self) -> None:
+        for stream in self.streams:
+            stream.put(None)
+        self.httpd.shutdown()
+        self.httpd.server_close()
+
+
+_STDIO_SCRIPT = _DOWNSTREAM_LOGIC + textwrap.dedent(
+    """
+    import sys
+    REJECTED = @@REJECTED@@
+    state_path = sys.argv[1]
+    for line in sys.stdin:
+        with open(state_path) as handle:
+            state = json.load(handle)
+        request = json.loads(line)
+        with open(state_path + ".seen", "a") as seen:
+            seen.write(str(request.get("method")) + "\\n")
+        data = reply(request, state)
+        if data is None:
+            continue
+        if request.get("method") == state["method"] and state["shape"] in REJECTED:
+            # stdio frames are lines: the rejected line, then the reply the
+            # request is waiting for (else it would only time out).
+            sys.stdout.buffer.write(data + b"\\n")
+            data = reply(request, {**state, "method": None})
+        sys.stdout.buffer.write(data + b"\\n")
+        sys.stdout.flush()
+    """
+).replace("@@REJECTED@@", repr(_REJECTED_SHAPES))
+
+
+def _config(transport: str, downstream: _Downstream, tmp: Path) -> ResolvedServerConfig:
+    if transport == "stdio":
+        script = tmp / "frames_downstream.py"
+        script.write_text(_STDIO_SCRIPT)
+        config: Any = LocalMcpServerConfig(
+            command=sys.executable, args=[str(script), str(tmp / "state.json")]
+        )
+    elif transport == "legacy-sse":
+        config = RemoteMcpServerConfig(type="sse", url=f"{downstream.base}/sse")
+    else:
+        config = RemoteMcpServerConfig(type="http", url=f"{downstream.base}/mcp")
+    return ResolvedServerConfig(name="frames", source="custom", config=config)
+
+
+def _wire_error(error: BaseException) -> str:
+    """What the SDK's dispatcher sends the caller for a handler's escaping
+    exception (`handler_exception_to_error_data`, else `code=0, str(e)`),
+    not `str(error)`: a bare `ValidationError` goes out as `-32602` with no
+    text (rev 11 correction)."""
+    from mcp.shared.jsonrpc_dispatcher import handler_exception_to_error_data
+
+    data = handler_exception_to_error_data(error)
+    if data is None:
+        return f"raised code 0: {error}"
+    return f"raised code {data.code}: {data.message} {data.data!r}"
+
+
+async def _request(server: Any, method: str) -> str:
+    """Drive `method` through pmcp's own surface; return what the caller sees."""
+    if method == "tools/call":
+        result = await _call(
+            server,
+            "gateway.invoke",
+            {"tool_id": "frames::run", "options": {"timeout_ms": 3000}},
+        )
+    elif method.startswith("tasks/"):
+        target = {"server_name": "frames", "task_id": "t"}
+        if method == "tasks/cancel":
+            from pmcp.types import McpTaskInfo
+
+            server._client_manager._record_task(
+                "frames", McpTaskInfo(task_id="t", status="working")
+            )
+        name = {
+            "tasks/list": "gateway.tasks_list",
+            "tasks/get": "gateway.tasks_get",
+            "tasks/result": "gateway.tasks_result",
+            "tasks/cancel": "gateway.tasks_cancel",
+        }[method]
+        args = {"server_name": "frames"} if method == "tasks/list" else target
+        result = await _call(server, name, args)
+    elif method == "resources/read":
+        entry = server._server.get_request_handler("resources/read")
+        try:
+            result = await entry.handler(
+                _make_ctx(), ReadResourceRequestParams(uri="x://r")
+            )
+        except Exception as error:  # noqa: BLE001 -- what the SDK sends
+            return _wire_error(error)
+    elif method == "prompts/get":
+        entry = server._server.get_request_handler("prompts/get")
+        try:
+            result = await entry.handler(
+                _make_ctx(), GetPromptRequestParams(name="frames::p")
+            )
+        except Exception as error:  # noqa: BLE001 -- what the SDK sends
+            return _wire_error(error)
+    else:
+        return ""  # a connect-time method: the connect result is the output
+    return result.model_dump_json()
+
+
+def _accepted(response: str) -> bool:
+    """A stdio frame pmcp accepted: its result is the product, by design."""
+    try:
+        payload = json.loads(response)
+    except ValueError:
+        return False
+    content = payload.get("content") or []
+    text = "".join(
+        block.get("text", "") for block in content if isinstance(block, dict)
+    )
+    try:
+        inner = json.loads(text)
+    except ValueError:
+        return False
+    return isinstance(inner, dict) and (inner.get("ok") is True or "task" in inner)
+
+
+#: The one timing-dependent line a teardown logs: whether a transport closed
+#: inside its 5 s grace depends on the host, not on the frame.
+_CLOSE_TIMEOUT = re.compile(
+    r"\[frames\] remote transport did not close within [0-9.]+s; cancelling"
+)
+
+
+@pytest.fixture(autouse=True)
+def _only_pytests_log_handlers() -> Any:
+    """Detach root handlers other tests left behind (a CLI test's file
+    handler whose directory is gone, say). A handler that fails while the
+    SDK's `except` block is active makes `logging.Handler.handleError` print
+    that exception's chain to stderr -- the rejected frame included -- past
+    every filter; see the plan's *Unverified*. pytest's own handlers stay."""
+    root = logging.getLogger()
+    foreign = [h for h in root.handlers if not type(h).__module__.startswith("_pytest")]
+    for handler in foreign:
+        root.removeHandler(handler)
+    yield
+    for handler in foreign:
+        root.addHandler(handler)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("method", _METHODS)
+@pytest.mark.parametrize("transport", _TRANSPORTS)
+async def test_no_malformed_frame_value_reaches_pmcps_output(
+    monkeypatch: pytest.MonkeyPatch,
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    transport: str,
+    method: str,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    # A failed connect is retried with back-off; the retries are the same
+    # frames again, and waiting them out adds nothing.
+    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
+    downstream = _Downstream()
+    downstream.state["sse"] = transport == "http-sse"
+    server, _ = _server(tmp_path, audited=False)
+    policy = server._policy_manager
+    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
+    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
+    policy.is_resource_allowed = lambda resource_id: True  # type: ignore[method-assign]
+    policy.is_prompt_allowed = lambda prompt_id: True  # type: ignore[method-assign]
+    tap = _Tap(server, None, caplog, capfd, recwarn)
+    manager = server._client_manager
+    config = _config(transport, downstream, tmp_path)
+    cases = 0
+    try:
+        for _ in (method,):
+            for shape in _SHAPES:
+                for family in _FRAME_FAMILIES:
+                    seen: list[tuple[str, ...]] = []
+                    for s in _FAMILIES[family]:
+                        state = {
+                            **downstream.state,
+                            "method": method,
+                            "shape": shape,
+                            "s": s,
+                        }
+                        downstream.state.update(state)
+                        (tmp_path / "state.json").write_text(json.dumps(state))
+                        # A known task makes a forced disconnect send `tasks/cancel`,
+                        # which may be the malformed method: forget it first.
+                        manager._tasks.clear()
+                        await manager.disconnect_server("frames", force=True)
+                        # Same request ids for both sentinels of a pair.
+                        manager._request_counters.pop("frames", None)
+                        mark = tap.start()
+                        errors = await manager.connect_server(config)
+                        answer = await asyncio.wait_for(_request(server, method), 20)
+                        accepted = _accepted(answer)
+                        response = json.dumps(errors) + answer
+                        health = "".join(
+                            b.text
+                            for b in (await _call(server, "gateway.health", {})).content
+                        )
+                        observed = tap.since(mark, response + health)
+                        # The pair differential: the caller-facing text, and
+                        # pmcp's own log records. The SDK's and httpx's
+                        # transport chatter varies with task timing (how many
+                        # messages were sent before a teardown); it is still
+                        # in the leak check above.
+                        pair_view = (
+                            # An accepted answer is the downstream's data, with
+                            # its timestamps: the pair compares rejections.
+                            json.dumps(errors) + ("<accepted>" if accepted else answer),
+                            # As a multiset: the transports' reader tasks log
+                            # concurrently, so the order is not the pair's to
+                            # keep.
+                            "\n".join(
+                                sorted(
+                                    _record_stable(r)
+                                    for r in caplog.records[mark[0] :]
+                                    if r.name.split(".")[0] == "pmcp"
+                                    and not _CLOSE_TIMEOUT.fullmatch(r.getMessage())
+                                )
+                            ),
+                            observed.streams,
+                            observed.warnings,
+                        )
+                        records = caplog.records[mark[0] :]
+                        leaks = observed.leaks(s)
+                        if (
+                            (transport == "stdio" or shape == "payload")
+                            and leaks == ["response"]
+                            and accepted
+                        ):
+                            # Accepted as the product, by design: the caller
+                            # asked for this result. Never in the log.
+                            leaks = []
+                        assert leaks == [], (transport, method, shape, family, observed)
+                        if transport == "stdio" and shape.startswith("envelope-"):
+                            # No vacuous pass: the envelope reached the
+                            # dispatcher and was dropped (rev 13).
+                            assert any(
+                                "dropped invalid frame" in r.getMessage()
+                                for r in records
+                            ), (transport, method, shape, family)
+                        if transport == "stdio" and shape.startswith("json-"):
+                            # No vacuous pass: the rejected frame reached the
+                            # reader and got the fixed record (rev 12).
+                            assert any(
+                                "non-protocol stdout line" in r.getMessage()
+                                for r in records
+                            ), (transport, method, shape, family)
+                        seen.append(pair_view)
+                    assert seen[0] == seen[1], (transport, method, shape, family, seen)
+                    cases += 1
+        assert cases == len(_SHAPES) * len(_FRAME_FAMILIES)
+        # No vacuous pass: the downstream did answer the targeted method.
+        seen_methods = set(downstream.seen)
+        if transport == "stdio":
+            seen_methods |= set((tmp_path / "state.json.seen").read_text().splitlines())
+        assert method in seen_methods, seen_methods
+    finally:
+        manager._tasks.clear()
+        await manager.disconnect_server("frames", force=True)
+        await server.shutdown()
+        downstream.close()
+
+
+# --- the SDK's own ClientSession (rev 4, rev 3 board B1) ----------------------
+#
+# `refresh_server` (the gateway's startup description refresh) talks to a
+# downstream through the SDK's `stdio_client` + `ClientSession`, whose logger
+# is named "client" -- outside `mcp.*` -- and which logs a notification it
+# rejects with `exc_info=True`. The downstream here sends one malformed
+# message of each kind before it answers `tools/list`.
+
+_SESSION_MESSAGES = {
+    "notifications/message": lambda s: {"level": s, "data": "x"},
+    "notifications/progress": lambda s: {"progressToken": {s: s}, "progress": 1},
+    "notifications/resources/updated": lambda s: {"uri": [s]},
+    "notifications/tools/list_changed": lambda s: s,
+    "notifications/cancelled": lambda s: {"requestId": {s: s}},
+    "request:roots/list": lambda s: s,
+    "request:sampling/createMessage": lambda s: {s: s},
+    "request:elicitation/create": lambda s: {"message": {s: s}},
+    "request:ping": lambda s: [s],
+}
+
+#: The kinds whose envelope is well formed, so `ClientSession` itself (on the
+#: "client" logger) is what rejects them.
+_SESSION_LEVEL = {
+    "notifications/message",
+    "notifications/progress",
+    "notifications/resources/updated",
+    "notifications/cancelled",
+}
+
+_SESSION_SCRIPT = textwrap.dedent(
+    """
+    import json, sys
+    with open(sys.argv[1]) as handle:  # the case, not on the command line:
+        kind, params = json.load(handle)  # pmcp records the command line
+    for line in sys.stdin:
+        with open(sys.argv[1] + ".in", "a") as seen:
+            seen.write(line)
+        request = json.loads(line)
+        rid, method = request.get("id"), request.get("method")
+        if rid is None or method is None:
+            continue
+        if method == "initialize":
+            result = {"protocolVersion": request["params"]["protocolVersion"],
+                      "capabilities": {"tools": {}}, "serverInfo": {"name": "d", "version": "1"}}
+        else:
+            if method == "tools/list":
+                if kind.startswith("request:"):
+                    frame = {"jsonrpc": "2.0", "id": 900, "method": kind[8:], "params": params}
+                else:
+                    frame = {"jsonrpc": "2.0", "method": kind, "params": params}
+                sys.stdout.write(json.dumps(frame) + "\\n")
+            result = {"tools": [{"name": "run", "inputSchema": {"type": "object"}}]} if method == "tools/list" else {}
+        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
+        sys.stdout.flush()
+    """
+)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("kind", sorted(_SESSION_MESSAGES))
+async def test_no_malformed_session_message_value_reaches_the_log(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    kind: str,
+) -> None:
+    from pmcp.manifest.loader import ServerConfig
+    from pmcp.manifest.refresher import refresh_server
+
+    caplog.set_level(logging.DEBUG)
+    script = tmp_path / "session_downstream.py"
+    script.write_text(_SESSION_SCRIPT)
+    tap = _Tap(typing.cast(Any, _NoServer()), None, caplog, capfd, recwarn)
+    for family in _FRAME_FAMILIES:
+        seen = []
+        for s in _FAMILIES[family]:
+            config = ServerConfig(
+                name="d",
+                description="d",
+                keywords=[],
+                install={},
+                command=sys.executable,
+                args=[str(script), str(tmp_path / "case.json")],
+            )
+            (tmp_path / "case.json").write_text(
+                json.dumps([kind, _SESSION_MESSAGES[kind](s)])
+            )
+            mark = tap.start()
+            result = await asyncio.wait_for(refresh_server(config, force=True), 30)
+            observed = tap.since(mark, repr(result))
+            assert observed.leaks(s) == [], (kind, family, observed)
+            seen.append(
+                "\\n".join(
+                    sorted(
+                        _record_stable(r)
+                        for r in caplog.records[mark[0] :]
+                        if r.name == "client" or r.name.split(".")[0] == "pmcp"
+                    )
+                )
+            )
+        assert seen[0] == seen[1], (kind, family, seen)
+    # No vacuous pass: the SDK rejected the message -- in its envelope
+    # (`mcp.client.stdio`) or, for a well-formed envelope, in `ClientSession`
+    # (the "client" logger) -- and the scrubbed record says so.
+    rejected = {
+        r.name
+        for r in caplog.records
+        if r.name.split(".")[0] in ("client", "mcp")
+        and "validation error" in r.getMessage()
+    }
+    replies = [
+        json.loads(line)
+        for line in (tmp_path / "case.json.in").read_text().splitlines()
+    ]
+    answered = any(r.get("id") == 900 and "error" in r for r in replies)
+    assert rejected or answered, kind
+    if kind in _SESSION_LEVEL:
+        assert "client" in rejected, (kind, rejected)
+
+
+class _NoServer:
+    """`_Tap` outside a server: no gateway tools, so no audit-event buffer."""
+
+    _gateway_tools = None
+
+
+# --- rev 8: every JSON-RPC-shaped frame the stdio parser rejects ---------------
+
+
+def _rejected_frames(s: str) -> list[tuple[str, bytes]]:
+    """JSON-RPC-shaped stdio lines the real parser rejects, derived from the
+    grammar rather than from findings (round-7 codex F028-F034 are two of
+    them): every truncation point and every structural-character deletion of
+    a frame carrying the sentinel, a doubled separator at every structural
+    position, and both parser limits (nesting depth, an integer's digits),
+    as an object and as a batch, bare and after leading whitespace."""
+    frame = json.dumps(
+        {"jsonrpc": "2.0", "id": 7, "result": {"k": s, "list": [1, s], "o": {"s": s}}}
+    )
+    variants: list[tuple[str, str]] = []
+    for cut in range(1, len(frame)):
+        variants.append((f"truncated@{cut}", frame[:cut]))
+    for i, char in enumerate(frame):
+        if char in '{}[]:,"':
+            variants.append((f"deleted {char}@{i}", frame[:i] + frame[i + 1 :]))
+        if char in ":,":
+            variants.append((f"doubled {char}@{i}", frame[:i] + char + frame[i:]))
+    variants.append(
+        ("depth", frame[:-1] + ',"d":' + "[" * 1500 + "0" + "]" * 1500 + "}")
+    )
+    variants.append(("digits", frame[:-1] + ',"n":' + "7" * 5000 + "}"))
+    # Codex's shapes without an object around them, and PR 321's bare array:
+    variants.append(("bare-depth", "[" * 1500 + json.dumps(s) + "]" * 1500))
+    variants.append(("bare-digits", "7" * 5000 + " " + json.dumps(s)))
+    variants.append(("scalar-then-data", "42 " + json.dumps(s)))
+    out: list[tuple[str, bytes]] = []
+    for label, text in variants:
+        for prefix, suffix, shape in (
+            ("", "", "object"),
+            ("  ", "", "indented"),
+            ("[", "]", "batch"),
+            ("\ufeff", "", "bom"),
+        ):
+            line = prefix + text + suffix
+            try:
+                json.loads(line)
+            except Exception:  # noqa: BLE001 -- any rejection counts
+                out.append((f"{shape} {label}", line.encode()))
+    return out
+
+
+# --- stdout line generators (revs 8-11), feeding rev 12's property ----------
+
+#: Every character that can begin a JSON value, plus Python's NaN/Infinity:
+#: generator input only (rev 12 has no classifier to oracle).
+_VALUE_STARTS = '{["-0123456789tfnNI'
+
+#: Lead characters a line can start with before its first significant one:
+#: plain and Unicode whitespace (all of which `.strip` removes), control and
+#: format characters, and the byte-order mark.
+_LEADS = (
+    "",
+    " ",
+    "   ",
+    "\t",
+    "\r",
+    "\x0c",
+    "\x0b",
+    "\u00a0",
+    "\u2028",
+    "\u3000",
+    "\u200b",
+    "\ufeff",
+    "\x00",
+    "\x1b",
+    " \ufeff\t",
+)
+#: Prefixes a downstream might write before a whole frame on the same line
+#: (round-8 claude N1), each a different class of first character.
+_FRAME_PREFIXES = (
+    ",",
+    "}",
+    ":",
+    "+",
+    "ready",
+    "ready ",
+    "ready\r",
+    "server ready: ",
+    "\x1b[0m",
+    "[INFO] ",
+    "true ",
+    "42 ",
+)
+
+
+def _line_cases(s: str) -> list[tuple[str, bytes]]:
+    """Lines a stdio downstream could write, carrying `s` only as JSON content:
+    value starts, unterminated strings, number and literal prefixes, whole
+    frames behind every lead and in every BOM/UTF encoding, CR-only and
+    several frames per line."""
+    spelled = json.dumps(s)[1:-1]
+    frame = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"k": s}})
+    out: list[tuple[str, bytes]] = []
+    for lead in _LEADS:
+        tag = repr(lead)
+        for char in _VALUE_STARTS:
+            out.append(
+                (f"{tag} value-start {char!r}", (lead + char + spelled).encode())
+            )
+        out.append((f"{tag} unterminated string", (lead + '"' + spelled).encode()))
+        out.append((f"{tag} unterminated key", (lead + '{"' + spelled).encode()))
+        for literal in (
+            "42 ",
+            "-1 ",
+            "0.5",
+            "true ",
+            "false",
+            "null ",
+            "NaN ",
+            "Infinity ",
+        ):
+            out.append(
+                (f"{tag} {literal!r} then data", (lead + literal + spelled).encode())
+            )
+        for prefix in _FRAME_PREFIXES:
+            out.append((f"{tag} {prefix!r} + frame", (lead + prefix + frame).encode()))
+    out.append(("\\xff + frame", b"\xff" + frame.encode()))
+    out.append(("\\xfe\\xff + frame", b"\xfe\xff" + frame.encode()))
+    for codec in (
+        "utf-8-sig",
+        "utf-16",
+        "utf-16-le",
+        "utf-16-be",
+        "utf-32",
+        "utf-32-le",
+        "utf-32-be",
+    ):
+        out.append((f"frame in {codec}", frame.encode(codec)))
+    out.append(("CR-only", ("ready\r" + frame + "\r" + frame).encode()))
+    out.append(("two frames", (frame + frame).encode()))
+    out.append(("two frames, spaced", (frame + " " + frame).encode()))
+    out.append(("frame + trailing banner", (frame + " done").encode()))
+    return out
+
+
+def _stdio_manager() -> tuple[Any, Any]:
+    from unittest.mock import MagicMock
+
+    from pmcp.client.manager import ClientManager, ManagedClient
+    from pmcp.types import ServerStatus, ServerStatusEnum
+
+    return ClientManager(), ManagedClient(
+        config=MagicMock(),
+        process=MagicMock(),
+        status=ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0),
+    )
+
+
+# --- rev 10: the SDK's traffic logging, outgoing --------------------------------
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("family", ("hex", "alpha", "unicode"))
+@pytest.mark.parametrize("transport", _TRANSPORTS)
+async def test_no_caller_argument_reaches_the_log_through_a_transport(
+    monkeypatch: pytest.MonkeyPatch,
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    transport: str,
+    family: str,
+) -> None:
+    """Round-9 codex P1's other direction (and round-8 claude N2): with DEBUG
+    on, the SDK logs each message it SENDS, and a `tools/call` request
+    carries the caller's arguments. Over every real transport, a caller
+    sentinel in `gateway.invoke`'s arguments reaches the downstream and is
+    in no log record, stream or warning -- none exempt."""
+    caplog.set_level(logging.DEBUG)
+    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
+    s = _FAMILIES[family][1]
+    downstream = _Downstream()
+    downstream.state["sse"] = transport == "http-sse"
+    state = {**downstream.state, "method": None, "shape": None, "s": ""}
+    (tmp_path / "state.json").write_text(json.dumps(state))
+    server, _ = _server(tmp_path, audited=False)
+    policy = server._policy_manager
+    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
+    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
+    tap = _Tap(server, None, caplog, capfd, recwarn)
+    manager = server._client_manager
+    try:
+        await manager.connect_server(_config(transport, downstream, tmp_path))
+        mark = tap.start()
+        answer = await asyncio.wait_for(
+            _call(
+                server,
+                "gateway.invoke",
+                {
+                    "tool_id": "frames::run",
+                    "arguments": {"q": s, "nested": {"k": [s]}},
+                    "options": {"timeout_ms": 3000},
+                },
+            ),
+            20,
+        )
+        observed = tap.since(mark, "")
+        records = caplog.records[mark[0] :]
+        # No record exempt: the raw log, every record's full text.
+        text = "\n".join(_record_text(r) for r in records)
+        forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
+        assert not any(form in text for form in forbidden), (transport, text[:600])
+        assert observed.leaks(s) == [], (transport, observed)
+        # No vacuous pass: the call reached the downstream and answered.
+        seen = set(downstream.seen)
+        if transport == "stdio":
+            seen |= set((tmp_path / "state.json.seen").read_text().splitlines())
+        assert "tools/call" in seen, seen
+        assert "ok" in "".join(getattr(b, "text", "") for b in answer.content)
+        if transport != "stdio":
+            # The SDK did log the outgoing request -- as its structure.
+            assert any(
+                "<JSON-RPC request: method 'tools/call'" in r.getMessage()
+                for r in records
+            ), [r.getMessage() for r in records][:20]
+    finally:
+        await manager.disconnect_server("frames", force=True)
+        await server.shutdown()
+
+
+# --- rev 10: a frame broken across lines (round-9 claude N1-r9) ---------------
+
+
+def _broken_frame_streams(s: str) -> list[tuple[str, list[bytes]]]:
+    """Streams of stdio lines in which a downstream broke a frame across
+    lines, each followed by a well-formed frame and then a banner:
+    - a raw newline inside a string, the tail line plain text, and again
+      with the tail starting with a closer;
+    - a caller's value echoed after the break;
+    - a UTF-16 frame whose character holds byte 0x0A (U+010A in LE, U+0A00
+      in BE), split by the line reader."""
+    good = json.dumps({"jsonrpc": "2.0", "method": "notifications/message"})
+    banner = "server ready banner-ok-7f3a"
+    tails = [
+        f'{{"jsonrpc":"2.0","id":7,"result":{{"text":"Config loaded\nAPI_KEY={s}\nDB={s}"}}}}',
+        f'{{"jsonrpc":"2.0","id":7,"result":{{"text":"you sent: abc\n{s}"}}}}',
+        f'{{"jsonrpc":"2.0","id":7,"result":{{"a":[1,\n], {s}]}}}}',
+        f'ready {{"jsonrpc":"2.0","id":7,"result":{{"text":"abc\n{s}"}}}}',
+    ]
+    streams = []
+    for index, text in enumerate(tails):
+        lines = [part.encode() for part in text.split("\n")]
+        streams.append(
+            (f"raw newline #{index}", lines + [good.encode(), banner.encode()])
+        )
+    for codec, char in (("utf-16-le", "\u010a"), ("utf-16-be", "\u0a00")):
+        data = json.dumps({"k": f"{char} {s}"}, ensure_ascii=False).encode(codec)
+        lines = data.split(b"\n")
+        assert len(lines) > 1, codec
+        streams.append(
+            (f"{codec} 0x0A split", lines + [good.encode(), banner.encode()])
+        )
+    return streams
+
+
+# --- rev 11: describe mode ends only on a valid frame; oversized heads -------
+
+
+async def _read_stream(
+    manager: Any, managed: Any, data: bytes, monkeypatch: Any, limit: int | None = None
+) -> None:
+    """Feed `data` to the REAL `_read_stdout` loop, then EOF."""
+    from pmcp.types import ServerStatusEnum
+
+    if limit is not None:
+        monkeypatch.setattr("pmcp.client.manager._stdio_read_limit", lambda: limit)
+        # Chunks smaller than the limit, so a long line spans several reads
+        # and meets the limit before its newline, as a 10 MiB line does.
+        monkeypatch.setattr("pmcp.client.manager._STDIO_CHUNK_SIZE", limit // 2)
+    reader = asyncio.StreamReader()
+    reader.feed_data(data)
+    reader.feed_eof()
+    managed.process.stdout = reader
+    managed.config = None  # no reconnect at EOF
+    managed.status.status = ServerStatusEnum.OFFLINE
+    await manager._read_stdout("srv", managed)
+
+
+#: Lines that parse but are not a frame the dispatcher accepts: each kind of
+#: JSON value, and objects that fail its guards.
+_NOT_FRAMES = (
+    "42",
+    "-1",
+    "0.5",
+    "true",
+    "false",
+    "null",
+    "[]",
+    "[1, 2]",
+    "{}",
+    '"ok"',
+    '{"jsonrpc": "2.0"}',
+    '{"id": 7, "result": {}}',
+    '{"jsonrpc": "1.0", "id": 7, "result": {}}',
+    '{"jsonrpc": "2.0", "id": true, "result": {}}',
+    '{"jsonrpc": "2.0", "id": 1.5, "result": {}}',
+    '{"jsonrpc": "2.0", "method": 5}',
+    '{"jsonrpc": "2.0", "id": 7}',
+    '{"jsonrpc": "2.0", "id": 7, "result": {}, "error": {"code": 1, "message": "m"}}',
+)
+
+
+# --- rev 11: the handler wrapper keeps the SDK's wire codes (claude N2-r10) ---
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("family", ("hex", "alpha", "unicode"))
+async def test_a_wrapped_handler_keeps_the_wire_code(family: str) -> None:
+    """`_described_errors` changes only the text the SDK would send, never
+    the code: a bare `ValidationError` stays `-32602` (now with a
+    structural message), an `MCPError` keeps its own code, any other
+    exception stays the SDK's `code=0`. No value reaches the message or
+    `data`; an unrelated exception passes unchanged."""
+    from mcp.shared.exceptions import MCPError
+    from mcp.shared.jsonrpc_dispatcher import handler_exception_to_error_data
+    from mcp.types import INVALID_PARAMS
+    from pydantic import ValidationError
+
+    from pmcp.server import _described_errors
+    from pmcp.types import McpTaskInfo
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
+
+    def invalid() -> ValidationError:
+        try:
+            McpTaskInfo.model_validate({"task_id": {"v": s}})
+        except ValidationError as error:
+            return error
+        raise AssertionError("did not reject")
+
+    async def bare() -> None:
+        raise invalid()
+
+    async def mcp_inside_except() -> None:
+        try:
+            raise invalid()
+        except ValidationError:
+            raise MCPError(-32002, "Resource not found") from None
+
+    async def mcp_from_error() -> None:
+        error = invalid()
+        raise MCPError(-32602, str(error)) from error
+
+    async def mcp_data_kept() -> None:
+        try:
+            raise invalid()
+        except ValidationError:
+            raise MCPError(-32002, "Resource not found", {"uri": "x://r"}) from None
+
+    async def mcp_data_carries() -> None:
+        error = invalid()
+        # The value alone, though the rejected input was `{"v": <s>}`: a
+        # string inside a container input is matched too (rev 13).
+        raise MCPError(-32602, "bad input", {"input": s}) from error
+
+    async def mcp_partial_message() -> None:
+        error = invalid()
+        raise MCPError(-32602, f"bad: {error.errors()[0]['input']}") from error
+
+    async def wrapped() -> None:
+        error = invalid()
+        raise ValueError(f"could not build the result: {error}") from error
+
+    async def unrelated() -> None:
+        raise RuntimeError("plain failure")
+
+    async def wire(handler: Any) -> tuple[int, str, Any]:  # (code, message, data)
+        try:
+            await _described_errors(handler)()
+        except Exception as error:  # noqa: BLE001 -- inspected
+            data = handler_exception_to_error_data(error)
+            if data is None:
+                return 0, str(error), None
+            return data.code, data.message, data.data
+        raise AssertionError("did not raise")
+
+    cases = {
+        "bare": (INVALID_PARAMS, "validation error"),
+        "mcp_inside_except": (-32002, "Resource not found"),
+        "mcp_from_error": (-32602, "validation error"),
+        "wrapped": (0, "validation error"),
+        "mcp_data_kept": (-32002, "Resource not found"),
+        "mcp_data_carries": (-32602, "bad input"),
+        "mcp_partial_message": (-32602, ""),
+    }
+    for name, handler in (
+        ("bare", bare),
+        ("mcp_inside_except", mcp_inside_except),
+        ("mcp_from_error", mcp_from_error),
+        ("wrapped", wrapped),
+        ("mcp_data_kept", mcp_data_kept),
+        ("mcp_data_carries", mcp_data_carries),
+        ("mcp_partial_message", mcp_partial_message),
+    ):
+        code, message, data = await wire(handler)
+        expected_code, expected_text = cases[name]
+        assert code == expected_code, (name, code, message)
+        assert expected_text in message, (name, message)
+        assert not any(f in f"{message}{data!r}" for f in forbidden), (name, message)
+        if name == "mcp_data_kept":
+            # rev 12: `data` that carries nothing rejected is kept.
+            assert data == {"uri": "x://r"}, data
+        if name == "mcp_data_carries":
+            assert data is None, data
+    assert await wire(unrelated) == (0, "plain failure", None)
+
+
+# --- rev 12: no non-protocol stdout line is shown, whatever it holds ----------
+
+
+def _is_valid_frame(line: bytes) -> bool:
+    """JSON-RPC 2.0 as MCP defines it, stated independently of
+    `jsonrpc_envelope_problem`: the only stdout line the reader may act on."""
+    try:
+        value = json.loads(line)
+    except Exception:  # noqa: BLE001
+        return False
+    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
+        return False
+
+    def request_id(v: Any) -> bool:
+        return type(v) in (str, int)
+
+    if "method" in value:
+        return (
+            isinstance(value["method"], str)
+            and ("id" not in value or request_id(value["id"]))
+            and isinstance(value.get("params") or {}, dict)
+            and "result" not in value
+            and "error" not in value
+        )
+    if "id" not in value or ("result" in value) == ("error" in value):
+        return False
+    if "result" in value:
+        return request_id(value["id"]) and isinstance(value["result"], dict)
+    error = value["error"]
+    return (
+        (value["id"] is None or request_id(value["id"]))
+        and isinstance(error, dict)
+        and type(error.get("code")) is int
+        and isinstance(error.get("message"), str)
+    )
+
+
+_MISSING = object()
+
+
+def _envelope_violations(s: str, rid: Any) -> list[tuple[str, dict[str, Any]]]:
+    """Rev 13 (round-12 codex B2): one frame per way a JSON-RPC 2.0 response
+    can break the specification's envelope rules (JSON-RPC 2.0 sections 4-5,
+    MCP's response types), each addressed to pending request `rid` and each
+    carrying the sentinel where a caller would render it -- the error's
+    `message` and `data`, or the result. Derived from the rules, not from the
+    four frames codex sent."""
+    err = {"code": -32000, "message": s, "data": {"detail": s}}
+    base: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "error": err}
+
+    def with_member(frame: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
+        out = dict(frame)
+        if value is _MISSING:
+            out.pop(key, None)
+        else:
+            out[key] = value
+        return out
+
+    cases: list[tuple[str, dict[str, Any]]] = []
+    # `jsonrpc` MUST be exactly "2.0".
+    for value in (_MISSING, "1.0", "2", "2.0 ", 2.0, 2, None, ["2.0"], {"v": "2.0"}):
+        cases.append(
+            (f"jsonrpc {type(value).__name__}", with_member(base, "jsonrpc", value))
+        )
+    # Exactly one of `result` and `error`.
+    cases.append(("result and error", {**base, "result": {}}))
+    cases.append(
+        (
+            "result and error, result first",
+            {"jsonrpc": "2.0", "id": rid, "result": {"x": s}, "error": err},
+        )
+    )
+    cases.append(
+        ("neither result nor error", {"jsonrpc": "2.0", "id": rid, "message": s})
+    )
+    # `error.code` MUST be an integer.
+    for code in (
+        _MISSING,
+        True,
+        False,
+        1.5,
+        -32000.0,
+        "-32000",
+        None,
+        {},
+        [],
+        {"c": s},
+        [s],
+    ):
+        cases.append(
+            (
+                f"code {type(code).__name__}",
+                {**base, "error": with_member(err, "code", code)},
+            )
+        )
+    # `error.message` MUST be a string (the sentinel then rides in `data`).
+    for message in (_MISSING, 5, None, True, [s], {s: s}):
+        cases.append(
+            (
+                f"message {type(message).__name__}",
+                {**base, "error": with_member(err, "message", message)},
+            )
+        )
+    # `error` MUST be an object.
+    for error in (s, [s], [err], 5, None, True):
+        cases.append((f"error {type(error).__name__}", {**base, "error": error}))
+    # A response's `id` is a string, an integer, or null (an error only):
+    # not a bool, a float, an array or an object -- and it MUST be present.
+    if isinstance(rid, int):
+        for bad_id in (float(rid), [rid], {"id": rid}):
+            cases.append((f"id {type(bad_id).__name__}", {**base, "id": bad_id}))
+        cases.append(("id bool", {**base, "id": rid == 1}))
+    cases.append(("no id", with_member(base, "id", _MISSING)))
+    # `result` MUST be an object (MCP's `Result`).
+    for result in (s, [s], 5, None, True):
+        cases.append(
+            (
+                f"result {type(result).__name__}",
+                {"jsonrpc": "2.0", "id": rid, "result": result},
+            )
+        )
+    cases.append(
+        ("result with null id", {"jsonrpc": "2.0", "id": None, "result": {"x": s}})
+    )
+    # A request or notification carries neither `result` nor `error`, and
+    # its `params` is an object.
+    cases.append(("request with error", {**base, "method": "x"}))
+    cases.append(
+        ("notification with error", {"jsonrpc": "2.0", "method": "x", "error": err})
+    )
+    cases.append(
+        (
+            "request with array params",
+            {"jsonrpc": "2.0", "id": rid, "method": "x", "params": [s]},
+        )
+    )
+    return cases
+
+
+def _stdout_streams(s: str) -> list[tuple[str, list[bytes], int | None]]:
+    """Every stdout stream the earlier rounds' generators produce, as
+    (label, lines, line limit): grammar-derived rejected frames (rev 8),
+    value-start and prefix lines behind every lead (rev 9), frames broken
+    across lines (rev 10), a non-frame or a valid frame between a broken
+    head and its continuation (rounds 10 and 11), an oversized head, and
+    plain banners in each common shape."""
+    head = b'{"jsonrpc":"2.0","id":7,"result":{"text":"start'
+    tail = f'API_KEY={s} "}}}}'.encode()
+    reply = json.dumps({"jsonrpc": "2.0", "id": 8, "result": {}}).encode()
+    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/message"}).encode()
+    streams: list[tuple[str, list[bytes], int | None]] = []
+    streams += [(label, [line], None) for label, line in _rejected_frames(s)]
+    streams += [(label, [line], None) for label, line in _line_cases(s)]
+    streams += [(label, lines, None) for label, lines in _broken_frame_streams(s)]
+    streams += [(f"middle {m}", [head, m.encode(), tail], None) for m in _NOT_FRAMES]
+    streams.append(("round-11 interleaved response", [head, reply, tail], None))
+    streams.append(("interleaved notification", [head, note, tail], None))
+    streams.append(("oversized head", [head + b"A" * 5000, tail, note], 2048))
+    # Rev 13: malformed envelopes addressed to the pending request (id 8).
+    streams += [
+        (f"envelope: {label}", [json.dumps(frame).encode()], None)
+        for label, frame in _envelope_violations(s, 8)
+    ]
+    for banner in (
+        f"server ready {s}",
+        f"INFO: {s}",
+        f"NOTICE {s}",
+        f"2026-10-01 12:00 ready {s}",
+        f"token={s}",
+        f"\x1b[32mready {s}\x1b[0m",
+        f"warning: value is '{s}'",
+    ):
+        streams.append((f"banner {banner[:12]!r}", [banner.encode()], None))
+    return streams
+
+
+def _windows(text: str, size: int = 12) -> set[str]:
+    return {text[i : i + size] for i in range(max(0, len(text) - size + 1))}
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+async def test_no_record_shows_a_non_protocol_stdout_line(
+    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, family: str
+) -> None:
+    """Rev 12 (round-11 codex P1, the class claude proposed): for any bytes a
+    downstream writes to stdout that are not a valid JSON-RPC message, no log
+    record carries any part of them -- through the real `_read_stdout`, with
+    no record exempt. Each unparseable line gets the one fixed record."""
+    import time
+
+    from pmcp.client.manager import PendingRequest
+
+    caplog.set_level(logging.DEBUG)
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
+    streams = _stdout_streams(s)
+    fixed = 0
+    for label, lines, limit in streams:
+        manager, managed = _stdio_manager()
+        future = asyncio.get_running_loop().create_future()
+        managed.pending_requests[8] = PendingRequest(
+            request_id=8,
+            server_name="srv",
+            tool_id="",
+            started_at=time.time(),
+            last_heartbeat=time.time(),
+            timeout_ms=1000,
+            future=future,
+        )
+        start = len(caplog.records)
+        await _read_stream(
+            manager, managed, b"\n".join(lines) + b"\n", monkeypatch, limit=limit
+        )
+        records = caplog.records[start:]
+        text = "\n".join(_record_text(r) for r in records)
+        flat = text.replace("\x00", "")
+        assert not any(f in text or f in flat for f in forbidden), (label, text[:400])
+        record_windows = _windows(text) | _windows(flat)
+        for line in lines:
+            if _is_valid_frame(line):
+                continue
+            decoded = line.decode("utf-8", "replace")
+            shown = _windows(decoded) & record_windows
+            assert not shown, (label, sorted(shown)[:3])
+        messages = [r.getMessage() for r in records]
+        assert not any("Non-JSON output" in m for m in messages), (label, messages)
+        if label.startswith("envelope"):
+            # Rev 13: a malformed envelope never settles the request it names;
+            # only the end of the stream does (a disconnect, not its error).
+            from pmcp.client.manager import DownstreamError
+
+            settled = future.exception() if future.done() else None
+            assert not (future.done() and settled is None), label
+            assert not isinstance(settled, DownstreamError), (label, settled)
+        fixed += sum("non-protocol stdout line" in m for m in messages)
+    # No vacuous pass: thousands of lines took the fixed record.
+    assert fixed > 1000, fixed
+
+
+def test_a_banner_line_gets_the_fixed_record(caplog: pytest.LogCaptureFixture) -> None:
+    """The operator-visibility control, rev 12: a non-conforming server's
+    stdout banner is no longer shown; it gets the fixed record (its log
+    belongs on stderr, which is unchanged)."""
+    import time
+
+    caplog.set_level(logging.DEBUG)
+    manager, managed = _stdio_manager()
+    manager._handle_stdout_line(
+        "srv", managed, b"server ready on port 8080", time.time()
+    )
+    assert [r.getMessage() for r in caplog.records] == [
+        "[srv] non-protocol stdout line: could not parse JSON downstream stdio "
+        "frame at line 1, column 1 (JSONDecodeError)"
+    ]
+
+
+# --- rev 13: a malformed envelope, through the caller waiting on it ------------
+
+
+class _Stdin:
+    """The stdio pipe pmcp writes requests to: each request line is handed
+    to `respond`, which feeds the downstream's answer to the reader."""
+
+    def __init__(self, respond: Any) -> None:
+        self.respond = respond
+
+    def write(self, data: bytes) -> None:
+        for line in data.splitlines():
+            self.respond(json.loads(line))
+
+    async def drain(self) -> None:
+        return None
+
+
+_CONSUMERS = ("tools/list page 2", "tools/call", "tasks/get")
+
+
+async def _through_a_caller(consumer: str, malformed: bytes) -> str:
+    """Run `consumer` -- the code that waits on a request and renders what it
+    gets -- against the REAL `_read_stdout`. The targeted request is answered
+    with `malformed`, then with a valid reply; return what the caller got
+    (its value, or its exception's text)."""
+    from pmcp.types import ServerStatusEnum, ToolInfo
+
+    manager, managed = _stdio_manager()
+    managed.config.name = "srv"
+    managed.status.server_capabilities = {"tasks": {}}
+    manager._clients["srv"] = managed
+    manager._tools["srv::run"] = ToolInfo(
+        tool_id="srv::run",
+        server_name="srv",
+        tool_name="run",
+        description="d",
+        short_description="d",
+        input_schema={"type": "object"},
+        tags=[],
+        risk_hint="low",
+    )
+    reader = asyncio.StreamReader()
+    managed.process.stdout = reader
+    method = consumer.split()[0]
+
+    def respond(request: dict[str, Any]) -> None:
+        rid, params = request["id"], request.get("params") or {}
+        if method == "tools/list" and not params.get("cursor"):
+            result: dict[str, Any] = {
+                "tools": [{"name": "run", "inputSchema": {"type": "object"}}],
+                "nextCursor": "page-2",
+            }
+            reader.feed_data(
+                json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode()
+                + b"\n"
+            )
+            return
+        valid = {
+            "tools/list": {"tools": []},
+            "tools/call": {"content": [{"type": "text", "text": "ok"}]},
+            "tasks/get": {"task": {"taskId": "t", "status": "working"}},
+        }[method]
+        line = malformed.replace(b"@@ID@@", json.dumps(rid).encode())
+        reply = json.dumps({"jsonrpc": "2.0", "id": rid, "result": valid}).encode()
+        reader.feed_data(line + b"\n" + reply + b"\n")
+
+    managed.process.stdin = _Stdin(respond)
+    read_task = asyncio.create_task(manager._read_stdout("srv", managed))
+    try:
+        if method == "tools/list":
+            outcome: Any = await manager._fetch_listing_pages(managed, "tools")
+        elif method == "tools/call":
+            outcome = await manager.call_tool("srv::run", {}, timeout_ms=5000)
+        else:
+            outcome = await manager.get_task("srv", "t")
+        text = repr(outcome)
+    except Exception as error:  # noqa: BLE001 -- what the caller would render
+        text = f"{type(error).__name__}: {error}"
+    finally:
+        managed.config = None  # no reconnect at EOF
+        managed.status.status = ServerStatusEnum.OFFLINE
+        reader.feed_eof()
+        await read_task
+    return text
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+@pytest.mark.parametrize("consumer", _CONSUMERS)
+async def test_no_malformed_envelope_reaches_its_waiting_caller(
+    caplog: pytest.LogCaptureFixture, consumer: str, family: str
+) -> None:
+    """Each envelope violation goes to a real caller (pagination, `call_tool`,
+    `get_task`) through the real reader: nothing it logs, returns or raises
+    carries any of it, and the valid reply that follows is delivered."""
+    caplog.set_level(logging.DEBUG)
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
+    cases = _envelope_violations(s, "@@ID@@")
+    assert len(cases) > 40, len(cases)
+    for label, frame in cases:
+        malformed = json.dumps(frame).encode().replace(b'"@@ID@@"', b"@@ID@@")
+        start = len(caplog.records)
+        outcome = await asyncio.wait_for(_through_a_caller(consumer, malformed), 20)
+        records = "\n".join(_record_text(r) for r in caplog.records[start:])
+        text = records + "\n" + outcome
+        assert not any(f in text for f in forbidden), (consumer, label, text[:600])
+        shown = _windows(malformed.decode()) & (_windows(text))
+        assert not shown, (consumer, label, sorted(shown)[:3])
+        # A part of the value: `describe_exception` elides a long message to
+        # its two ends, which the whole-value forms above do not match.
+        part = _windows(s, 8) & _windows(text, 8)
+        assert not part, (consumer, label, sorted(part)[:3])
+        # The valid reply answered the caller: the malformed frame did not.
+        assert "Error" not in outcome.split(":")[0], (consumer, label, outcome)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("consumer", _CONSUMERS)
+async def test_a_valid_error_still_reaches_its_caller(consumer: str) -> None:
+    """The control: a well-formed error envelope is the downstream's own
+    message, shown by design -- so the test above would see a leak."""
+    frame = {
+        "jsonrpc": "2.0",
+        "id": "@@ID@@",
+        "error": {"code": -32000, "message": "downstream says no"},
+    }
+    malformed = json.dumps(frame).encode().replace(b'"@@ID@@"', b"@@ID@@")
+    outcome = await asyncio.wait_for(_through_a_caller(consumer, malformed), 20)
+    if consumer.startswith("tools/list"):
+        # Page 2 failed: the whole kind is unreadable (None), and logged.
+        assert outcome == "None", outcome
+    else:
+        assert "downstream says no" in outcome, outcome
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_the_sdk_client_transports_check_the_envelope(family: str) -> None:
+    """The SDK's three client transports validate through the strict adapter:
+    each envelope violation is a value-free `ValidationError`, and a valid
+    frame validates as before."""
+    import mcp.client.sse as sse_module
+    import mcp.client.stdio as stdio_module
+    import mcp.client.streamable_http as streamable_module
+    from pydantic import ValidationError
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
+    adapters = {
+        "sse": sse_module.types.jsonrpc_message_adapter,
+        "stdio": stdio_module.types.jsonrpc_message_adapter,
+        "streamable_http": streamable_module.jsonrpc_message_adapter,
+    }
+    for transport, adapter in adapters.items():
+        for label, frame in _envelope_violations(s, 8):
+            raw = json.dumps(frame)
+            with pytest.raises(ValidationError) as caught:
+                adapter.validate_json(raw, by_name=False)
+            text = str(caught.value)
+            assert not any(f in text for f in forbidden), (transport, label, text)
+            assert not _windows(raw) & _windows(text), (transport, label, text)
+        for frame in (
+            {"jsonrpc": "2.0", "id": 8, "result": {"x": s}},
+            {"jsonrpc": "2.0", "id": 8, "error": {"code": -1, "message": s}},
+            {"jsonrpc": "2.0", "id": None, "error": {"code": -1, "message": s}},
+            {"jsonrpc": "2.0", "method": "notifications/message", "params": {}},
+            {"jsonrpc": "2.0", "id": "a", "method": "ping"},
+        ):
+            assert adapter.validate_json(json.dumps(frame), by_name=False)
+
+
+def test_the_strict_envelope_is_installed_on_import_pmcp() -> None:
+    """`pmcp refresh` reaches downstreams through the SDK's `stdio_client`
+    without importing the client manager: the adapter is installed with the
+    record scrubber, by `import pmcp` alone."""
+    import subprocess
+
+    code = (
+        "import pmcp, mcp.client.sse as a, mcp.client.stdio as b, "
+        "mcp.client.streamable_http as c, mcp.server.sse as d; "
+        "print(type(a.types).__name__, type(b.types).__name__, "
+        "type(c.jsonrpc_message_adapter).__name__, type(d.types).__name__ "
+        "if hasattr(d, 'types') else 'module')"
+    )
+    out = subprocess.run(
+        [sys.executable, "-c", code], capture_output=True, text=True, check=True
+    ).stdout.split()
+    assert out[:3] == ["_ClientTypesView", "_ClientTypesView", "_StrictMessageAdapter"]
+    # The server side is untouched.
+    assert out[3] != "_ClientTypesView", out
+
+
+# --- rev 14: the SDK session pmcp builds is bounded (round-13 claude N2) -------
+
+_INIT_SCRIPT = textwrap.dedent(
+    """
+    import json, sys
+    with open(sys.argv[1]) as handle:
+        kind, s = json.load(handle)
+    for line in sys.stdin:
+        request = json.loads(line)
+        rid, method = request.get("id"), request.get("method")
+        if rid is None or method != "initialize":
+            continue
+        error = {"code": -32000, "message": s}
+        frame = {
+            "result-and-error": {"jsonrpc": "2.0", "id": rid, "result": {}, "error": error},
+            "string-code": {"jsonrpc": "2.0", "id": rid, "error": {"code": "5", "message": s}},
+            "result-null": {"jsonrpc": "2.0", "id": rid, "result": None},
+            "silent": None,
+        }[kind]
+        if frame is not None:
+            sys.stdout.write(json.dumps(frame) + "\\n")
+            sys.stdout.flush()
+    """
+)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize(
+    "kind", ["result-and-error", "string-code", "result-null", "silent"]
+)
+async def test_a_malformed_initialize_reply_ends_the_refresh_in_bounded_time(
+    tmp_path: Path,
+    monkeypatch: pytest.MonkeyPatch,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    kind: str,
+) -> None:
+    """A dropped `initialize` reply no longer hangs `refresh_server` (the one
+    SDK session pmcp builds): it fails within its read timeout, value-free."""
+    import time
+
+    import pmcp.manifest.refresher as refresher
+    from pmcp.manifest.loader import ServerConfig
+
+    caplog.set_level(logging.DEBUG)
+    # `raising=False`: before rev 14 these did not exist, and the refresh then
+    # waits out the guard below instead of failing on a missing name.
+    monkeypatch.setattr(refresher, "REFRESH_READ_TIMEOUT_SECONDS", 1.0, raising=False)
+    monkeypatch.setattr(refresher, "REFRESH_TIMEOUT_SECONDS", 8.0, raising=False)
+    script = tmp_path / "init_downstream.py"
+    script.write_text(_INIT_SCRIPT)
+    tap = _Tap(typing.cast(Any, _NoServer()), None, caplog, capfd, recwarn)
+    for family in ("hex", "token"):
+        s = _FAMILIES[family][1]
+        (tmp_path / "case.json").write_text(json.dumps([kind, s]))
+        config = ServerConfig(
+            name="d",
+            description="d",
+            keywords=[],
+            install={},
+            command=sys.executable,
+            args=[str(script), str(tmp_path / "case.json")],
+        )
+        mark = tap.start()
+        started = time.monotonic()
+        try:
+            result = await asyncio.wait_for(
+                refresher.refresh_server(config, force=True), 20
+            )
+        except asyncio.TimeoutError:
+            raise AssertionError(f"refresh still waiting at 20 s: {kind}") from None
+        assert time.monotonic() - started < 10, (kind, family)
+        assert result is None, (kind, family)
+        observed = tap.since(mark, repr(result))
+        assert observed.leaks(s) == [], (kind, family, observed)
+        assert any(
+            "Failed to refresh d" in r.getMessage() for r in caplog.records[mark[0] :]
+        ), kind
+
+
+def test_every_sdk_session_pmcp_builds_has_a_read_timeout() -> None:
+    """Every `ClientSession(...)` in `src/pmcp` passes `read_timeout_seconds`
+    (round-13 claude N2, as a class: today there is one, in the refresher)."""
+    import ast
+
+    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+    sessions = []
+    for path in sorted(root.rglob("*.py")):
+        tree = ast.parse(path.read_text())
+        for node in ast.walk(tree):
+            if (
+                isinstance(node, ast.Call)
+                and isinstance(node.func, ast.Name)
+                and node.func.id == "ClientSession"
+            ):
+                keywords = {k.arg for k in node.keywords}
+                sessions.append(
+                    (path.name, node.lineno, "read_timeout_seconds" in keywords)
+                )
+    assert sessions, "no SDK session found"
+    assert all(ok for _, _, ok in sessions), sessions
+
+
+def test_params_null_reads_as_absent_and_a_ping_is_answered(
+    caplog: pytest.LogCaptureFixture,
+) -> None:
+    """Round-13 claude N1: `params: null` is outside JSON-RPC 2.0, but the
+    SDK's models accept it; a `ping` sent that way is answered, on stdio and
+    through the SDK client adapter, while `params` of any other non-object
+    type is still dropped."""
+    import time
+
+    import mcp.client.streamable_http as streamable_module
+    from pydantic import ValidationError
+
+    from pmcp.argument_errors import jsonrpc_envelope_problem
+
+    ping = {"jsonrpc": "2.0", "id": 4, "method": "ping", "params": None}
+    assert jsonrpc_envelope_problem(ping) is None
+    assert streamable_module.jsonrpc_message_adapter.validate_json(json.dumps(ping))
+    with pytest.raises(ValidationError):
+        streamable_module.jsonrpc_message_adapter.validate_json(
+            json.dumps({**ping, "params": [1]})
+        )
+    manager, managed = _stdio_manager()
+    replies: list[tuple[Any, str]] = []
+    manager._reply_to_downstream_request = (  # type: ignore[method-assign]
+        lambda name, managed, msg_id, method: replies.append((msg_id, method))
+    )
+    manager._handle_stdout_line("srv", managed, json.dumps(ping).encode(), time.time())
+    assert replies == [(4, "ping")]
+
+
+def test_the_adapter_rejects_what_pythons_parser_cannot_read() -> None:
+    """Round-13 claude N3: a frame Python's `json` cannot parse is rejected by
+    the strict adapter itself, never handed to the SDK's own parser, so the
+    rules do not depend on the two parsers agreeing."""
+    import mcp.client.streamable_http as streamable_module
+    from pydantic import ValidationError
+
+    adapter = streamable_module.jsonrpc_message_adapter
+    calls: list[Any] = []
+    base = adapter._base
+
+    class _Spy:
+        def validate_json(self, *args: Any, **kwargs: Any) -> Any:
+            calls.append(args)
+            return base.validate_json(*args, **kwargs)
+
+    adapter._base = _Spy()
+    try:
+        for raw in ('{"jsonrpc": "2.0", "id": 1, "result": {}', "[" * 5000, "7" * 5000):
+            with pytest.raises(ValidationError) as caught:
+                adapter.validate_json(raw)
+            assert "not parseable as JSON" in str(caught.value)
+        assert calls == []
+    finally:
+        adapter._base = base
+
+
+def test_drop_reasons_use_json_type_names() -> None:
+    """Round-13 claude nit: the reasons name JSON's types."""
+    from pmcp.argument_errors import jsonrpc_envelope_problem
+
+    assert jsonrpc_envelope_problem("x") == "not a JSON object (string)"
+    assert jsonrpc_envelope_problem(None) == "not a JSON object (null)"
+    error = {"jsonrpc": "2.0", "id": 1, "error": {"code": {}, "message": "m"}}
+    assert jsonrpc_envelope_problem(error) == "error code of type object"
+    result = {"jsonrpc": "2.0", "id": 1.5, "result": {}}
+    assert jsonrpc_envelope_problem(result) == "id of type number"
+
+
+def test_a_notification_with_a_null_id_is_delivered_as_a_notification(
+    caplog: pytest.LogCaptureFixture,
+) -> None:
+    """Round-14 grok F004 (rev 15): main delivered `{"method": ..., "id":
+    null}` as a notification, and the MCP SDK's own parser reads it as one;
+    rev 14 dropped it (`id of type null`). It is accepted again, routed as a
+    notification and never answered (a reply with a null id would be a
+    response to nothing). Any other non-RequestId id is still dropped."""
+    import time
+
+    from pmcp.argument_errors import jsonrpc_envelope_problem
+
+    note = {"jsonrpc": "2.0", "id": None, "method": "notifications/tools/list_changed"}
+    assert jsonrpc_envelope_problem(note) is None
+    assert jsonrpc_envelope_problem({**note, "id": 1.5}) == "id of type number"
+    manager, managed = _stdio_manager()
+    replies: list[tuple[Any, str]] = []
+    notified: list[str] = []
+    manager._reply_to_downstream_request = (  # type: ignore[method-assign]
+        lambda name, managed, msg_id, method: replies.append((msg_id, method))
+    )
+    manager._handle_downstream_notification = (  # type: ignore[method-assign]
+        lambda name, managed, method: notified.append(method)
+    )
+    manager._handle_stdout_line("srv", managed, json.dumps(note).encode(), time.time())
+    assert notified == ["notifications/tools/list_changed"]
+    assert replies == []
````

### Patch — `tests/test_exception_text_sinks.py`

````diff
--- /dev/null
+++ b/tests/test_exception_text_sinks.py
@@ -0,0 +1,1215 @@
+"""Every place `src/pmcp` turns an exception into text goes through the
+value-free renderers (Consiliency/pmcp#297).
+
+A pydantic or jsonschema `ValidationError` renders the rejected value, so
+wherever pmcp renders an exception that may be one -- a response field, a
+log line or its traceback, an audit or audit-event field -- it must use
+`exception_text` / `safe_exc_info` (or a renderer built on them). This test
+enforces that statically, over every module in `src/pmcp`, the CLI included
+(rev 6: `pmcp refresh` and the config commands read files and downstreams).
+
+The scanner tracks exceptions through a function, not just an `except`
+body (rev 3; the rev 2 board seat found 25 constructs rev 2's scanner
+missed). Within each function (and the module body), an **exception name**
+is:
+
+- an `except` clause's name, if a type it catches -- resolved in the
+  module's own namespace -- is a base of pydantic's or jsonschema's
+  `ValidationError` (an unresolvable type counts as one);
+- a name assigned from `<x>.exception(...)` anywhere in the value
+  (`fut.exception()`, `t.exception() if ... else None`);
+- a name `isinstance`-tested anywhere in a condition against such a type;
+- a name bound from `gather(..., return_exceptions=True)`, and a loop
+  variable over any exception name or over `<x>.exceptions`;
+- an alias of any of these (`last = e`), wherever it is later used.
+
+A **use** of an exception name is safe only as: an argument to a renderer
+(by its bare name, imported from `pmcp` -- `self._sanitize_error` is the one
+method), `type()`/`isinstance()`, `raise`, a comparison or truthiness test,
+an alias to a plain name, a loop's iterable, or an attribute that carries no
+text. Anything else -- an f-string, `str()`/`repr()`, a `%` argument, a
+store into an attribute or container, a return, a text-bearing attribute
+(`args`, `errors()`, jsonschema's `path`/`json_path`, `__notes__`, ...), or
+an unknown callee -- is a sink. Also sinks, anywhere: `exc_info=` not
+through `safe_exc_info`, `logger.exception`, `logging.exception`,
+`makeRecord`/`handle`, `sys.exc_info()`/`sys.exception()`, and every
+`traceback` renderer.
+"""
+
+from __future__ import annotations
+
+import ast
+import re
+import builtins
+import importlib
+import typing
+from pathlib import Path
+from typing import Any
+
+import json
+
+import jsonschema
+import pydantic
+import yaml
+import pytest
+
+#: Exceptions whose own text can carry the input they rejected: validation
+#: errors, and (rev 6) the parse errors of every structured-text parser pmcp
+#: uses -- the same set `exception_text` renders structurally.
+_VALIDATION_ERRORS = (
+    pydantic.ValidationError,
+    jsonschema.ValidationError,
+    yaml.YAMLError,
+    json.JSONDecodeError,
+)
+
+#: Renderers, called by their bare name.
+_RENDERERS = {
+    "exception_text",
+    "safe_exc_info",
+    "safe_traceback_text",
+    "describe_exception",
+    "message_text",
+    "sanitize_auth_diagnostic",
+    "describe_argument_error",
+    "describe_schema_error",
+    "describe_model_error",
+    "model_error_path",
+    "schema_error_path",
+    "schema_error_keyword",
+}
+#: Where a renderer that is not in `pmcp.argument_errors` is defined; its own
+#: body is scanned like any other.
+_RENDERER_HOMES = {
+    "describe_exception": "client/manager.py",
+    "sanitize_auth_diagnostic": "auth.py",
+}
+#: The one renderer called as a method.
+_RENDERER_METHODS = {"_sanitize_error"}
+#: Callees that receive an exception and do not render its text, each read:
+_NON_RENDERING_CALLEES = {
+    # manager.py: a boolean predicate over the message.
+    "_is_protocol_version_initialize_error",
+    # manager.py: hands the exception to the awaiting connect caller, whose
+    # own `except` is checked here like any other.
+    "set_exception",
+    # policy.py: renders its `error` argument with `exception_text`.
+    "_warn_unparseable",
+    # scoped_advisor_audit.py (#296): records path and keyword only.
+    "record_rejected_arguments",
+    # parsing.py (rev 7): reads a MarkedYAMLError's mark line and column only.
+    "_yaml_position",
+    # server.py (rev 12): a predicate, whether a message or `data` carries
+    # what the chain rejected; it renders nothing.
+    "carries_rejected_value",
+    # manager.py: flattens a group into leaves; its callers render each leaf
+    # with `exception_text` (`describe_exception`).
+    "_iter_leaf_exceptions",
+    # auth.py: see `_NON_RENDERING_FUNCTIONS`.
+    "parse_url_elicitation_error",
+}
+#: Functions that read an exception's text to parse it and return no text.
+_NON_RENDERING_FUNCTIONS = {
+    # auth.py: finds a JSON-RPC -32042 payload in `args[0]` / `str()` and
+    # returns structured `UrlElicitationInfo` (URLs the server sent).
+    "parse_url_elicitation_error",
+    # manager.py: a predicate (above).
+    "_is_protocol_version_initialize_error",
+    # manager.py: yields leaves; never renders.
+    "_iter_leaf_exceptions",
+}
+#: Attributes that carry an exception's text or the rejected value.
+_TEXT_ATTRIBUTES = {
+    "args",
+    "message",
+    "errors",
+    "json",
+    "instance",
+    "validator_value",
+    "context",
+    "cause",
+    "schema",
+    "path",
+    "relative_path",
+    "absolute_path",
+    "json_path",
+    "schema_path",
+    "__notes__",
+    "doc",
+    "msg",
+    "exceptions",
+    "__cause__",
+    "__context__",
+    "__traceback__",
+    "__str__",
+    "__repr__",
+    "__dict__",
+    "add_note",
+}
+_TRACEBACK_RENDERERS = {
+    "format_exc",
+    "format_exception",
+    "format_exception_only",
+    "print_exc",
+    "print_exception",
+    "TracebackException",
+}
+
+
+def _sources() -> list[Path]:
+    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+    return [
+        path
+        for path in sorted(root.rglob("*.py"))
+        if not any(part in ("baml_client",) for part in path.relative_to(root).parts)
+        and path.name != "argument_errors.py"
+    ]
+
+
+def _namespace_for(path: Path) -> dict[str, Any]:
+    root = Path(__file__).resolve().parents[1] / "src"
+    parts = path.relative_to(root).with_suffix("").parts
+    name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
+    return dict(vars(importlib.import_module(name)))
+
+
+def _snippet_namespace(tree: ast.Module) -> dict[str, Any]:
+    """Every import in the source -- function-local ones too -- executed,
+    over the builtins."""
+    namespace: dict[str, Any] = {}
+    imports = [
+        n
+        for n in ast.walk(tree)
+        if isinstance(n, ast.Import) or (isinstance(n, ast.ImportFrom) and n.level == 0)
+    ]
+    for statement in imports:
+        try:
+            exec(  # noqa: S102 -- the source's own import statements
+                compile(
+                    ast.Module(body=[statement], type_ignores=[]), "<imports>", "exec"
+                ),
+                namespace,
+            )
+        except ImportError:
+            continue  # a platform-only module (`msvcrt`); its names stay unresolved
+    return namespace
+
+
+def _catches_validation(node: Any, namespace: dict[str, Any]) -> bool:
+    """Whether an `except`/`isinstance` type expression can match a
+    validation error. Resolved in the module's namespace; unresolvable
+    counts as yes."""
+    if node is None:
+        return True
+    try:
+        # The expression is an `except`/`isinstance` type taken from pmcp's
+        # own source (or a test snippet above), evaluated in that module's
+        # namespace to resolve imports and aliases -- as #296's
+        # `_raised_exceptions` does. No external input reaches it.
+        value = eval(ast.unparse(node), {**vars(builtins), **namespace})  # noqa: S307
+    except Exception:
+        return True
+    pending, classes = [value], []
+    while pending:
+        item = pending.pop()
+        if isinstance(item, tuple):
+            pending.extend(item)
+        elif typing.get_args(item) and not isinstance(item, type):
+            pending.extend(typing.get_args(item))  # `int | float`
+        else:
+            classes.append(item)
+    for cls in classes:
+        if not isinstance(cls, type):
+            return True
+        if any(issubclass(error, cls) for error in _VALIDATION_ERRORS):
+            return True
+    return False
+
+
+def _callee(call: ast.Call) -> str | None:
+    func = call.func
+    if isinstance(func, ast.Name):
+        return func.id
+    if isinstance(func, ast.Attribute):
+        return func.attr
+    return None
+
+
+def _is_renderer_call(call: ast.Call, imported: set[str], label: str) -> bool:
+    func = call.func
+    if isinstance(func, ast.Name):
+        if func.id in ("type", "isinstance"):
+            return True
+        if func.id in _RENDERERS:
+            home = _RENDERER_HOMES.get(func.id)
+            return func.id in imported or (home is not None and label.endswith(home))
+        return func.id in _NON_RENDERING_CALLEES
+    if isinstance(func, ast.Attribute):
+        if (
+            func.attr in _RENDERER_METHODS
+            and isinstance(func.value, ast.Name)
+            and func.value.id == "self"
+        ):
+            return True
+        return func.attr in _NON_RENDERING_CALLEES
+    return False
+
+
+def _scopes(tree: ast.Module) -> list[list[ast.stmt]]:
+    """Each function's body, and the module's own statements."""
+    bodies: list[list[ast.stmt]] = [
+        [
+            s
+            for s in tree.body
+            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
+        ]
+    ]
+    for node in ast.walk(tree):
+        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
+            bodies.append(node.body)
+    return bodies
+
+
+def _own_nodes(body: list[ast.AST]) -> list[ast.AST]:
+    """Nodes of `body`, including nested functions and lambdas -- a closure
+    sees the enclosing scope's names (rev 4: a `def` inside an `except`) --
+    but not nested classes. A nested function is also scanned as its own
+    scope; findings are de-duplicated."""
+    found: list[ast.AST] = []
+    pending: list[ast.AST] = list(body)
+    while pending:
+        node = pending.pop()
+        found.append(node)
+        for child in ast.iter_child_nodes(node):
+            if not isinstance(child, ast.ClassDef):
+                pending.append(child)
+    return found
+
+
+def _is_context_exception(node: ast.AST) -> bool:
+    """`context["exception"]` / `context.get("exception")`: an asyncio loop
+    exception handler's exception (rev 4)."""
+    if isinstance(node, ast.Subscript):
+        key = node.slice
+        return isinstance(key, ast.Constant) and key.value == "exception"
+    return (
+        isinstance(node, ast.Call)
+        and isinstance(node.func, ast.Attribute)
+        and node.func.attr == "get"
+        and bool(node.args)
+        and isinstance(node.args[0], ast.Constant)
+        and node.args[0].value == "exception"
+    )
+
+
+#: A name's region: the ids of the nodes where it holds an exception, or
+#: `None` for the whole scope.
+_Regions = dict[str, "set[int]"]
+
+
+def _widen(regions: _Regions, name: str, region: set[int]) -> bool:
+    before = regions.get(name, set())
+    after = before | region
+    if after == before and name in regions:
+        return False
+    regions[name] = after
+    return True
+
+
+def _ids(nodes: list[ast.AST]) -> set[int]:
+    return {id(node) for node in _own_nodes(nodes)}
+
+
+def _isinstance_names(
+    test: ast.AST, namespace: dict[str, Any], *, positive: bool
+) -> set[str]:
+    """Names `test` narrows to a possible validation error: `isinstance(x, T)`
+    alone or under `and` (positive), or `not isinstance(x, T)` alone or under
+    `or` (negative)."""
+    if positive and isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
+        return set().union(
+            *(_isinstance_names(v, namespace, positive=True) for v in test.values)
+        )
+    if not positive and isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
+        return set().union(
+            *(_isinstance_names(v, namespace, positive=False) for v in test.values)
+        )
+    if not positive:
+        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
+            return _isinstance_names(test.operand, namespace, positive=True)
+        return set()
+    if (
+        isinstance(test, ast.Call)
+        and _callee(test) == "isinstance"
+        and len(test.args) == 2
+        and isinstance(test.args[0], ast.Name)
+        and _catches_validation(test.args[1], namespace)
+    ):
+        return {test.args[0].id}
+    return set()
+
+
+def _exits(body: list[ast.stmt]) -> bool:
+    return bool(body) and isinstance(
+        body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break)
+    )
+
+
+def _loop_targets(node: ast.AST, names: _Regions) -> set[str]:
+    """Targets of a loop that receive an exception: position-mapped through
+    a literal sequence of tuples, `zip(...)` and `enumerate(...)`; every
+    target of an opaque iterable that is an exception name or `.exceptions`."""
+    iterable, target = node.iter, node.target  # type: ignore[attr-defined]
+
+    def mentions(expr: ast.AST) -> bool:
+        return any(
+            (isinstance(n, ast.Name) and n.id in names)
+            or (isinstance(n, ast.Attribute) and n.attr == "exceptions")
+            for n in ast.walk(expr)
+        )
+
+    columns: list[ast.AST] | None = None
+    if isinstance(iterable, ast.Call) and _callee(iterable) == "zip":
+        columns = list(iterable.args)
+    elif (
+        isinstance(iterable, ast.Call)
+        and _callee(iterable) == "enumerate"
+        and iterable.args
+    ):
+        columns = [ast.Constant(0), iterable.args[0]]
+    elif isinstance(iterable, (ast.Tuple, ast.List)) and all(
+        isinstance(e, ast.Tuple) for e in iterable.elts
+    ):
+        width = {len(e.elts) for e in iterable.elts}  # type: ignore[attr-defined]
+        if len(width) == 1:
+            n = width.pop()
+            columns = [
+                ast.Tuple([e.elts[i] for e in iterable.elts], ast.Load())
+                for i in range(n)
+            ]  # type: ignore[attr-defined]
+    if (
+        columns is not None
+        and isinstance(target, ast.Tuple)
+        and len(target.elts) == len(columns)
+    ):
+        return {
+            t.id
+            for t, column in zip(target.elts, columns)
+            if isinstance(t, ast.Name) and mentions(column)
+        }
+    if mentions(iterable):
+        return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
+    return set()
+
+
+def _exception_regions(body: list[ast.stmt], namespace: dict[str, Any]) -> _Regions:
+    regions: _Regions = {}
+    nodes = _own_nodes(body)
+    everywhere = {id(node) for node in nodes}
+    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
+
+    def block_of(node: ast.AST) -> list[ast.AST] | None:
+        if node in body:
+            return list(body)
+        parent = parents.get(node)
+        for field in ("body", "orelse", "finalbody", "handlers"):
+            statements = getattr(parent, field, None)
+            if isinstance(statements, list) and node in statements:
+                return statements
+        return None
+
+    def after(node: ast.AST) -> set[int]:
+        block = block_of(node)
+        return _ids(block[block.index(node) + 1 :]) if block is not None else set()
+
+    for node in nodes:
+        if isinstance(node, ast.ExceptHandler) and node.name:
+            if _catches_validation(node.type, namespace):
+                _widen(regions, node.name, _ids(node.body))
+        elif isinstance(node, (ast.If, ast.While)):
+            for name in _isinstance_names(node.test, namespace, positive=True):
+                _widen(regions, name, _ids(node.body))
+            for name in _isinstance_names(node.test, namespace, positive=False):
+                _widen(regions, name, _ids(node.orelse))
+                if _exits(node.body):
+                    _widen(regions, name, after(node))
+        elif isinstance(node, ast.IfExp):
+            for name in _isinstance_names(node.test, namespace, positive=True):
+                _widen(regions, name, _ids([node.body]))
+            for name in _isinstance_names(node.test, namespace, positive=False):
+                _widen(regions, name, _ids([node.orelse]))
+        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
+            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
+            calls = [c for c in ast.walk(node.value) if isinstance(c, ast.Call)]
+            if (
+                _is_context_exception(node.value)
+                or any(
+                    _callee(c) == "exception" and isinstance(c.func, ast.Attribute)
+                    for c in calls
+                )
+                or any(
+                    _callee(c) == "gather"
+                    and any(
+                        k.arg == "return_exceptions"
+                        and not (
+                            isinstance(k.value, ast.Constant) and k.value.value is False
+                        )
+                        for k in c.keywords
+                    )
+                    for c in calls
+                )
+            ):
+                for t in targets:
+                    if isinstance(t, ast.Name):
+                        _widen(regions, t.id, everywhere)
+
+    def held(use: ast.Name) -> bool:
+        return id(use) in regions.get(use.id, set())
+
+    changed = True
+    while changed:
+        changed = False
+        for node in nodes:
+            if isinstance(node, ast.Assign):
+                sources = [
+                    n
+                    for n in ([node.value] if isinstance(node.value, ast.Name) else [])
+                    + (
+                        [node.value.value]
+                        if isinstance(node.value, ast.Subscript)
+                        and isinstance(node.value.value, ast.Name)
+                        else []
+                    )
+                    if n.id in regions and held(n)
+                ]
+                if sources:
+                    for t in node.targets:
+                        if isinstance(t, ast.Name):
+                            # An alias escapes its region: held everywhere.
+                            changed |= _widen(regions, t.id, everywhere)
+            elif isinstance(node, (ast.For, ast.AsyncFor)):
+                live = {k: v for k, v in regions.items()}
+                for name in _loop_targets(node, live):
+                    changed |= _widen(regions, name, _ids(node.body))
+            elif isinstance(node, ast.comprehension):
+                for name in _loop_targets(node, regions):
+                    changed |= _widen(regions, name, everywhere)
+    # Narrowing the other way: in the `else` of `isinstance(x, T)`, and after
+    # `if isinstance(x, T): raise/return`, `x` is not an exception.
+    for node in nodes:
+        if isinstance(node, ast.If):
+            for name in _isinstance_names(node.test, namespace, positive=True):
+                if name in regions:
+                    regions[name] -= _ids(node.orelse)
+                    if _exits(node.body):
+                        regions[name] -= after(node)
+    return regions
+
+
+def _in_loop_iterable(use: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
+    child, node = use, parents.get(use)
+    while node is not None:
+        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
+            return child is node.iter
+        if isinstance(node, (ast.stmt, ast.Lambda)):
+            return False
+        child, node = node, parents.get(node)
+    return False
+
+
+def exception_sinks(
+    source: str, label: str, namespace: dict[str, Any] | None = None
+) -> list[str]:
+    """Every exception-to-text sink in `source` (see the module docstring)."""
+    tree = ast.parse(source)
+    namespace = {**(namespace or {}), **_snippet_namespace(tree)}
+    for function in ast.walk(tree):
+        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
+            function.name in _NON_RENDERING_FUNCTIONS
+        ):
+            function.body = [ast.Pass()]
+    imported = {
+        alias.asname or alias.name
+        for node in ast.walk(tree)
+        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pmcp")
+        for alias in node.names
+    }
+    parents = {
+        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
+    }
+    found: list[str] = []
+    for function in ast.walk(tree):
+        if (
+            isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
+            and function.name in _RENDERERS
+            and not label.endswith(_RENDERER_HOMES.get(function.name, "\0"))
+        ):
+            found.append(
+                f"{label}:{function.lineno}: shadows the renderer {function.name}"
+            )
+    for body in _scopes(tree):
+        regions = _exception_regions(body, namespace)
+        for use in _own_nodes(body):
+            if not (
+                isinstance(use, ast.Name)
+                and isinstance(use.ctx, ast.Load)
+                and id(use) in regions.get(use.id, set())
+            ):
+                continue
+            if _in_loop_iterable(use, parents):
+                continue  # the loop's targets are tracked instead
+            parent = parents.get(use)
+            if isinstance(parent, ast.keyword):
+                parent = parents.get(parent)
+            if isinstance(parent, ast.Call) and use is not parent.func:
+                if _is_renderer_call(parent, imported, label):
+                    continue
+            elif isinstance(parent, (ast.Raise, ast.Compare, ast.BoolOp, ast.UnaryOp)):
+                continue
+            elif (
+                isinstance(parent, (ast.If, ast.While, ast.Assert))
+                and use is parent.test
+            ):
+                continue
+            elif isinstance(parent, ast.IfExp) and use is parent.test:
+                continue
+            elif isinstance(parent, ast.Assign) and parent.value is use:
+                if all(isinstance(t, ast.Name) for t in parent.targets):
+                    continue
+            elif isinstance(parent, ast.Subscript) and parent.value is use:
+                grand = parents.get(parent)
+                if (
+                    isinstance(grand, ast.Assign)
+                    and grand.value is parent
+                    and all(isinstance(t, ast.Name) for t in grand.targets)
+                ):
+                    continue
+                if isinstance(grand, (ast.Raise, ast.Compare)):
+                    continue
+                if isinstance(grand, ast.Call) and _is_renderer_call(
+                    grand, imported, label
+                ):
+                    continue
+            elif isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and any(
+                n is use for n in ast.walk(parent.iter)
+            ):
+                continue
+            elif (
+                isinstance(parent, ast.Attribute)
+                and parent.attr not in _TEXT_ATTRIBUTES
+            ):
+                grand = parents.get(parent)
+                if not (isinstance(grand, ast.Call) and grand.func is parent):
+                    continue
+            found.append(f"{label}:{use.lineno}: {type(parent).__name__} uses {use.id}")
+    for node in ast.walk(tree):
+        if _is_context_exception(node):
+            parent = parents.get(node)
+            if isinstance(parent, ast.Assign) and all(
+                isinstance(t, ast.Name) for t in parent.targets
+            ):
+                continue  # tracked as an exception name
+            if isinstance(parent, ast.Call) and _is_renderer_call(
+                parent, imported, label
+            ):
+                continue
+            if isinstance(
+                parent, (ast.Compare, ast.BoolOp, ast.If, ast.UnaryOp, ast.Raise)
+            ):
+                continue
+            found.append(f"{label}:{node.lineno}: context['exception'] used as text")
+    for call in ast.walk(tree):
+        if not isinstance(call, ast.Call):
+            continue
+        callee = _callee(call)
+        if (
+            callee == "exception"
+            and (call.args or call.keywords)
+            and not any(k.arg == "timeout" for k in call.keywords)
+        ):
+            found.append(f"{label}:{call.lineno}: logger.exception renders a traceback")
+        if (
+            callee == "exception"
+            and isinstance(call.func, ast.Attribute)
+            and not call.args
+            and all(k.arg == "timeout" for k in call.keywords)
+            # a bare statement discards it (`waits._retrieve` marks the
+            # outcome retrieved): nothing is rendered
+            and not isinstance(parents.get(call), (ast.Assign, ast.IfExp, ast.Expr))
+        ):
+            found.append(f"{label}:{call.lineno}: .exception() used inline")
+        if callee in _TRACEBACK_RENDERERS:
+            found.append(f"{label}:{call.lineno}: traceback.{callee}")
+        if (
+            callee in ("exc_info", "exception")
+            and isinstance(call.func, ast.Attribute)
+            and (isinstance(call.func.value, ast.Name) and call.func.value.id == "sys")
+        ):
+            found.append(f"{label}:{call.lineno}: sys.{callee}()")
+        if callee in ("makeRecord", "handle") and isinstance(call.func, ast.Attribute):
+            found.append(f"{label}:{call.lineno}: {callee} builds a record by hand")
+        for keyword in call.keywords:
+            if keyword.arg == "exc_info" and not (
+                isinstance(keyword.value, ast.Call)
+                and _callee(keyword.value) == "safe_exc_info"
+            ):
+                found.append(
+                    f"{label}:{call.lineno}: exc_info= not through safe_exc_info"
+                )
+            if keyword.arg is None and any(
+                isinstance(k, ast.Constant) and k.value == "exc_info"
+                for k in ast.walk(keyword.value)
+            ):
+                found.append(f"{label}:{call.lineno}: exc_info passed through **")
+    return list(dict.fromkeys(found))
+
+
+def test_no_exception_reaches_text_except_through_the_renderer() -> None:
+    sources = _sources()
+    assert len(sources) > 40, len(sources)
+    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+    found = [
+        sink
+        for path in sources
+        for sink in exception_sinks(
+            path.read_text(), str(path.relative_to(root)), _namespace_for(path)
+        )
+    ]
+    assert found == [], "\n".join(found)
+    # Not vacuous: the analysis tracked exceptions through every module.
+    tracked = sum(
+        len(_exception_regions(body, _namespace_for(path)))
+        for path in sources
+        for body in _scopes(ast.parse(path.read_text()))
+    )
+    assert tracked > 100, tracked
+
+
+#: Every construct the rev 2 board seat fed rev 2's scanner (42; `except*`
+#: needs Python 3.11, this suite runs 3.10), plus the `_connect_with_retry`
+#: regression it found surviving every test. Each must be flagged.
+_FLAGGED = {
+    "alias_used_after_except": "last=None\nfor i in r:\n    try:\n        f()\n    except Exception as e:\n        last = e\nlog(f'{last}')\n",
+    "attr_store_then_render": "try:\n    f()\nexcept Exception as e:\n    self.last_error = e\nlog(f'{self.last_error}')\n",
+    "subscript_store": "try:\n    f()\nexcept Exception as e:\n    errs[name] = e\nlog(str(errs[name]))\n",
+    "import_alias_except": "from pydantic import ValidationError as PVE\ntry:\n    f()\nexcept PVE as e:\n    log(f'{e}')\n",
+    "custom_base_except": "try:\n    f()\nexcept (KeyError, TypeError, PydanticValidationError) as e:\n    log(f'{e}')\n",
+    "isinstance_boolop": "def g(r):\n    if isinstance(r, Exception) and r:\n        log(f'{r}')\n",
+    "isinstance_negative": "def g(r):\n    if not isinstance(r, BaseException):\n        return\n    log(f'{r}')\n",
+    "isinstance_valueerror": "def g(r):\n    if isinstance(r, ValueError):\n        log(f'{r}')\n",
+    "gather_results_loop": "async def g():\n    rs = await asyncio.gather(*ts, return_exceptions=True)\n    for r in rs:\n        log(f'{r}')\n",
+    "task_exception_inline": "def g(t):\n    log(f'{t.exception()}')\n",
+    "task_exception_ifexp": "def g(t):\n    exc = t.exception() if not t.cancelled() else None\n    log(f'{exc}')\n",
+    "sys_exc_info": "import sys\ntry:\n    f()\nexcept Exception:\n    log(str(sys.exc_info()[1]))\n",
+    "sys_exception": "import sys\ntry:\n    f()\nexcept Exception:\n    log(str(sys.exception()))\n",
+    "format_exception_only": "import sys, traceback\ntry:\n    f()\nexcept Exception:\n    log(''.join(traceback.format_exception_only(*sys.exc_info()[:2])))\n",
+    "tb_exception_obj": "import sys, traceback\ntry:\n    f()\nexcept Exception:\n    log(''.join(traceback.TracebackException(*sys.exc_info()).format()))\n",
+    "jsonschema_path": "try:\n    v()\nexcept Exception as e:\n    log(f'bad at {list(e.path)}')\n",
+    "jsonschema_json_path": "try:\n    v()\nexcept Exception as e:\n    log('bad at ' + e.json_path)\n",
+    "jsonschema_relative_path": "try:\n    v()\nexcept Exception as e:\n    log(e.relative_path)\n",
+    "notes": "try:\n    v()\nexcept Exception as e:\n    log(e.__notes__)\n",
+    "jsondecode_doc": "try:\n    v()\nexcept ValueError as e:\n    log(e.doc)\n",
+    "exc_info_kwargs_splat": "try:\n    f()\nexcept Exception:\n    logger.error('x', **{'exc_info': True})\n",
+    "logger_handle_make_record": "import sys\ntry:\n    f()\nexcept Exception:\n    logger.handle(logger.makeRecord('n', 40, 'f', 1, 'x', None, sys.exc_info()))\n",
+    "name_shadow_renderer": "def describe_exception(e):\n    return str(e)\n",
+    "renderer_attr_on_other_obj": "try:\n    f()\nexcept Exception as e:\n    log(other.type(e))\n",
+    "e_str_method": "try:\n    f()\nexcept Exception as e:\n    log(e.__str__())\n",
+    "getattr_args": "try:\n    f()\nexcept Exception as e:\n    log(getattr(e, 'message', ''))\n",
+    "vars_e": "try:\n    f()\nexcept Exception as e:\n    log(vars(e))\n",
+    "e_errors_call": "try:\n    f()\nexcept Exception as e:\n    log(e.errors())\n",
+    "e_json_call": "try:\n    f()\nexcept Exception as e:\n    log(e.json())\n",
+    "future_exception_timeout": "def g(fut):\n    exc = fut.exception(timeout=0)\n    log(f'{exc}')\n",
+    "warnings_warn": "import warnings\ntry:\n    f()\nexcept Exception as e:\n    warnings.warn(f'{e}')\n",
+    "percent_arg": "try:\n    f()\nexcept Exception as e:\n    logger.warning('x %s', e)\n",
+    "repr": "try:\n    f()\nexcept Exception as e:\n    x = repr(e)\n",
+    "chained_raise_text": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError(f'wrap {e}') from e\n",
+    "chained_raise_repr": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError('wrap %r' % (e,)) from e\n",
+    "logging_exception_module": "import logging\ntry:\n    f()\nexcept Exception:\n    logging.exception('x')\n",
+    "logger_exception_noargs": "try:\n    f()\nexcept Exception:\n    logger.exception(msg) if 0 else None\n",
+    "print_exc": "import traceback\ntry:\n    f()\nexcept Exception:\n    traceback.print_exc()\n",
+    "starred_args": "try:\n    f()\nexcept Exception as e:\n    log(*e.args)\n",
+    "exc_group_loop_var": "try:\n    f()\nexcept BaseExceptionGroup as eg:\n    for x in eg.exceptions:\n        log(f'{x}')\n",
+    "lambda_capture": "try:\n    f()\nexcept Exception as e:\n    cb = lambda: str(e)\n",
+    "return_exception": "def g():\n    try:\n        f()\n    except Exception as e:\n        return e\n",
+    "truncated_copy": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError(str(e)[:200]) from e\n",
+    "closure_in_except": (
+        "try:\n    f()\nexcept Exception as e:\n    def inner():\n        log(f'{e}')\n    later(inner)\n"
+    ),
+    "loop_exception_handler": (
+        "def handler(loop, context):\n    log(f\"{context['exception']}\")\n"
+    ),
+    "loop_exception_handler_alias": (
+        "def handler(loop, context):\n    exc = context.get('exception')\n    log(str(exc))\n"
+    ),
+    "connect_with_retry_regression": (
+        "async def _connect_with_retry(self, config):\n"
+        "    last_error = None\n"
+        "    for attempt in range(3):\n"
+        "        try:\n"
+        "            await self._connect_server(config)\n"
+        "            return\n"
+        "        except Exception as e:\n"
+        "            last_error = e\n"
+        "    if last_error:\n"
+        "        logger.warning(f'giving up: {last_error}')\n"
+        "        raise last_error\n"
+    ),
+}
+
+
+@pytest.mark.parametrize("label", sorted(_FLAGGED))
+def test_the_scanner_flags_each_construct(label: str) -> None:
+    """The static check is only as wide as its rules: each fires."""
+    assert exception_sinks(_FLAGGED[label], label), label
+
+
+def test_the_scanner_passes_the_renderers() -> None:
+    clean = (
+        "from pmcp.argument_errors import exception_text, safe_exc_info\n"
+        "try:\n    f()\nexcept Exception as e:\n"
+        "    last = e\n"
+        "    log(f'{exception_text(e)}', exc_info=safe_exc_info(e))\n"
+        "    if e.code == 1 or isinstance(e, KeyError):\n        raise\n"
+        "    raise X() from e\n"
+        "try:\n    f()\nexcept KeyError as e:\n    log(f'{e}')\n"
+        "def g(fut):\n    exc = fut.exception()\n    if exc is not None:\n"
+        "        log(exception_text(exc))\n        raise exc\n"
+    )
+    assert exception_sinks(clean, "clean") == []
+
+
+# --- rev 12: a value pmcp rejects by hand is never rendered with repr ---------
+
+#: Rejection wording: a message carrying one of these, built with `!r`,
+#: `repr(...)` or `%r` of a non-constant, is a hand-rendered rejection.
+#: Rev 13 (round-12 claude): bad, wrong, failed, cannot, unrecognised and
+#: not found added.
+_REJECTION_WORDS = re.compile(
+    r"(?i)(invalid|unusable|unexpected|malformed|reject|refus|ignor|unsupported"
+    r"|unknown|unsafe|not a valid|not permitted|not allowed|must be|expected"
+    r"|illegal|disallowed|forbidden|denied|blocked|skipping|unparseable"
+    r"|unreadable|repeated|not an? |does not |must match|bad|wrong|fail"
+    r"|cannot|can't|unrecogni[sz]|not found)"
+)
+
+
+def _is_repr_call(node: ast.AST) -> bool:
+    """`repr(<non-constant>)` or `ascii(<non-constant>)`."""
+    return (
+        isinstance(node, ast.Call)
+        and isinstance(node.func, ast.Name)
+        and node.func.id in ("repr", "ascii")
+        and bool(node.args)
+        and not isinstance(node.args[0], ast.Constant)
+    )
+
+
+_PERCENT = re.compile(
+    r"%(?:\((?P<key>[^)]*)\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?(?P<conv>[a-zA-Z%])"
+)
+
+
+def _percent_repr_targets(text: str, args: list[ast.AST]) -> list[ast.AST]:
+    """The arguments a `%`-format renders with `%r` or `%a`, by position (or
+    by key, for a single mapping argument)."""
+    targets: list[ast.AST] = []
+    position = 0
+    for match in _PERCENT.finditer(text):
+        conv = match.group("conv")
+        if conv == "%":
+            continue
+        if match.group("key") is not None:
+            if conv in "ra" and len(args) == 1 and isinstance(args[0], ast.Dict):
+                for k, v in zip(args[0].keys, args[0].values):
+                    if isinstance(k, ast.Constant) and k.value == match.group("key"):
+                        targets.append(v)
+            continue
+        if conv in "ra" and position < len(args):
+            targets.append(args[position])
+        position += 1
+    return [t for t in targets if not isinstance(t, ast.Constant)]
+
+
+def _format_repr_targets(
+    text: str, args: list[ast.AST], keywords: dict[str, ast.AST]
+) -> list[ast.AST]:
+    """The arguments a `str.format` renders with `!r` or `!a`."""
+    import string
+
+    targets: list[ast.AST] = []
+    auto = 0
+    try:
+        fields = list(string.Formatter().parse(text))
+    except ValueError:
+        return []
+    for _, name, _, conversion in fields:
+        if name is None:
+            continue
+        head = name.split(".")[0].split("[")[0]
+        if head == "":
+            index: int | str = auto
+            auto += 1
+        elif head.isdigit():
+            index = int(head)
+        else:
+            index = head
+        if conversion not in ("r", "a"):
+            continue
+        if isinstance(index, int) and index < len(args):
+            targets.append(args[index])
+        elif isinstance(index, str) and index in keywords:
+            targets.append(keywords[index])
+    return [t for t in targets if not isinstance(t, ast.Constant)]
+
+
+def _constant_text(node: ast.AST) -> str:
+    """The literal text of a string expression: a constant, an f-string's
+    constant parts, or the constant operands of a `+` chain."""
+    if isinstance(node, ast.Constant) and isinstance(node.value, str):
+        return node.value
+    if isinstance(node, ast.JoinedStr):
+        return "".join(_constant_text(v) for v in node.values)
+    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
+        return _constant_text(node.left) + _constant_text(node.right)
+    return ""
+
+
+def _repr_sites(source: str) -> list[tuple[int, str]]:
+    """Each value rendered with its repr (`!r`, `repr()`, `+`, `%r`,
+    `.format`, a later call argument) in a message with rejection wording,
+    as (line, expression)."""
+    tree = ast.parse(source)
+    sites: list[tuple[int, str]] = []
+
+    def add(node: ast.AST, value: ast.AST) -> None:
+        sites.append((getattr(node, "lineno", 0), ast.unparse(value)))
+
+    def reprs_in(node: ast.AST) -> list[ast.AST]:
+        return [n.args[0] for n in ast.walk(node) if _is_repr_call(n)]  # type: ignore[attr-defined]
+
+    for node in ast.walk(tree):
+        if isinstance(node, ast.JoinedStr):
+            if not _REJECTION_WORDS.search(_constant_text(node)):
+                continue
+            for v in node.values:
+                if not isinstance(v, ast.FormattedValue) or isinstance(
+                    v.value, ast.Constant
+                ):
+                    continue
+                if v.conversion in (ord("r"), ord("a")):
+                    add(node, v.value)
+                elif _is_repr_call(v.value):
+                    add(node, v.value.args[0])  # type: ignore[attr-defined]
+        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
+            if isinstance(getattr(node, "_parent_add", None), ast.BinOp):
+                continue
+            if _REJECTION_WORDS.search(_constant_text(node)):
+                operands = []
+                stack: list[ast.AST] = [node]
+                while stack:
+                    current = stack.pop()
+                    if isinstance(current, ast.BinOp) and isinstance(
+                        current.op, ast.Add
+                    ):
+                        current.left._parent_add = current  # type: ignore[attr-defined]
+                        current.right._parent_add = current  # type: ignore[attr-defined]
+                        stack += [current.left, current.right]
+                    else:
+                        operands.append(current)
+                for operand in operands:
+                    if _is_repr_call(operand):
+                        add(node, operand.args[0])  # type: ignore[attr-defined]
+        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
+            text = _constant_text(node.left)
+            if not _REJECTION_WORDS.search(text):
+                continue
+            values = (
+                node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
+            )
+            for value in _percent_repr_targets(text, values):
+                add(node, value)
+            for value in values:
+                for inner in reprs_in(value):
+                    add(node, inner)
+        elif isinstance(node, ast.Call):
+            func = node.func
+            if (
+                isinstance(func, ast.Attribute)
+                and func.attr == "format"
+                and _REJECTION_WORDS.search(_constant_text(func.value))
+            ):
+                text = _constant_text(func.value)
+                arguments = list(node.args) + [k.value for k in node.keywords]
+                keywords = {k.arg: k.value for k in node.keywords if k.arg}
+                for value in _format_repr_targets(text, list(node.args), keywords):
+                    add(node, value)
+                for value in arguments:
+                    for inner in reprs_in(value):
+                        add(node, inner)
+            elif node.args and _REJECTION_WORDS.search(_constant_text(node.args[0])):
+                text = _constant_text(node.args[0])
+                if isinstance(node.args[0], ast.JoinedStr):
+                    continue  # its fields are the JoinedStr branch's
+                for value in _percent_repr_targets(text, list(node.args[1:])):
+                    add(node, value)
+                for value in node.args[1:]:
+                    for inner in reprs_in(value):
+                        add(node, inner)
+    return sites
+
+
+def _repr_rejections(source: str) -> list[int]:
+    """Lines in `source` that render a non-constant with its repr inside a
+    message carrying rejection wording (`_repr_sites`)."""
+    return sorted({line for line, _ in _repr_sites(source)})
+
+
+#: Sites whose repr'd value is the operator's own (config, environment, CLI
+#: arguments, pmcp's own stores) or pmcp's own child: outside Consiliency/
+#: pmcp#297's caller/downstream class, and tracked in Consiliency/pmcp#315.
+#: A downstream's or a caller's value is described with `describe_value`.
+#: Rev 13 (round-12 claude): keyed by SITE -- the module, the function and
+#: the rendered expression -- not by function, so a new repr inside an
+#: exempt function is a new key, and fails until triaged.
+_REPR_REJECTION_EXEMPT: dict[str, str] = {
+    "cli.py::_exact_package_spec::spec": "operator CLI argument",
+    "cli.py::_run_trust_revoke_package::args.spec": "operator CLI argument",
+    "client/manager.py::_request_ceiling_ms::raw": "operator environment",
+    "client/manager.py::_stdio_read_limit::raw": "operator environment",
+    "config/loader.py::registry_allow_private_from_config::value": "operator config",
+    "manifest/npm_resolver.py::_query_locked::status": "pmcp's own resolver child",
+    "manifest/npm_resolver.py::resolve::command": "manifest/config command",
+    "manifest/package_identity.py::_fetch_packument::name": (
+        "an accepted, validated package name; the failure is the registry fetch's"
+    ),
+    "manifest/refresher.py::check_staleness::cfg_name": "configured package",
+    "manifest/refresher.py::check_staleness::desc.package": "cached package",
+    "package_approvals.py::_decode::decision": "pmcp's own approval store",
+    "package_approvals.py::_read_store_and_stale::entry['name']": (
+        "pmcp's own approval store"
+    ),
+    "package_approvals.py::_read_store_and_stale::entry['resolved_version']": (
+        "pmcp's own approval store"
+    ),
+    "package_approvals.py::_require_identity_fields::name": (
+        "pmcp's own approval store"
+    ),
+    "package_approvals.py::_require_identity_fields::registry": (
+        "pmcp's own approval store"
+    ),
+    "provision_gate.py::evaluate_provision::getattr(server_config, 'name', None)": (
+        "a resolved server config's name (operator/manifest config)"
+    ),
+    "trust_store.py::_decode::decision": "pmcp's own trust store",
+    "trust_store.py::record_resolved::decision": "pmcp's own trust store",
+    "validation.py::parse_package_spec::spec": "config/CLI/manifest package spec",
+}
+
+
+def _repr_site_keys() -> set[str]:
+    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+    found: set[str] = set()
+    for path in sorted(root.rglob("*.py")):
+        if "baml_client" in path.parts:
+            continue
+        source = path.read_text()
+        tree = ast.parse(source)
+        functions = [
+            n
+            for n in ast.walk(tree)
+            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
+        ]
+        counts: dict[str, int] = {}
+        for line, expression in sorted(_repr_sites(source)):
+            owners = [
+                f for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)
+            ]
+            owner = (
+                min(owners, key=lambda f: (f.end_lineno or f.lineno) - f.lineno).name
+                if owners
+                else "<module>"
+            )
+            key = f"{path.relative_to(root).as_posix()}::{owner}::{expression}"
+            counts[key] = counts.get(key, 0) + 1
+            found.add(key if counts[key] == 1 else f"{key}#{counts[key]}")
+    return found
+
+
+def test_no_rejected_value_is_rendered_with_repr() -> None:
+    """A repr in a rejecting message renders the value: every such site in
+    `src/pmcp` is a named exemption with its provenance, and each exemption
+    must still exist. `{x}`/`%s` of `x` were triaged by hand."""
+    found = _repr_site_keys()
+    assert found == set(_REPR_REJECTION_EXEMPT), (
+        sorted(found - set(_REPR_REJECTION_EXEMPT)),
+        sorted(set(_REPR_REJECTION_EXEMPT) - found),
+    )
+
+
+@pytest.mark.parametrize(
+    "snippet, flagged",
+    [
+        ('f"unusable cursor ({raw!r})"', True),
+        ('f"invalid value {repr(v)}"', True),
+        ('logger.warning("Ignoring %s: got %r", k, v)', True),
+        # rev 13 (round-12 claude): every other spelling, and the new words.
+        ('"invalid: " + repr(x)', True),
+        ('"invalid: " + name + " " + repr(x)', True),
+        ('"invalid: %r" % x', True),
+        ('"invalid: %r and %r" % (x, y)', True),
+        ('"invalid: %s" % repr(x)', True),
+        ('"invalid {!r}".format(x)', True),
+        ('"invalid {}".format(repr(x))', True),
+        ('logger.warning("invalid %s", repr(x))', True),
+        ('logger.warning("bad cursor: %r", x)', True),
+        ('f"wrong value {x!r}"', True),
+        ('f"lookup failed for {x!r}"', True),
+        ('f"cannot use {x!r}"', True),
+        ('f"unrecognised {x!r}"', True),
+        ('f"not found: {x!r}"', True),
+        ('"invalid: " + str(x)', False),
+        ('"invalid: %s" % x', False),
+        ('"connected to %r" % x', False),
+        ('logger.debug("fetch failed for %s: %s", name, repr(e))', True),
+        ('logger.debug("fetch failed for %s: %r", name, exception_text(e))', True),
+        ('logger.debug("fetch failed for %r: %s", "pkg", exception_text(e))', False),
+        ('"invalid %(v)r" % {"v": x}', True),
+        ('"invalid {0} {1!r}".format(a, b)', True),
+        ('"invalid {0!r}".format("const")', False),
+        ('f"invalid {x!a}"', True),
+        ('f"unusable cursor ({describe_value(raw)})"', False),
+        ('f"connected to {name!r}"', False),
+        ("f\"invalid {'x'!r}\"", False),
+    ],
+)
+def test_the_repr_rejection_rule_sees_its_spellings(
+    snippet: str, flagged: bool
+) -> None:
+    assert bool(_repr_rejections(snippet)) is flagged, snippet
+
+
+# --- rev 19: a pmcp-authored description is raised outside the handler ------
+#
+# Since rev 18 an exception whose chain holds a validation or parse error is
+# rendered as its class and that error's description, never its own message.
+# A pmcp message built with a renderer (`f"Invalid policy file {path}:
+# {exception_text(e)}"`) and raised inside the handler of such an error would
+# therefore be withheld, and the operator would lose the file and the
+# refusal. pmcp builds the description in the handler and raises after it,
+# so the exception chains nothing (round-17 claude F001).
+
+_DESCRIBERS = _RENDERERS - {"safe_exc_info"}
+
+
+def _calls_a_describer(node: ast.AST) -> bool:
+    return any(
+        isinstance(inner, ast.Call)
+        and (_callee(inner) in _DESCRIBERS or _callee(inner) in _RENDERER_METHODS)
+        for inner in ast.walk(node)
+    )
+
+
+def _handler_nodes(handler: ast.ExceptHandler) -> list[ast.AST]:
+    """The handler's own statements' nodes, not those of a nested function
+    or class (which run later, outside the handler)."""
+    out: list[ast.AST] = []
+    pending: list[ast.AST] = list(handler.body)
+    while pending:
+        node = pending.pop()
+        out.append(node)
+        if isinstance(
+            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
+        ):
+            continue
+        pending.extend(ast.iter_child_nodes(node))
+    return out
+
+
+def _described_raises_in_handlers(source: str) -> list[int]:
+    """Lines of each `raise` inside an `except` body whose exception carries a
+    renderer's output: called in the raise itself, or through a name the same
+    handler bound to an expression that calls one (`failure =
+    exception_text(e)`, then `raise X(f"... {failure}")` or `raise error`)."""
+    lines: list[int] = []
+    for handler in ast.walk(ast.parse(source)):
+        if not isinstance(handler, ast.ExceptHandler):
+            continue
+        nodes = _handler_nodes(handler)
+        described: set[str] = set()
+        for node in nodes:
+            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
+                value = node.value
+                targets = (
+                    node.targets if isinstance(node, ast.Assign) else [node.target]
+                )
+                if value is not None and _calls_a_describer(value):
+                    described |= {t.id for t in targets if isinstance(t, ast.Name)}
+        for node in nodes:
+            if not isinstance(node, ast.Raise) or node.exc is None:
+                continue
+            names = {n.id for n in ast.walk(node.exc) if isinstance(n, ast.Name)}
+            if _calls_a_describer(node.exc) or names & described:
+                lines.append(node.lineno)
+    return sorted(lines)
+
+
+def test_no_pmcp_description_is_raised_inside_a_handler() -> None:
+    """Every pmcp exception whose message holds a renderer's description is
+    raised after its handler, so its own text is what the operator sees."""
+    found = [
+        f"{path.relative_to(path.parents[2])}:{line}"
+        for path in _sources()
+        for line in _described_raises_in_handlers(path.read_text())
+    ]
+    assert not found, found
+
+
+@pytest.mark.parametrize(
+    ("snippet", "flagged"),
+    [
+        (
+            "try:\n    f()\nexcept ValueError as e:\n"
+            "    raise RuntimeError(f'bad {exception_text(e)}') from e\n",
+            True,
+        ),
+        (
+            "try:\n    f()\nexcept ValueError as e:\n"
+            "    raise RuntimeError(f'bad {exception_text(e)}')\n",
+            True,
+        ),
+        (
+            "try:\n    f()\nexcept ValueError as e:\n"
+            "    if x:\n        raise RuntimeError(describe_model_error(e, s, a)) from None\n",
+            True,
+        ),
+        (
+            "try:\n    f()\nexcept ValueError as e:\n"
+            "    error = RuntimeError('bad ' + exception_text(e))\n    raise error\n",
+            True,
+        ),
+        (
+            "try:\n    f()\nexcept ValueError as e:\n"
+            "    failure = exception_text(e)\nraise RuntimeError(f'bad {failure}')\n",
+            False,
+        ),
+        (
+            "try:\n    f()\nexcept ValueError as e:\n"
+            "    failure = exception_text(e)\n"
+            "    raise RuntimeError(f'bad {failure}') from e\n",
+            True,
+        ),
+        (
+            "try:\n    f()\nexcept ValueError as e:\n    raise RuntimeError('fixed') from e\n",
+            False,
+        ),
+        (
+            "try:\n    f()\nexcept ValueError as e:\n"
+            "    def later():\n        raise RuntimeError(exception_text(e))\n",
+            False,
+        ),
+    ],
+)
+def test_the_described_raise_rule_sees_its_spellings(
+    snippet: str, flagged: bool
+) -> None:
+    assert bool(_described_raises_in_handlers(snippet)) is flagged, snippet
````

### Patch — `tests/test_gateway_tool_schemas.py`

````diff
--- a/tests/test_gateway_tool_schemas.py
+++ b/tests/test_gateway_tool_schemas.py
@@ -438 +438,5 @@
-        ("gateway.describe", {"tool_id": ""}, "should be non-empty"),
+        (
+            "gateway.describe",
+            {"tool_id": ""},
+            "$.tool_id: must be at least 1 character",
+        ),
@@ -442 +446 @@
-            "is too short",
+            "$.title: must be at least 8 characters",
@@ -447 +451 @@
-            "less than the minimum",
+            "$.options.max_output_chars: must be greater than or equal to",
````

### Patch — `tests/test_http_transport.py`

````diff
--- a/tests/test_http_transport.py
+++ b/tests/test_http_transport.py
@@ -491,0 +492,46 @@
+    @pytest.mark.parametrize(
+        "client_kwargs",
+        [
+            {},
+            {"allowed_origins": ["https://app.example"]},
+            {"auth_token": "gateway-token"},
+        ],
+        ids=["default", "allowlist", "shared-secret"],
+    )
+    def test_malformed_origin_port_is_rejected_without_echo(
+        self,
+        client_kwargs: dict[str, object],
+        capfd: pytest.CaptureFixture[str],
+        caplog: pytest.LogCaptureFixture,
+    ) -> None:
+        """An unauthenticated Origin whose port is not a number is a 403, not a
+        500 whose log line quotes the port (Consiliency/pmcp#297, rev 7 B2)."""
+        import logging
+
+        # ASCII: an Origin header is Latin-1 on the wire.
+        sentinel = "Q7portSentinel" + "x" * 20
+        client = _make_contract_client(**client_kwargs)  # type: ignore[arg-type]
+        caplog.set_level(logging.DEBUG)
+
+        response = client.post(
+            "/mcp",
+            headers={"Origin": f"http://evil.example:{sentinel}"},
+            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
+        )
+
+        assert response.status_code == 403
+        out, err = capfd.readouterr()
+        observed = "\n".join(
+            [
+                response.text,
+                str(dict(response.headers)),
+                out,
+                err,
+                *(record.getMessage() for record in caplog.records),
+                *(str(record.exc_info) for record in caplog.records),
+            ]
+        )
+        assert len(sentinel) == 34
+        assert sentinel not in observed
+        assert sentinel.encode("utf-8").hex() not in observed.encode("utf-8").hex()
+
@@ -594,0 +641,380 @@
+
+
+# --- rev 19: the SDK's transport rejections are value-free (round-17 grok F001,
+# claude N1). The MCP SDK answers a /mcp request it cannot accept before any
+# pmcp handler runs; three of its rejections were built from the request.
+
+_GRID_SENTINELS = {
+    "long": "sk-live-" + "Zq" * 12 + "SECRETVALUE",
+    "short": "Qx7",
+}
+_MCP_HEADERS = {
+    "content-type": "application/json",
+    "accept": "application/json, text/event-stream",
+}
+
+
+def _envelope_shapes(s: str) -> dict[str, bytes]:
+    """Every way a /mcp body can fail the SDK's envelope parse or validation,
+    with the sentinel in a position the caller controls (the request id is
+    excluded: JSON-RPC requires a reply to carry it)."""
+    import json
+
+    def body(value: object) -> bytes:
+        return json.dumps(value).encode()
+
+    call = {"name": "gateway.invoke", "arguments": {"token": s, s: [s]}}
+    return {
+        "jsonrpc-version": body(
+            {"jsonrpc": "1.0", "id": 1, "method": "tools/call", "params": call}
+        ),
+        "jsonrpc-missing": body({"id": 1, "method": "tools/call", "params": call}),
+        "method-object": body({"jsonrpc": "2.0", "id": 1, "method": {"m": s}}),
+        "method-number-params": body(
+            {"jsonrpc": "2.0", "id": 1, "method": 5, "params": call}
+        ),
+        "params-scalar": body(
+            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": s}
+        ),
+        "params-list": body(
+            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [s]}
+        ),
+        "response-shaped": body({"jsonrpc": "2.0", "id": 1, "error": s}),
+        "result-and-error": body(
+            {"jsonrpc": "2.0", "id": 1, "result": {"k": s}, "error": {"m": s}}
+        ),
+        "batch": body([{"jsonrpc": "2.0", "id": 1, "method": s}]),
+        "scalar": body(s),
+        "number-key": body({s: s}),
+        "truncated": b'{"jsonrpc":"2.0","method":"' + s.encode(),
+        "bad-escape": b'{"jsonrpc":"2.0","method":"' + s.encode() + b'\\q"}',
+        "not-utf8": b'{"jsonrpc":"2.0","method":"' + s.encode() + b'\xff"}',
+        "trailing": b'{"jsonrpc":"2.0","method":"x"} ' + s.encode(),
+    }
+
+
+def _modern_version_shape(s: str) -> tuple[bytes, dict[str, str]]:
+    """A per-request-envelope call naming an unsupported protocol version."""
+    import json
+
+    body = {
+        "jsonrpc": "2.0",
+        "id": 1,
+        "method": "tools/list",
+        "params": {
+            "_meta": {
+                "io.modelcontextprotocol/protocolVersion": s,
+                "io.modelcontextprotocol/clientCapabilities": {},
+            }
+        },
+    }
+    headers = {**_MCP_HEADERS, "mcp-protocol-version": s, "mcp-method": "tools/list"}
+    return json.dumps(body).encode(), headers
+
+
+def _post_all(
+    cases: list[tuple[str, bytes, dict[str, str]]],
+) -> dict[str, tuple[int, str, str]]:
+    """POST each body to a real `create_http_app` /mcp."""
+    from mcp.server.lowlevel import Server
+
+    from pmcp.transport.http import create_http_app
+
+    out: dict[str, tuple[int, str, str]] = {}
+    with TestClient(create_http_app(Server("grid")), base_url="http://127.0.0.1") as c:
+        for name, body, headers in cases:
+            response = c.post("/mcp", content=body, headers=headers)
+            out[name] = (
+                int(response.status_code),
+                response.text,
+                str(dict(response.headers)),
+            )
+    return out
+
+
+@pytest.mark.parametrize("sentinel", sorted(_GRID_SENTINELS))
+@pytest.mark.parametrize("era", ["handshake", "per-request-envelope"])
+def test_a_rejected_envelope_echoes_nothing_of_the_request(
+    era: str, sentinel: str, caplog: pytest.LogCaptureFixture
+) -> None:
+    """Every malformed-envelope shape, on both SDK request paths, gets a 4xx
+    JSON-RPC error whose body, headers and the log carry no form of the
+    sentinel; a parse or validation rejection says why from its structure."""
+    import json
+    import logging
+
+    from mcp_types.version import MODERN_PROTOCOL_VERSIONS
+
+    from tests.test_argument_error_echo import _forbidden, _record_text
+
+    s = _GRID_SENTINELS[sentinel]
+    headers = dict(_MCP_HEADERS)
+    if era == "per-request-envelope":
+        headers["mcp-protocol-version"] = MODERN_PROTOCOL_VERSIONS[0]
+    cases = [(name, body, headers) for name, body in _envelope_shapes(s).items()]
+    if era == "per-request-envelope":
+        body, version_headers = _modern_version_shape(s)
+        cases.append(("unsupported-version", body, version_headers))
+    caplog.set_level(logging.DEBUG)
+    observed = _post_all(cases)
+    logs = "\n".join(_record_text(record) for record in caplog.records)
+
+    def leaked(text: str) -> bool:
+        if sentinel == "short":
+            return s in text
+        return any(form in text for form in _forbidden(s))
+
+    failures = []
+    for name, (status, text, response_headers) in observed.items():
+        if not 400 <= status < 500:
+            failures.append((name, "status", status))
+        payload = json.loads(text)
+        error = payload.get("error") or {}
+        if not isinstance(error.get("code"), int) or not isinstance(
+            error.get("message"), str
+        ):
+            failures.append((name, "not a JSON-RPC error", text[:200]))
+        if leaked(text) or leaked(response_headers):
+            failures.append((name, "response", text[:300]))
+        if error.get("code") == -32602 and payload.get("id") is None:
+            if not error["message"].startswith("Validation error: "):
+                failures.append((name, "validation text", error["message"]))
+    assert not failures, failures
+    assert not leaked(logs), [line for line in logs.splitlines() if leaked(line)][:5]
+
+
+def test_envelope_rejection_does_not_echo_caller_value() -> None:
+    """Round-17 grok F001's falsifier, through the gateway's own /mcp (the
+    filed form drove the SDK's transport class directly, which pmcp serves
+    only behind this app): a `tools/call` the SDK's envelope adapter rejects
+    omits the argument."""
+    secret = "sk-live-SECRETVALUE"
+    body = (
+        b'{"jsonrpc":"1.0","id":1,"method":"tools/call","params":'
+        b'{"name":"gateway.invoke","arguments":{"token":"' + secret.encode() + b'"}}}'
+    )
+    status, text, headers = _post_all([("grok", body, _MCP_HEADERS)])["grok"]
+    assert status == 400
+    assert secret not in text and secret not in headers
+    assert '"code":-32602' in text and "Validation error: " in text
+
+
+def test_value_free_rejection_rewrites_only_request_built_parts() -> None:
+    """A result, a handler's error (it has an id) and an SDK literal pass
+    unchanged; the three request-built rejections are rewritten."""
+    import json
+
+    from pmcp.transport.http import value_free_rejection
+
+    request = b'{"jsonrpc":"1.0","id":1,"method":"m","params":{"k":"SECRETzz"}}'
+    unchanged = [
+        b'{"jsonrpc":"2.0","id":1,"result":{"k":"SECRETzz"}}',
+        b'{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"bad: SECRETzz"}}',
+        b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Session not found"}}',
+        b"Request body too large",
+    ]
+    for body in unchanged:
+        assert value_free_rejection(body, request) == body, body
+    validation = json.dumps(
+        {
+            "jsonrpc": "2.0",
+            "id": None,
+            "error": {"code": -32602, "message": "Validation error: SECRETzz"},
+        }
+    ).encode()
+    parse = json.dumps(
+        {
+            "jsonrpc": "2.0",
+            "id": None,
+            "error": {"code": -32700, "message": "Parse error: SECRETzz"},
+        }
+    ).encode()
+    version = json.dumps(
+        {
+            "jsonrpc": "2.0",
+            "id": 1,
+            "error": {
+                "code": -32022,
+                "message": "Unsupported protocol version",
+                "data": {"supported": ["2026-07-28"], "requested": "SECRETzz"},
+            },
+        }
+    ).encode()
+    for body in (validation, parse, version):
+        rewritten = value_free_rejection(body, request)
+        assert b"SECRETzz" not in rewritten, rewritten
+        assert (
+            json.loads(rewritten)["error"]["code"] == json.loads(body)["error"]["code"]
+        )
+    kept = version.replace(b"SECRETzz", b"2099-01-01")
+    assert (
+        value_free_rejection(kept, request)
+        == json.dumps(json.loads(kept), separators=(",", ":")).encode()
+    )
+
+
+#: Every non-literal message or `data` the SDK's server-transport modules can
+#: put in a rejection, and why it is safe or where pmcp rewrites it. Keyed by
+#: (module, call, the argument's source). An SDK upgrade that adds one fails
+#: `test_every_sdk_rejection_message_is_reviewed` until it is reviewed here.
+_SDK_REJECTION_SITES: dict[tuple[str, str, str], str] = {
+    (
+        "mcp.server.streamable_http",
+        "_create_error_response",
+        "f'Parse error: {str(e)}'",
+    ): "request-built: rewritten by value_free_rejection (PARSE_ERROR, id null)",
+    (
+        "mcp.server.streamable_http",
+        "_create_error_response",
+        "f'Validation error: {str(e)}'",
+    ): "request-built: rewritten by value_free_rejection (INVALID_PARAMS, id null)",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        "UnsupportedProtocolVersionErrorData(supported=list(supported_modern_versions), requested=protocol_version).model_dump(mode='json')",
+    ): "request-built `requested`: rewritten by value_free_rejection",
+    (
+        "mcp.server.streamable_http",
+        "ErrorData",
+        "error_message",
+    ): "plumbing: the message of _create_error_response, reviewed at its callers",
+    (
+        "mcp.server.streamable_http",
+        "Response",
+        "error_response.model_dump_json(by_alias=True, exclude_unset=True)",
+    ): "plumbing: serialises the reviewed ErrorData",
+    (
+        "mcp.server.streamable_http",
+        "Response",
+        "response_message.model_dump_json(by_alias=True, exclude_unset=True) if response_message else None",
+    ): "a handler's own response (JSON mode), not a rejection",
+    (
+        "mcp.server.streamable_http_manager",
+        "Response",
+        "body.model_dump_json(by_alias=True, exclude_unset=True)",
+    ): "plumbing: `Session not found`, a literal",
+    (
+        "mcp.server._streamable_http_modern",
+        "ErrorData",
+        "rejection.message",
+    ): "plumbing: an InboundLadderRejection's message, reviewed at its sites",
+    (
+        "mcp.server._streamable_http_modern",
+        "ErrorData",
+        "rejection.data",
+    ): "plumbing: an InboundLadderRejection's data, reviewed at its sites",
+    (
+        "mcp.server._streamable_http_modern",
+        "Response",
+        "json.dumps(body, separators=(',', ':'))",
+    ): "plumbing: serialises a reviewed JSON-RPC message",
+    (
+        "mcp.server._streamable_http_modern",
+        "InboundLadderRejection",
+        "f'{duplicated} header appears more than once'",
+    ): "`duplicated` is one of the SDK's fixed routing-header names",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        "f'params._meta must be an object carrying the required {PROTOCOL_VERSION_META_KEY!r} and {CLIENT_CAPABILITIES_META_KEY!r} envelope keys'",
+    ): "SDK constants",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        "f\"params._meta is missing the required envelope key(s): {', '.join(missing)}\"",
+    ): "`missing` holds SDK constants",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        'f"{MCP_PROTOCOL_VERSION_HEADER} header does not match the request envelope\'s protocol version"',
+    ): "SDK constant",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        'f"{MCP_METHOD_HEADER} header does not match the request body\'s method"',
+    ): "SDK constant",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        'f"{MCP_NAME_HEADER} header does not match the request body\'s {name_key!r} parameter"',
+    ): "`name_key` is from the SDK's NAME_BEARING_METHODS",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        "f'{header_name} header appears more than once'",
+    ): "`header_name` is from the tool's own x-mcp-header schema token",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        'f"{header_name} header is present but the request body\'s {argument!r} argument is absent"',
+    ): "schema token and schema path",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        'f"{header_name} header does not match the request body\'s {argument!r} argument"',
+    ): "schema token and schema path",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        'f"{header_name} header is missing but the request body\'s {argument!r} argument is present"',
+    ): "schema token and schema path",
+    (
+        "mcp.shared.inbound",
+        "InboundLadderRejection",
+        "f'{header_name} header carries a malformed base64 sentinel value'",
+    ): "schema token",
+}
+_SDK_REJECTION_MODULES = (
+    "mcp.server.streamable_http",
+    "mcp.server.streamable_http_manager",
+    "mcp.server._streamable_http_modern",
+    "mcp.shared.inbound",
+    "mcp.server.transport_security",
+)
+_SDK_REJECTION_CALLS = {
+    "_create_error_response",
+    "ErrorData",
+    "InboundLadderRejection",
+    "Response",
+    "JSONResponse",
+    "PlainTextResponse",
+}
+
+
+def _sdk_rejection_sites() -> set[tuple[str, str, str]]:
+    import ast
+    import importlib.util
+
+    found: set[tuple[str, str, str]] = set()
+    for module in _SDK_REJECTION_MODULES:
+        spec = importlib.util.find_spec(module)
+        assert spec is not None and spec.origin, module
+        with open(spec.origin, encoding="utf-8") as handle:
+            tree = ast.parse(handle.read())
+        for node in ast.walk(tree):
+            if not isinstance(node, ast.Call):
+                continue
+            func = node.func
+            name = (
+                func.attr
+                if isinstance(func, ast.Attribute)
+                else getattr(func, "id", None)
+            )
+            if name not in _SDK_REJECTION_CALLS:
+                continue
+            arguments = list(node.args[:1]) + [
+                keyword.value
+                for keyword in node.keywords
+                if keyword.arg in ("message", "data", "content", "error_message")
+            ]
+            for argument in arguments:
+                if not isinstance(argument, ast.Constant):
+                    found.add((module, name, ast.unparse(argument)))
+    return found
+
+
+def test_every_sdk_rejection_message_is_reviewed() -> None:
+    """Each non-literal rejection message in the SDK's server transport is
+    reviewed: request-built ones are rewritten, the rest are built from SDK
+    constants or pmcp's schema. The set is exact both ways."""
+    assert _sdk_rejection_sites() == set(_SDK_REJECTION_SITES)
````

### Patch — `tests/test_log_record_scrubber.py`

````diff
--- /dev/null
+++ b/tests/test_log_record_scrubber.py
@@ -0,0 +1,429 @@
+"""Every log record is scrubbed at creation, whoever logs it
+(Consiliency/pmcp#297, rev 4).
+
+`install_log_scrubber` wraps the `logging` record factory, so it covers
+loggers pmcp does not own and cannot enumerate -- the MCP SDK's
+`ClientSession` logs on `"client"`, outside `mcp.*` (the rev 3 board's B1)
+-- and each branch of `scrub_record` is pinned here: `exc_info`, `%`-args
+(including exceptions nested in containers and a `%(name)s` mapping), a
+`msg` that is itself an exception, and `stack_info`.
+"""
+
+from __future__ import annotations
+
+import logging
+import traceback
+from collections.abc import Iterator
+from typing import Any
+
+import pytest
+from pydantic import ValidationError
+
+from pmcp.types import McpTaskInfo
+from tests.test_argument_error_echo import _FAMILIES, _forbidden, _record_text
+
+_LOGGERS = (
+    "client",
+    "server",
+    "mcp.client.session",
+    "asyncio",
+    "uvicorn.error",
+    "httpx",
+    "anyio",
+    "third.party",
+)
+
+
+def _validation_error(s: str) -> ValidationError:
+    try:
+        McpTaskInfo.model_validate({"task_id": {"v": s}})
+    except ValidationError as error:
+        return error
+    raise AssertionError("no validation error")
+
+
+class _Capture(logging.Handler):
+    def __init__(self) -> None:
+        super().__init__(logging.DEBUG)
+        self.records: list[logging.LogRecord] = []
+
+    def emit(self, record: logging.LogRecord) -> None:
+        self.records.append(record)
+
+
+@pytest.fixture
+def capture() -> Iterator[_Capture]:
+    """A handler on every logger under test, with propagation off, so the
+    record the scrubber made is the one inspected."""
+    from pmcp.argument_errors import install_log_scrubber
+
+    install_log_scrubber()
+    handler = _Capture()
+    saved = []
+    for name in _LOGGERS:
+        logger = logging.getLogger(name)
+        saved.append((logger, logger.level, logger.propagate))
+        logger.addHandler(handler)
+        logger.setLevel(logging.DEBUG)
+        logger.propagate = False
+    yield handler
+    for logger, level, propagate in saved:
+        logger.removeHandler(handler)
+        logger.setLevel(level)
+        logger.propagate = propagate
+
+
+def _clean(record: logging.LogRecord, s: str) -> bool:
+    text = _record_text(record)
+    return not any(form in text for form in _forbidden(s))
+
+
+@pytest.mark.parametrize("name", _LOGGERS)
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_exc_info_is_scrubbed_on_any_logger(
+    capture: _Capture, name: str, family: str
+) -> None:
+    s = _FAMILIES[family][1]
+    try:
+        raise _validation_error(s)
+    except ValidationError:
+        logging.getLogger(name).warning(
+            "Failed to validate notification: %s", "m", exc_info=True
+        )
+    (record,) = capture.records
+    assert record.exc_info is None
+    assert "validation error for McpTaskInfo: $.task_id" in record.getMessage()
+    assert _clean(record, s)
+
+
+@pytest.mark.parametrize(
+    "shape",
+    ["tuple", "list", "dict", "set-free-nested", "mapping", "repr"],
+)
+def test_percent_args_are_scrubbed_however_nested(
+    capture: _Capture, shape: str
+) -> None:
+    s = _FAMILIES["alpha"][1]
+    error = _validation_error(s)
+    logger = logging.getLogger("third.party")
+    if shape == "tuple":
+        logger.error("failed: %s", error)
+    elif shape == "list":
+        logger.error("failed: %s", [1, error])
+    elif shape == "dict":
+        logger.error("failed: %s", {"k": error})
+    elif shape == "set-free-nested":
+        logger.error("failed: %s", ([{"k": (error,)}],))
+    elif shape == "mapping":
+        logger.error("failed: %(e)s", {"e": error})
+    else:
+        logger.error("failed: %r", error)
+    (record,) = capture.records
+    assert _clean(record, s), record.getMessage()
+    assert "validation error for McpTaskInfo" in record.getMessage()
+
+
+def test_msg_that_is_an_exception_is_scrubbed(capture: _Capture) -> None:
+    s = _FAMILIES["hex"][1]
+    logging.getLogger("client").error(_validation_error(s))
+    (record,) = capture.records
+    assert _clean(record, s)
+    assert "validation error for McpTaskInfo" in record.getMessage()
+
+
+def test_stack_info_carries_no_exception_text(capture: _Capture) -> None:
+    """`stack_info` renders frames and source lines, never an exception's
+    text, so the scrubber leaves it; this pins that it stays clean inside an
+    `except` block holding a validation error."""
+    s = _FAMILIES["digits"][1]
+    try:
+        raise _validation_error(s)
+    except ValidationError:
+        logging.getLogger("client").warning("inside", stack_info=True)
+    (record,) = capture.records
+    assert record.stack_info
+    assert _clean(record, s)
+
+
+def test_other_exceptions_and_values_pass_unchanged(capture: _Capture) -> None:
+    logger = logging.getLogger("third.party")
+    plain = RuntimeError("plain failure")
+    try:
+        raise plain
+    except RuntimeError:
+        logger.error("x %r %s", plain, {"k": 1}, exc_info=True)
+    (record,) = capture.records
+    assert record.args == (plain, {"k": 1})
+    assert record.exc_info is not None and record.exc_info[1] is plain
+    assert "RuntimeError('plain failure')" in record.getMessage()
+    assert "plain failure" in "".join(traceback.format_exception(*record.exc_info))
+
+
+def test_install_is_idempotent_and_wraps_the_previous_factory() -> None:
+    from pmcp.argument_errors import install_log_scrubber
+
+    original = logging.getLogRecordFactory()
+    marked: list[Any] = []
+
+    def custom(*args: Any, **kwargs: Any) -> logging.LogRecord:
+        record = original(*args, **kwargs)
+        marked.append(record)
+        return record
+
+    logging.setLogRecordFactory(custom)
+    try:
+        install_log_scrubber()
+        installed = logging.getLogRecordFactory()
+        install_log_scrubber()
+        assert logging.getLogRecordFactory() is installed
+        assert getattr(installed, "previous", None) is custom
+        logging.getLogger("third.party").warning("hello")
+        assert marked, "the previous factory was not called"
+    finally:
+        logging.setLogRecordFactory(original)
+        install_log_scrubber()
+
+
+def test_the_gateway_installs_the_scrubber(tmp_path: Any) -> None:
+    from pmcp.server import GatewayServer
+
+    original = logging.getLogRecordFactory()
+    logging.setLogRecordFactory(logging.LogRecord)
+    try:
+        policy = tmp_path / "policy.json"
+        policy.write_text("{}")
+        GatewayServer(policy_path=policy, cache_dir=tmp_path / "cache")
+        assert getattr(logging.getLogRecordFactory(), "pmcp_validation_scrubber", False)
+    finally:
+        logging.setLogRecordFactory(original)
+
+
+# --- every entry point installs it (rev 5, rev 4 board B1) --------------------
+
+_MARKER = "import logging; print(getattr(logging.getLogRecordFactory(), 'pmcp_validation_scrubber', False))"
+
+
+def _console_scripts() -> list[str]:
+    """The console scripts pmcp's distribution declares (pyproject's
+    `[project.scripts]`), as `module:attr`."""
+    from importlib.metadata import distribution
+
+    return sorted(
+        ep.value
+        for ep in distribution("pmcp").entry_points
+        if ep.group == "console_scripts"
+    )
+
+
+def _entry_points() -> dict[str, str]:
+    """A fresh interpreter per entry point: the snippet enters pmcp that way
+    and then prints whether the record factory is the scrubber."""
+    snippets = {
+        f"import {module}": f"import {module}\n{_MARKER}"
+        for module in (
+            "pmcp",
+            "pmcp.cli",
+            "pmcp.manifest.refresher",
+            "pmcp.transport.http",
+        )
+    }
+    snippets["python -m pmcp --version"] = (
+        "import runpy, sys\nsys.argv = ['pmcp', '--version']\n"
+        "try:\n    runpy.run_module('pmcp', run_name='__main__')\n"
+        "except SystemExit:\n    pass\n" + _MARKER
+    )
+    for value in _console_scripts():
+        module, _, attr = value.partition(":")
+        snippets[f"console script {value}"] = (
+            f"import importlib\ngetattr(importlib.import_module({module!r}), {attr!r})\n{_MARKER}"
+        )
+    return snippets
+
+
+@pytest.mark.parametrize("entry", sorted(_entry_points()))
+def test_every_entry_point_installs_the_scrubber(entry: str) -> None:
+    import subprocess
+    import sys
+
+    assert _console_scripts(), "pmcp declares no console script?"
+    result = subprocess.run(
+        [sys.executable, "-c", _entry_points()[entry]],
+        capture_output=True,
+        text=True,
+        timeout=120,
+    )
+    assert result.returncode == 0, result.stderr
+    assert result.stdout.strip().splitlines()[-1] == "True", (entry, result.stdout)
+
+
+_CLI_REFRESH = """
+import json, sys
+from pathlib import Path
+import pmcp.cli as cli
+import pmcp.manifest.refresher as refresher
+from pmcp.manifest.loader import ServerConfig
+
+tmp, script = Path(sys.argv[1]), sys.argv[2]
+config = ServerConfig(name="d", description="d", keywords=[], install={},
+                      command=sys.executable, args=[script, str(tmp / "case.json")])
+
+class _Manifest:
+    servers = {"d": config}
+    def get_server(self, name):
+        return self.servers.get(name)
+
+# Only the manifest lookup is replaced: the downstream is the test's.
+refresher.load_manifest = lambda *a, **k: _Manifest()
+sys.argv = ["pmcp", "refresh", "--server", "d", "--force",
+            "--cache-dir", str(tmp / "cache"), "-l", "info"]
+try:
+    cli.main()
+except SystemExit:
+    pass
+"""
+
+
+@pytest.mark.parametrize(
+    "kind",
+    [
+        "notifications/message",
+        "notifications/progress",
+        "notifications/resources/updated",
+    ],
+)
+def test_pmcp_refresh_logs_no_downstream_value(tmp_path: Any, kind: str) -> None:
+    """The operator's `pmcp refresh` (the real `pmcp.cli.main`, in a fresh
+    interpreter) against a downstream whose malformed notification the
+    SDK's `ClientSession` rejects: neither stderr nor `.pmcp/logs/gateway.log`
+    carries the value."""
+    import json
+    import os
+    import subprocess
+    import sys
+
+    from tests.test_downstream_frame_echo import _SESSION_MESSAGES, _SESSION_SCRIPT
+
+    script = tmp_path / "session_downstream.py"
+    script.write_text(_SESSION_SCRIPT)
+    driver = tmp_path / "cli_refresh.py"
+    driver.write_text(_CLI_REFRESH)
+    for family in ("hex", "alpha", "unicode"):
+        s = _FAMILIES[family][1]
+        (tmp_path / "case.json").write_text(
+            json.dumps([kind, _SESSION_MESSAGES[kind](s)])
+        )
+        (tmp_path / "case.json.in").unlink(missing_ok=True)
+        result = subprocess.run(
+            [sys.executable, str(driver), str(tmp_path), str(script)],
+            capture_output=True,
+            text=True,
+            timeout=120,
+            cwd=tmp_path,
+            env={**os.environ, "HOME": str(tmp_path)},
+        )
+        log_file = tmp_path / ".pmcp" / "logs" / "gateway.log"
+        log = log_file.read_text(errors="replace") if log_file.exists() else ""
+        for channel, text in (
+            ("stdout", result.stdout),
+            ("stderr", result.stderr),
+            ("log file", log),
+        ):
+            assert not any(form in text for form in _forbidden(s)), (
+                kind,
+                family,
+                channel,
+                text,
+            )
+        # No vacuous pass: the refresh ran, and the SDK rejected the message.
+        assert "Refreshing server: d" in result.stdout, result.stdout + result.stderr
+        assert "Failed to validate notification" in result.stderr + log, (
+            kind,
+            result.stderr,
+        )
+
+
+# --- rev 10: a JSON-RPC message renders as its structure ------------------------
+
+
+def _sdk_messages(s: str) -> list[tuple[str, object]]:
+    import mcp.types as mcp_types
+    from mcp.shared.message import SessionMessage
+
+    adapter = mcp_types.jsonrpc_message_adapter
+    frames = {
+        "response": {"jsonrpc": "2.0", "id": 7, "result": {"task": {"ttl": s}}},
+        "request": {
+            "jsonrpc": "2.0",
+            "id": 3,
+            "method": "tools/call",
+            "params": {"name": "run", "arguments": {"q": s}},
+        },
+        "notification": {
+            "jsonrpc": "2.0",
+            "method": "notifications/message",
+            "params": {"data": s},
+        },
+        "error": {
+            "jsonrpc": "2.0",
+            "id": 9,
+            "error": {"code": -1, "message": s, "data": {"k": s}},
+        },
+        "undeclared method": {"jsonrpc": "2.0", "method": f"notifications/{s}"},
+        "id carries it": {"jsonrpc": "2.0", "id": s, "result": {}},
+    }
+    from mcp.shared.message import ClientMessageMetadata
+
+    out: list[tuple[str, object]] = []
+    for label, frame in frames.items():
+        message = adapter.validate_python(frame, by_name=False)
+        out.append((label, message))
+        out.append((f"{label} in a SessionMessage", SessionMessage(message)))
+    # `Mcp-Param-*` headers carry tool-argument values (`x-mcp-header`).
+    headers = ClientMessageMetadata(
+        headers={"Mcp-Param-Q": s, "Mcp-Method": "tools/call"}
+    )
+    out.append(("metadata headers", SessionMessage(out[2][1], headers)))  # type: ignore[arg-type]
+    out.append(("metadata alone", headers))
+    return out
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_a_jsonrpc_message_renders_as_its_structure(family: str) -> None:
+    """Every rendering of an MCP SDK message object -- f-string, `%s`, `%r`,
+    a containing `SessionMessage` -- shows its structure, never its payload,
+    an undeclared method or an id's value (rev 10). A method the SDK
+    declares is shown."""
+    import pmcp  # noqa: F401 -- installs the rendering
+    from pmcp.argument_errors import describe_jsonrpc_message
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s)
+    for label, message in _sdk_messages(s):
+        for text in (f"{message}", "%s" % (message,), "%r" % (message,), repr(message)):
+            assert not any(form in text for form in forbidden), (label, text)
+            assert "<JSON-RPC " in text or label == "metadata alone", (label, text)
+    request = _sdk_messages(s)[2][1]
+    assert describe_jsonrpc_message(request) == (
+        "<JSON-RPC request: method 'tools/call', id: int, params: object (2 keys)>"
+    )
+    assert "an undeclared method" in str(_sdk_messages(s)[8][1])
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_a_dict_shaped_message_in_log_arguments_is_described(
+    caplog: pytest.LogCaptureFixture, family: str
+) -> None:
+    """A dict shaped like a JSON-RPC message, passed to any logger as a
+    `%`-argument -- alone, as the single mapping, inside a tuple, list or
+    dict -- is described, whatever logger logs it (rev 10)."""
+    caplog.set_level(logging.DEBUG)
+    s = _FAMILIES[family][1]
+    frame = {"jsonrpc": "2.0", "id": 1, "result": {"secret": s}}
+    logger = logging.getLogger("mcp.client.future_transport")
+    logger.debug("received %s", frame)
+    logger.debug("received %r and %s", frame, [frame])
+    logger.debug("received %s", {"wrapped": frame})
+    logger.debug("received %(method)s %(result)s", {**frame, "method": "ping"})
+    text = "\n".join(_record_text(r) for r in caplog.records)
+    assert not any(form in text for form in _forbidden(s)), text
+    assert text.count("<JSON-RPC response") >= 4, text
````

### Patch — `tests/test_parse_error_echo.py`

````diff
--- /dev/null
+++ b/tests/test_parse_error_echo.py
@@ -0,0 +1,1072 @@
+"""A parse error never echoes the structured text it rejected
+(Consiliency/pmcp#297; rev 6, reclassified by origin in rev 7).
+
+PyYAML renders a snippet of the input around the error mark, and a YAML tag
+makes it run a constructor whose *plain* ``ValueError`` / ``KeyError`` quotes
+the value (``k: !!int <value>``). So a failure is classified by where it
+happened, not by its type: every parse of structured text in ``src/pmcp``
+goes through ``pmcp.parsing`` (``load_yaml`` / ``load_json`` /
+``load_json_file`` / ``parse_timestamp``), which turns any exception raised
+while parsing into a value-free ``ParseError`` that chains nothing.
+
+Two halves:
+- **static**: no module in ``src/pmcp`` but ``parsing.py`` references a
+  parser -- resolved through ``import x as y`` / ``from x import y as z`` /
+  ``import datetime`` -- except the attributes named safe below (an
+  allowlist, so a parser entry point nobody listed fails), nor calls a
+  distinctive parser name on any receiver (``.fromisoformat``,
+  ``.safe_load``, ``.load_all``, ``.raw_decode``, ...), nor ``.json()`` on a
+  response; the exemptions are named, with the reason, and must all still
+  exist;
+- **dynamic**: a sentinel in each parser's rejected input -- syntax errors,
+  every value-constructing YAML tag, undefined and duplicate anchors -- at
+  every file-reading site pmcp exposes as a function, checked (in any case)
+  in the log, the raised message, the traceback the gateway would print
+  uncaught and stdout/stderr; plus the helpers themselves, the timestamp
+  sites, and a fresh interpreter for the fatal policy path.
+"""
+
+from __future__ import annotations
+
+import ast
+import json
+import logging
+import os
+import subprocess
+import sys
+from collections.abc import Callable
+from pathlib import Path
+from typing import Any
+
+import pytest
+
+from tests.test_argument_error_echo import _FAMILIES, _forbidden, _record_text
+
+_SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+
+#: The one module allowed to call a parser.
+_HELPERS = "parsing.py"
+
+#: Per resolved module (or class), the attributes that do not parse text.
+#: An allowlist: any other attribute of these -- ``safe_load``, ``load_all``,
+#: ``compose``, ``JSONDecoder``, ``fromisoformat``, ``strptime``, whatever a
+#: later release adds -- is a parser reference.
+_SAFE_ATTRIBUTES: dict[str, frozenset[str]] = {
+    "json": frozenset({"dumps", "dump", "JSONDecodeError", "JSONEncoder"}),
+    "yaml": frozenset(
+        {
+            "YAMLError",
+            "MarkedYAMLError",
+            "dump",
+            "dump_all",
+            "safe_dump",
+            "safe_dump_all",
+            "Dumper",
+            "SafeDumper",
+        }
+    ),
+    "tomllib": frozenset({"TOMLDecodeError"}),
+    "datetime.datetime": frozenset(
+        {"now", "utcnow", "fromtimestamp", "utcfromtimestamp", "min", "max"}
+    ),
+    "datetime.date": frozenset({"today", "fromtimestamp", "min", "max"}),
+}
+
+#: Parser entry points recognisable by name on ANY receiver -- a variable, an
+#: attribute, a class the resolver cannot follow.
+_PARSER_NAMES = frozenset(
+    {
+        "fromisoformat",
+        "strptime",
+        "safe_load",
+        "safe_load_all",
+        "load_all",
+        "full_load",
+        "full_load_all",
+        "unsafe_load",
+        "unsafe_load_all",
+        "raw_decode",
+        "JSONDecoder",
+    }
+)
+
+#: Named exemptions, ``file::function`` -> reason. Each must still exist.
+_EXEMPT: dict[str, str] = {
+    # python-dotenv never raises on bad input; it logs "python-dotenv could
+    # not parse statement starting at line N" -- the line number only
+    # (measured on python-dotenv 1.x).
+    "env_store.py::read_env_file": "dotenv_values",
+    "cli.py::load_startup_env": "load_dotenv",
+    "tools/handlers.py::_check_api_key_available": "load_dotenv",
+    # A response object's `.json()`: JSON has no constructors, so its
+    # failures are JSONDecodeError (fixed vocabulary; the `doc` attribute is
+    # rendered by exception_text as `could not parse JSON ...`),
+    # RecursionError, or aiohttp's ContentTypeError (the server's MIME type).
+    # Every one is caught and rendered via exception_text or swallowed.
+    "manifest/version_checker.py::get_npm_version": ".json() (aiohttp)",
+    "manifest/version_checker.py::get_pypi_version": ".json() (aiohttp)",
+    "manifest/version_checker.py::get_cargo_version": ".json() (aiohttp)",
+    "manifest/version_checker.py::get_docker_version": ".json() (aiohttp)",
+    "cli.py::_probe_http_health": ".json() (httpx), error swallowed",
+}
+
+_DOTENV = frozenset({"dotenv_values", "load_dotenv"})
+
+
+def _aliases(tree: ast.AST) -> dict[str, str]:
+    """Local name -> the dotted name it is bound to, for every import in the
+    module (at any depth: a function-local ``import yaml`` counts)."""
+    bound: dict[str, str] = {}
+    for node in ast.walk(tree):
+        if isinstance(node, ast.Import):
+            for alias in node.names:
+                if alias.asname:
+                    bound[alias.asname] = alias.name
+                else:
+                    head = alias.name.split(".")[0]
+                    bound[head] = head
+        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
+            for alias in node.names:
+                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
+    return bound
+
+
+def _qualified(node: ast.AST, bound: dict[str, str]) -> str | None:
+    parts: list[str] = []
+    while isinstance(node, ast.Attribute):
+        parts.append(node.attr)
+        node = node.value
+    if not isinstance(node, ast.Name) or node.id not in bound:
+        return None
+    return ".".join([bound[node.id], *reversed(parts)])
+
+
+#: The parser packages: an attribute of any of their SUBMODULES
+#: (``yaml.loader.SafeLoader``, ``yaml.cyaml.CSafeLoader``,
+#: ``yaml.constructor.SafeConstructor``, ``json.decoder.JSONDecoder``) is a
+#: parser reference unless its name is safe in the package itself (rev 8,
+#: round-7 claude (3)).
+_PARSER_PACKAGES = ("json", "yaml", "tomllib")
+
+
+def _violation(qualified: str) -> bool:
+    owner, _, attr = qualified.rpartition(".")
+    if owner in _SAFE_ATTRIBUTES:
+        return attr not in _SAFE_ATTRIBUTES[owner]
+    package = owner.split(".")[0]
+    if package in _PARSER_PACKAGES and owner != package:
+        return attr not in _SAFE_ATTRIBUTES[package]
+    return False
+
+
+def _parser_references(source: str) -> list[tuple[int, str]]:
+    """``(line, what)`` for every parser reference in ``source``."""
+    tree = ast.parse(source)
+    bound = _aliases(tree)
+    found: list[tuple[int, str]] = []
+    for node in ast.walk(tree):
+        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
+            for alias in node.names:
+                if _violation(f"{node.module}.{alias.name}"):
+                    found.append(
+                        (node.lineno, f"from {node.module} import {alias.name}")
+                    )
+        elif isinstance(node, ast.Attribute):
+            qualified = _qualified(node, bound)
+            if qualified is not None and _violation(qualified):
+                found.append((node.lineno, qualified))
+            elif node.attr in _PARSER_NAMES:
+                found.append((node.lineno, f".{node.attr}"))
+            elif (
+                isinstance(node.value, ast.Attribute)
+                and node.value.attr in _PARSER_PACKAGES
+                and node.attr not in _SAFE_ATTRIBUTES[node.value.attr]
+            ):
+                # A parser package reached through another module's attribute
+                # (``policy.yaml.load``), which the resolver cannot follow.
+                found.append((node.lineno, f".{node.value.attr}.{node.attr}"))
+        elif isinstance(node, ast.Call):
+            func = node.func
+            if isinstance(func, ast.Attribute) and func.attr == "json":
+                # With or without arguments (``resp.json(content_type=None)``).
+                found.append((node.lineno, ".json()"))
+            elif isinstance(func, ast.Name) and (
+                func.id in _DOTENV or bound.get(func.id, "").startswith("dotenv.")
+            ):
+                found.append((node.lineno, func.id))
+        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
+            qualified = bound.get(node.id)
+            if qualified is not None and _violation(qualified):
+                found.append((node.lineno, qualified))
+    return sorted(set(found))
+
+
+def _owner(tree: ast.AST, line: int) -> str:
+    functions = [
+        n
+        for n in ast.walk(tree)
+        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
+    ]
+    owner = min(
+        (f for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)),
+        key=lambda f: (f.end_lineno or f.lineno) - f.lineno,
+        default=None,
+    )
+    return owner.name if owner else "<module>"
+
+
+def _parse_sites() -> dict[str, list[str]]:
+    """``file::function`` -> the parser references in it, outside the helpers."""
+    found: dict[str, list[str]] = {}
+    for path in sorted(_SRC.rglob("*.py")):
+        rel = path.relative_to(_SRC).as_posix()
+        if rel == _HELPERS or "baml_client" in path.parts:
+            continue
+        source = path.read_text()
+        tree = ast.parse(source)
+        for line, what in _parser_references(source):
+            found.setdefault(f"{rel}::{_owner(tree, line)}", []).append(
+                f"{line}: {what}"
+            )
+    return found
+
+
+def test_no_parser_call_outside_the_helpers() -> None:
+    sites = _parse_sites()
+    unexempt = {k: v for k, v in sites.items() if k not in _EXEMPT}
+    assert not unexempt, unexempt
+    # Every exemption still names a real call: a stale one would hide the
+    # next parse added to that function.
+    assert set(_EXEMPT) == set(sites), sorted(set(_EXEMPT) ^ set(sites))
+
+
+@pytest.mark.parametrize(
+    "snippet",
+    [
+        "import yaml\nyaml.safe_load(t)",
+        "import yaml as y\ny.safe_load(t)",
+        "from yaml import safe_load\nsafe_load(t)",
+        "from yaml import safe_load as sl\nsl(t)",
+        "import yaml\nyaml.load_all(t)",
+        "import yaml\nyaml.compose(t)",
+        "import yaml\nf = yaml.full_load",
+        "import json\njson.loads(t)",
+        "import json as j\nj.load(f)",
+        "from json import loads\nloads(t)",
+        "import json\njson.JSONDecoder().decode(t)",
+        "from json import JSONDecoder\nJSONDecoder().raw_decode(t)",
+        "import tomllib\ntomllib.loads(t)",
+        "from datetime import datetime\ndatetime.fromisoformat(t)",
+        "from datetime import datetime as dt\ndt.fromisoformat(t)",
+        "import datetime\ndatetime.datetime.fromisoformat(t)",
+        "import datetime\ndatetime.date.fromisoformat(t)",
+        "from datetime import datetime\ndatetime.strptime(t, f)",
+        "cls.fromisoformat(t)",
+        "self._yaml.safe_load(t)",
+        "response.json()",
+        "await resp.json(content_type=None)",
+        "from yaml.loader import SafeLoader\nSafeLoader(t).get_single_data()",
+        "from yaml.cyaml import CSafeLoader\nCSafeLoader(t).get_single_data()",
+        "from yaml.constructor import SafeConstructor\nSafeConstructor()",
+        "from json.decoder import JSONDecoder\nJSONDecoder().decode(t)",
+        "import yaml.loader\nyaml.loader.SafeLoader(t)",
+        "policy.yaml.load(t, Loader=policy.yaml.SafeLoader)",
+        "from dotenv import load_dotenv as ld\nld(p)",
+        "def f():\n    import yaml\n    return yaml.safe_load(t)",
+    ],
+)
+def test_the_parse_site_check_sees_every_spelling(snippet: str) -> None:
+    """No vacuous pass: each spelling of a parser call is found."""
+    assert _parser_references(snippet), snippet
+
+
+@pytest.mark.parametrize(
+    "snippet",
+    [
+        "import json\njson.dumps(v)",
+        "import yaml\nyaml.safe_dump(v)",
+        "from datetime import datetime, timezone\ndatetime.now(timezone.utc)",
+        "import json\nexcept_types = (json.JSONDecodeError, ValueError)",
+        "from pmcp.parsing import load_yaml\nload_yaml(t, source='x')",
+        "from json.decoder import JSONDecodeError\nexcept_types = (JSONDecodeError,)",
+        "from yaml.error import MarkedYAMLError\nisinstance(e, MarkedYAMLError)",
+        "self.yaml.safe_dump(v)",
+    ],
+)
+def test_the_parse_site_check_passes_non_parsers(snippet: str) -> None:
+    assert not _parser_references(snippet), snippet
+
+
+# --- dynamic ---------------------------------------------------------------------
+
+
+def _yaml_bad(s: str) -> str:
+    """Four ways PyYAML rejects input, each quoting it in its message."""
+    return f"servers: [{s}}}\n"
+
+
+def _yaml_bads(s: str) -> list[str]:
+    """Every way PyYAML rejects input that we know quotes it -- syntax, and
+    each tag whose constructor raises a *plain* exception (rev 7, B1)."""
+    return [
+        f"servers: [{s}}}\n",  # ParserError (flow sequence)
+        f"a: b\n  c: {s}: d\n",  # ScannerError (mapping values)
+        f'k: "{s}\n',  # ScannerError (unterminated quote)
+        f"k: !{s} v\n",  # ConstructorError (the tag is the input)
+        f"k: !!int {s}\n",  # ValueError: invalid literal for int() ... '<s>'
+        f"k: !!int 0x{s}\n",  # ValueError (base 16)
+        f"k: !!float {s}\n",  # ValueError: could not convert ... '<s lowered>'
+        f"k: !!bool {s}\n",  # KeyError: '<s lowered>'
+        f"k: !!timestamp {s}\n",  # AttributeError
+        f"k: !!binary {s}!\n",  # ConstructorError (base64)
+        f"k: !!python/object:{s} {{}}\n",  # ConstructorError (the tag)
+        f"k: !!python/name:{s} ''\n",  # ConstructorError (the tag)
+        f"a: &x1 [1]\nb: *{s}\n",  # ComposerError: undefined alias '<s>'
+        f"a: &{s} 1\nb: &{s} 2\n",  # ComposerError: duplicate anchor '<s>'
+        f"k: !!set [{s}]\n",  # ConstructorError (node kind)
+        f"k: {{<<: {s}}}\n",  # ConstructorError (merge)
+        # Accepted, so filtered out by `_rejects` (measured: last key wins):
+        f"{s}: 1\n{s}: 2\n",  # duplicate keys
+    ]
+
+
+def _json_bads(s: str) -> list[str]:
+    return [f'{{"k": {s}}}', f'{{"{s}": 1,}}', f"[1, 2, {s}"]
+
+
+def _run(fn: Callable[[], Any]) -> tuple[str, str]:
+    """(the raised error as pmcp renders it, the traceback the gateway would
+    print uncaught) for `fn`. pmcp renders an exception only through
+    `exception_text` -- the static guard pins that at every sink -- so that,
+    not the library's own `str()`, is what reaches a caller; a message pmcp
+    builds itself (`ValueError(f"... {exception_text(e)}")`) is covered by
+    the same call."""
+    try:
+        from pmcp.argument_errors import exception_text, safe_traceback_text
+    except ImportError:  # a tree without the renderer (main, for the red run)
+        import traceback
+
+        def exception_text(error: BaseException) -> str:
+            return str(error)
+
+        def safe_traceback_text(error: BaseException) -> str:
+            return "".join(
+                traceback.format_exception(type(error), error, error.__traceback__)
+            )
+
+    try:
+        fn()
+    except BaseException as error:  # noqa: BLE001 -- inspected
+        return exception_text(error), safe_traceback_text(error)
+    return "", ""
+
+
+def _rejects(parser: str, text: str) -> bool:
+    """Whether the raw parser rejects ``text`` -- with ANY exception: a tag's
+    constructor raises ``ValueError``/``KeyError``/``AttributeError``."""
+    import yaml
+
+    try:
+        (yaml.safe_load if parser == "yaml" else json.loads)(text)
+    except Exception:  # noqa: BLE001 -- any rejection counts
+        return True
+    return False
+
+
+def _forbidden_any_case(s: str) -> set[str]:
+    """``_forbidden`` of the sentinel in each case: PyYAML's float and bool
+    constructors quote the value lowercased."""
+    return _forbidden(s) | _forbidden(s.lower()) | _forbidden(s.upper())
+
+
+def _policy(tmp: Path, content: str, suffix: str, fatal: bool) -> Callable[[], Any]:
+    from pmcp.policy.policy import PolicyManager
+
+    path = tmp / f"policy{suffix}"
+    path.write_text(content)
+    return lambda: PolicyManager()._parse_policy(content, path, fatal=fatal)
+
+
+def _write(tmp: Path, name: str, content: str) -> Path:
+    path = tmp / name
+    path.write_text(content)
+    return path
+
+
+def _cases() -> list[tuple[str, str, Callable[[Path, str], Callable[[], Any]]]]:
+    """(label, parser, builder(tmp, bad text) -> call)."""
+    from pmcp import package_approvals, trust_store
+    from pmcp.config import guidance
+    from pmcp.config import loader as config_loader
+    from pmcp.manifest import loader as manifest_loader
+    from pmcp.manifest import refresher, registry
+    from pmcp.manifest.code_patterns_loader import CodePatternsLoader
+    from pmcp.templates.code_snippets_loader import CodeSnippetsLoader
+
+    return [
+        ("policy yaml, warn", "yaml", lambda t, c: _policy(t, c, ".yaml", False)),
+        ("policy yaml, fatal", "yaml", lambda t, c: _policy(t, c, ".yaml", True)),
+        ("policy json, warn", "json", lambda t, c: _policy(t, c, ".json", False)),
+        ("policy json, fatal", "json", lambda t, c: _policy(t, c, ".json", True)),
+        (
+            "manifest overlay",
+            "yaml",
+            lambda t, c: lambda: manifest_loader._parse_overlay_document(
+                t / "overlay.yaml", c.encode()
+            ),
+        ),
+        (
+            "manifest",
+            "yaml",
+            lambda t, c: lambda: manifest_loader.load_manifest(_write(t, "m.yaml", c)),
+        ),
+        (
+            "config file",
+            "json",
+            lambda t, c: lambda: config_loader.parse_config_bytes(
+                c.encode(), t / ".mcp.json"
+            ),
+        ),
+        (
+            "config object",
+            "json",
+            lambda t, c: lambda: _returned(
+                config_loader._config_object_from_bytes(c.encode())
+            ),
+        ),
+        (
+            "guidance",
+            "yaml",
+            lambda t, c: lambda: guidance.load_guidance_config(_write(t, "g.yaml", c)),
+        ),
+        (
+            "code patterns",
+            "yaml",
+            lambda t, c: lambda: CodePatternsLoader(_write(t, "p.yaml", c)),
+        ),
+        (
+            "code snippets",
+            "yaml",
+            lambda t, c: lambda: CodeSnippetsLoader(_write(t, "s.yaml", c)),
+        ),
+        (
+            "descriptions cache",
+            "yaml",
+            lambda t, c: lambda: refresher.load_descriptions_cache(
+                _write(t, "d.yaml", c)
+            ),
+        ),
+        (
+            "registry cache",
+            "json",
+            lambda t, c: lambda: registry.load_registry_cache(_write(t, "r.json", c)),
+        ),
+        (
+            "trust store",
+            "json",
+            lambda t, c: lambda: trust_store._read_store(_write(t, "trust.json", c)),
+        ),
+        (
+            "package approvals",
+            "json",
+            lambda t, c: lambda: package_approvals._read_store(_write(t, "a.json", c)),
+        ),
+    ]
+
+
+def _returned(value: Any) -> None:
+    """A site that returns its diagnostic instead of raising: make the
+    returned text the 'raised' text."""
+    raise RuntimeError(repr(value))
+
+
+@pytest.mark.parametrize("label", [c[0] for c in _cases()])
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_no_parse_site_echoes_its_input(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    label: str,
+    family: str,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    (case,) = [c for c in _cases() if c[0] == label]
+    _, parser, build = case
+    s = _FAMILIES[family][1]
+    # Only inputs the parser rejects: a digits-only sentinel makes `{"k": 123}`
+    # valid JSON, which is data, not a parse error.
+    bads = [
+        bad
+        for bad in (_yaml_bads(s) if parser == "yaml" else _json_bads(s))
+        if _rejects(parser, bad)
+    ]
+    assert len(bads) >= (12 if parser == "yaml" else 2), (label, family, bads)
+    forbidden = _forbidden_any_case(s)
+    silent: list[str] = []
+    for bad in bads:
+        start = len(caplog.records)
+        capfd.readouterr()
+        raised, shown = _run(build(tmp_path, bad))
+        streams = capfd.readouterr()
+        logged = "\n".join(_record_text(r) for r in caplog.records[start:])
+        for channel, text in (
+            ("raised", raised),
+            ("traceback", shown),
+            ("log", logged),
+            ("stdout/stderr", streams.out + streams.err),
+        ):
+            assert not any(form in text for form in forbidden), (
+                label,
+                bad,
+                channel,
+                text,
+            )
+        everything = raised + shown + logged + streams.out + streams.err
+        if "could not parse" not in everything and (
+            raised or shown or streams.out.strip() or streams.err.strip()
+        ):
+            silent.append(bad)
+    # No vacuous pass, checked after every input's leak check: for each
+    # rejected input pmcp said it could not parse, or said nothing at all (a
+    # site that falls back silently, e.g. a cache miss). PyYAML reading a file
+    # omits the snippet but a constructor error still names the input's tag,
+    # so the leak half needs every input, not just the first.
+    assert not silent, (label, silent)
+
+
+def test_an_uncaught_fatal_policy_error_prints_no_input(tmp_path: Path) -> None:
+    """An explicit policy that does not parse is fatal: the gateway exits with
+    the error uncaught, and the interpreter prints it. Its chain holds the
+    YAML error, whose text quotes the file; the excepthook `import pmcp`
+    installs prints it structurally."""
+    s = _FAMILIES["token"][1]
+    policy = tmp_path / "policy.yaml"
+    policy.write_text(_yaml_bad(s))
+    result = subprocess.run(
+        [
+            sys.executable,
+            "-c",
+            "import sys; from pmcp.policy.policy import PolicyManager; "
+            "PolicyManager(policy_path=__import__('pathlib').Path(sys.argv[1]))",
+            str(policy),
+        ],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        env={**os.environ, "HOME": str(tmp_path)},
+        cwd=tmp_path,
+    )
+    assert result.returncode != 0
+    assert "could not parse YAML policy file at line 1" in result.stderr, result.stderr
+    assert "Traceback (most recent call last)" in result.stderr
+    assert not any(form in result.stderr + result.stdout for form in _forbidden(s)), (
+        result.stderr
+    )
+
+
+def test_parse_text_names_only_format_class_and_position() -> None:
+    import yaml
+
+    from pmcp.argument_errors import exception_text
+
+    s = _FAMILIES["alpha"][1]
+    for bad, expected in (
+        (f"servers: [{s}}}", "could not parse YAML (ParserError) at line 1, column "),
+        (f"k: !{s} v", "could not parse YAML (ConstructorError) at line 1, column "),
+    ):
+        try:
+            yaml.safe_load(bad)
+        except yaml.YAMLError as error:
+            # PyYAML's snippet truncates long lines; any window is the leak.
+            assert any(form in str(error) for form in _forbidden(s)), str(error)
+            assert exception_text(error).startswith(expected), exception_text(error)
+    try:
+        json.loads(f'{{"k": {s}}}')
+    except json.JSONDecodeError as error:
+        assert (
+            exception_text(error)
+            == "could not parse JSON (JSONDecodeError) at line 1, column 7"
+        )
+
+
+def test_a_failing_log_handler_prints_no_input(
+    capfd: pytest.CaptureFixture[str],
+) -> None:
+    """A handler whose `emit` raises while an `except` block for a parse
+    error is active: `logging.Handler.handleError` prints the active chain to
+    stderr. pmcp's wrapper prints it structurally."""
+    import yaml
+
+    import pmcp  # noqa: F401 -- installs the scrubber, excepthook and handleError
+
+    class _Broken(logging.Handler):
+        def emit(self, record: logging.LogRecord) -> None:
+            # As every stdlib handler does: a failing emit calls handleError.
+            try:
+                raise OSError("disk gone")
+            except Exception:
+                self.handleError(record)
+
+    s = _FAMILIES["hex"][1]
+    logger = logging.getLogger("pmcp.test.broken")
+    handler = _Broken()
+    logger.addHandler(handler)
+    try:
+        capfd.readouterr()
+        try:
+            yaml.safe_load(f"servers: [{s}}}")
+        except yaml.YAMLError:
+            logger.error("could not load")
+        err = capfd.readouterr().err
+    finally:
+        logger.removeHandler(handler)
+    assert "--- Logging error ---" in err
+    assert "could not parse YAML (ParserError)" in err, err
+    # The handler's own error was raised while the parse error was handled:
+    # its message is withheld, its class kept (rev 18).
+    assert "\nOSError: could not parse YAML (ParserError)" in err, err
+    assert "OSError: disk gone" not in err, err  # the frame's source line is code
+    assert not any(form in err for form in _forbidden(s)), err
+
+
+# --- the helpers themselves (rev 7) ---------------------------------------------
+
+
+def _helper_yaml_inputs(s: str) -> list[str]:
+    return [
+        *(bad for bad in _yaml_bads(s) if _rejects("yaml", bad)),
+        "[" * 3000 + s + "]" * 3000,  # RecursionError
+    ]
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_load_yaml_classifies_every_failure_by_origin(family: str) -> None:
+    import yaml
+
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.parsing import ParseError, YAMLParseError, load_yaml
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden_any_case(s)
+    causes: set[str] = set()
+    for bad in _helper_yaml_inputs(s):
+        with pytest.raises(YAMLParseError) as caught:
+            load_yaml(bad, source="policy file")
+        error = caught.value
+        # It chains nothing, so no traceback printer can reach the original.
+        assert error.__cause__ is None and error.__context__ is None, bad
+        assert isinstance(error, (yaml.YAMLError, ValueError, ParseError))
+        for text in (
+            str(error),
+            repr(error),
+            exception_text(error),
+            safe_traceback_text(error),
+            repr(error.args),
+        ):
+            assert not any(form in text for form in forbidden), (bad, text)
+        assert str(error).startswith("could not parse YAML policy file"), str(error)
+        causes.add(error.cause or "")
+    # No vacuous pass: the plain exceptions the board found are among them.
+    plain = {"ValueError", "KeyError", "AttributeError", "RecursionError"}
+    if family == "digits":
+        plain.discard("ValueError")  # `!!int <digits>` is a valid int
+    assert plain <= causes, causes
+    assert {"ParserError", "ScannerError", "ConstructorError"} <= causes, causes
+    if family in ("alpha", "hex", "token"):
+        # A name with no space or leading digit is a valid anchor name.
+        assert "ComposerError" in causes, causes
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_load_json_classifies_every_failure_by_origin(family: str) -> None:
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.parsing import JSONParseError, load_json
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden_any_case(s)
+    inputs: list[Any] = [
+        *(b for b in _json_bads(s) if _rejects("json", b)),
+        "[" * 100000 + json.dumps(s) + "]" * 100000,  # RecursionError
+        s.encode("utf-8") + b"\xff",  # UnicodeDecodeError (encoding=)
+    ]
+    causes: set[str] = set()
+    for bad in inputs:
+        with pytest.raises(JSONParseError) as caught:
+            load_json(bad, source="registry response", encoding="utf-8")
+        error = caught.value
+        assert error.__cause__ is None and error.__context__ is None
+        assert isinstance(error, json.JSONDecodeError) and error.doc == ""
+        for text in (str(error), exception_text(error), safe_traceback_text(error)):
+            assert not any(form in text for form in forbidden), (bad, text)
+        causes.add(error.cause or "")
+    assert {"RecursionError", "UnicodeDecodeError"} <= causes, causes
+
+
+def test_duplicate_yaml_keys_are_data_not_a_parse_error() -> None:
+    """Measured, so the sweep's `_rejects` filter drops them: PyYAML's
+    safe_load keeps the last value."""
+    from pmcp.parsing import load_yaml
+
+    assert load_yaml("a: 1\na: 2\n", source="x") == {"a": 2}
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_no_timestamp_site_echoes_its_input(family: str) -> None:
+    """``datetime.fromisoformat`` quotes a bad timestamp (rev 7, N1)."""
+    from pmcp import package_approvals, trust_store
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.types import McpTaskInfo
+
+    s = "2026-13-99T" + _FAMILIES[family][1]
+    forbidden = _forbidden_any_case(_FAMILIES[family][1])
+    calls: list[Callable[[], Any]] = [
+        lambda: trust_store._decode(
+            {
+                "absolute_path": "/x",
+                "content_sha256": "0" * 64,
+                "scope": "project",
+                "decision": "approved",
+                "recorded_at": s,
+            }
+        ),
+        lambda: package_approvals._decode(
+            {
+                "registry": "npm",
+                "name": "left-pad",
+                "resolved_version": "1.3.0",
+                "integrity": None,
+                "decision": "approved",
+                "recorded_at": s,
+            }
+        ),
+    ]
+    # Since Consiliency/pmcp#298 a task timestamp pmcp cannot use is dropped,
+    # not raised: it is `None`, named in `unusable_fields`, and not kept.
+    task = McpTaskInfo(task_id="t", created_at=s)
+    assert task.created_at is None and task.unusable_fields == ["created_at"]
+    assert not any(form in task.model_dump_json() for form in forbidden)
+    for call in calls:
+        with pytest.raises(Exception) as caught:
+            call()
+        error = caught.value
+        for text in (exception_text(error), safe_traceback_text(error)):
+            assert not any(form in text for form in forbidden), text
+    try:
+        calls[0]()
+    except Exception as error:  # noqa: BLE001 -- inspected
+        assert "could not parse timestamp trust store record" in str(error)
+
+
+def test_parse_error_is_rendered_as_its_own_text() -> None:
+    """A ParseError subclasses the parser's type but is not a parser's own
+    error: exception_text keeps its source label and safe_exc_info keeps the
+    traceback."""
+    from pmcp.argument_errors import exception_text, safe_exc_info
+    from pmcp.parsing import load_yaml
+
+    try:
+        load_yaml("k: !!int nope\n", source="guidance config")
+    except Exception as error:  # noqa: BLE001 -- inspected
+        assert (
+            exception_text(error) == "could not parse YAML guidance config (ValueError)"
+        )
+        assert safe_exc_info(error) is error
+    else:  # pragma: no cover
+        raise AssertionError("did not raise")
+
+
+def test_a_thread_prints_no_input(tmp_path: Path) -> None:
+    """``threading.excepthook`` prints an uncaught thread exception, chain and
+    all (rev 7, N3): the wrapper `import pmcp` installs prints it
+    structurally, under the interpreter's header and qualified class name;
+    with no stderr it prints nothing, like the default."""
+    s = _FAMILIES["token"][1]
+    script = (
+        "import sys, threading, yaml, pmcp\n"
+        "def run():\n"
+        "    try:\n"
+        "        yaml.safe_load(sys.argv[1])\n"
+        "    except yaml.YAMLError as e:\n"
+        "        raise RuntimeError('boom') from e\n"
+        "t = threading.Thread(target=run, name='parser'); t.start(); t.join()\n"
+        "if len(sys.argv) > 2:\n"
+        "    sys.stderr = None\n"
+        "    t = threading.Thread(target=run); t.start(); t.join()\n"
+    )
+    result = subprocess.run(
+        [sys.executable, "-c", script, f"servers: [{s}}}"],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert "Exception in thread parser:" in result.stderr, result.stderr
+    assert "yaml.parser.ParserError: could not parse YAML (ParserError)" in (
+        result.stderr
+    ), result.stderr
+    # A wrapper of a parse error prints what it chains, never its own
+    # message (rev 18).
+    assert (
+        "RuntimeError: could not parse YAML (ParserError) at line 1, column"
+        in result.stderr
+    ), result.stderr
+    assert "boom" not in result.stderr, result.stderr
+    assert not any(form in result.stderr for form in _forbidden(s)), result.stderr
+    silent = subprocess.run(
+        [sys.executable, "-c", script, f"servers: [{s}}}", "no-stderr"],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert silent.returncode == 0, silent.stderr
+    assert silent.stderr.count("Exception in thread") == 1, silent.stderr
+
+
+@pytest.mark.parametrize("origin", ["yaml", "pydantic"])
+def test_an_uncaught_chain_prints_no_input(tmp_path: Path, origin: str) -> None:
+    """``sys.excepthook`` prints an uncaught exception's chain. Since rev 7 a
+    pmcp parse failure chains nothing, so this pins the wrapper on the
+    chains that still can hold a value: a parser's own error raised outside
+    ``pmcp.parsing`` and a pydantic ``ValidationError`` (the rev 7 mutation
+    run found M34, "no excepthook", surviving without it)."""
+    s = _FAMILIES["token"][1]
+    raise_inner = {
+        "yaml": "    import yaml\n    yaml.safe_load(sys.argv[1])\n",
+        "pydantic": (
+            "    import pydantic\n"
+            "    pydantic.TypeAdapter(int).validate_python(sys.argv[1])\n"
+        ),
+    }[origin]
+    script = (
+        "import sys, pmcp\n"
+        "try:\n" + raise_inner + "except Exception as e:\n"
+        "    raise RuntimeError('boom') from e\n"
+    )
+    arg = f"servers: [{s}}}" if origin == "yaml" else s
+    result = subprocess.run(
+        [sys.executable, "-c", script, arg],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert result.returncode == 1, result.stderr
+    assert "Traceback (most recent call last)" in result.stderr, result.stderr
+    expected = {
+        "yaml": "yaml.parser.ParserError: could not parse YAML (ParserError)",
+        "pydantic": "pydantic_core._pydantic_core.ValidationError: 1 validation error",
+    }[origin]
+    assert expected in result.stderr, result.stderr
+    # The wrapper's line is its class and what it chains, once (rev 18).
+    wrapper = {
+        "yaml": "\nRuntimeError: could not parse YAML (ParserError) at line 1, column",
+        "pydantic": "\nRuntimeError: 1 validation error for int: $: must be an integer\n",
+    }[origin]
+    assert wrapper in result.stderr, result.stderr
+    assert "boom" not in result.stderr, result.stderr
+    assert "RuntimeError: RuntimeError" not in result.stderr, result.stderr
+    assert not any(form in result.stderr for form in _forbidden(s)), result.stderr
+
+
+def test_an_uncaught_error_with_no_stderr_prints_nothing(tmp_path: Path) -> None:
+    """The default excepthook is silent when ``sys.stderr`` is None; so is
+    pmcp's wrapper (rev 7 nit), instead of raising AttributeError."""
+    script = (
+        "import sys, yaml, pmcp\n"
+        "try:\n"
+        "    yaml.safe_load('servers: [x}')\n"
+        "except yaml.YAMLError as e:\n"
+        "    err = RuntimeError('boom')\n"
+        "    err.__cause__ = e\n"
+        "sys.stderr = None\n"
+        "sys.excepthook(RuntimeError, err, None)\n"
+        "sys.stdout.write('survived')\n"
+    )
+    result = subprocess.run(
+        [sys.executable, "-c", script],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert result.returncode == 0, result.stderr
+    assert result.stdout == "survived"
+    assert result.stderr == ""
+
+
+def test_an_unparseable_auth_url_port_is_not_chained_into_a_traceback() -> None:
+    """Consiliency/pmcp#348's URL rule refuses with fixed registry messages;
+    the one refusal built on a parser error (urllib's, which quotes the
+    port) is raised `from None`, so no traceback carries the value."""
+    import traceback
+
+    from pmcp.auth import check_auth_config, sanitize_public_auth_url
+
+    s = "zqcanaryzq"
+    for call in (
+        lambda: sanitize_public_auth_url(f"https://a.example.com:{s}/"),
+        lambda: check_auth_config(jwks_url=f"https://a.example.com:{s}/"),
+        lambda: check_auth_config(metadata_url=f"https://a.example.com:{s}/"),
+    ):
+        with pytest.raises(ValueError) as caught:
+            call()
+        assert s not in "".join(traceback.format_exception(caught.value))
+
+
+# --- rev 19: pmcp's own refusals keep their words (round-17 claude F001) ----
+#
+# Since rev 18 a wrapper whose chain holds a validation or parse error shows
+# only that error's description. pmcp's own refusals are built from
+# `exception_text` and raised after their handler, so they chain nothing and
+# are shown whole: the file, the description and, for a discovered policy,
+# the fail-closed refusal. Each case runs the real entry point.
+
+_R19 = _FAMILIES["alpha"][0]
+_R19_SCHEMA = (
+    "1 validation error for GatewayPolicy: $.tools.allowlist: must be an array"
+)
+
+
+def _r19_case(case: str, root: Path) -> tuple[list[str], str]:
+    """Set up `case` under `root`; return the CLI arguments and the exact
+    stderr line the operator must see."""
+    home = root / "home"
+    s = _R19
+    if case == "user-policy-yaml-schema":
+        path = home / ".claude" / "gateway-policy.yaml"
+        path.write_text(f"tools:\n  allowlist: {s}\n")
+        return [], (
+            f"Fatal error: Invalid policy file {path}: {_R19_SCHEMA}. "
+            "Refusing to start rather than fall back to an unrestricted gateway."
+        )
+    if case == "user-policy-json-schema":
+        path = home / ".claude" / "gateway-policy.json"
+        path.write_text(json.dumps({"tools": {"allowlist": s}}))
+        return [], (
+            f"Fatal error: Invalid policy file {path}: {_R19_SCHEMA}. "
+            "Refusing to start rather than fall back to an unrestricted gateway."
+        )
+    if case == "user-policy-root-list":
+        path = home / ".claude" / "gateway-policy.yaml"
+        path.write_text(f"- {s}\n")
+        return [], (
+            f"Fatal error: Invalid policy file {path}: policy root must be an "
+            "object, got list. Refusing to start rather than fall back to an "
+            "unrestricted gateway."
+        )
+    if case == "explicit-policy-yaml-schema":
+        path = root / "p.yaml"
+        path.write_text(f"tools:\n  allowlist: {s}\n")
+        return ["--policy", str(path)], (
+            f"Fatal error: Failed to load explicit policy {path}: {_R19_SCHEMA}"
+        )
+    if case == "explicit-policy-json-schema":
+        path = root / "p.json"
+        path.write_text(json.dumps({"tools": {"allowlist": s}}))
+        return ["--policy", str(path)], (
+            f"Fatal error: Failed to load explicit policy {path}: {_R19_SCHEMA}"
+        )
+    if case == "explicit-policy-yaml-parse":
+        path = root / "p.yaml"
+        path.write_text(f"tools: [{s}}}\n")
+        return ["--policy", str(path)], (
+            f"Fatal error: Failed to load explicit policy {path}: could not "
+            "parse YAML policy file at line 1, column "
+        )
+    if case == "explicit-policy-missing":
+        path = root / "absent.yaml"
+        return ["--policy", str(path)], (
+            f"Fatal error: Failed to load explicit policy {path}: [Errno 2] "
+        )
+    if case == "trust-store-parse":
+        path = home / ".config" / "pmcp" / "trust.json"
+        path.parent.mkdir(parents=True)
+        path.write_text('{"records": [' + s + "}")
+        path.chmod(0o600)
+        return ["trust", "list"], (
+            f"Error: Cannot parse trust store {path.resolve()}: could not parse "
+            "JSON trust store at line 1, column 14 (JSONDecodeError)"
+        )
+    assert case == "auth-jwks-url", case
+    return [
+        "--transport",
+        "http",
+        "--auth-mode",
+        "resource-server",
+        "--oauth-jwks-url",
+        f"https://a.example.com:{s}/",
+    ], "error: Invalid public auth URL."
+
+
+@pytest.mark.parametrize(
+    "case",
+    [
+        "user-policy-yaml-schema",
+        "user-policy-json-schema",
+        "user-policy-root-list",
+        "explicit-policy-yaml-schema",
+        "explicit-policy-json-schema",
+        "explicit-policy-yaml-parse",
+        "explicit-policy-missing",
+        "trust-store-parse",
+        "auth-jwks-url",
+    ],
+)
+def test_a_startup_refusal_names_the_file_and_the_refusal(
+    tmp_path: Path, case: str
+) -> None:
+    """The real CLI: a refusal reads as pmcp wrote it -- path, description and
+    consequence -- and carries nothing of the value."""
+    (tmp_path / "home" / ".claude").mkdir(parents=True)
+    project = tmp_path / "project"
+    project.mkdir()
+    args, expected = _r19_case(case, tmp_path)
+    env = {
+        key: value
+        for key, value in os.environ.items()
+        if not key.startswith(("PMCP_", "npm_config_", "pnpm_config_"))
+    }
+    env["HOME"] = str(tmp_path / "home")
+    result = subprocess.run(
+        [sys.executable, "-c", "from pmcp.cli import main; main()", *args],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=project,
+        env=env,
+        stdin=subprocess.DEVNULL,
+    )
+    assert result.returncode != 0, result.stderr
+    lines = result.stderr.splitlines()
+    assert any(line.startswith(expected) for line in lines), result.stderr
+    assert not any(form in result.stderr for form in _forbidden(_R19)), result.stderr
+
+
+@pytest.mark.parametrize("scope", ["explicit", "user"])
+def test_a_policy_refusal_still_names_the_file_and_the_refusal(
+    scope: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    """Round-17 claude F001's falsifier, as filed: every rendering of the
+    refusal -- `exception_text` and the traceback's last line -- names the
+    file, and the discovered policy's says it refuses to start."""
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.policy.policy import PolicyManager
+
+    home = tmp_path / "home"
+    (home / ".claude").mkdir(parents=True)
+    project = tmp_path / "project"
+    project.mkdir()
+    monkeypatch.setenv("HOME", str(home))
+    monkeypatch.chdir(project)
+    policy = home / ".claude" / "gateway-policy.yaml"
+    policy.write_text("tools:\n  allowlist: 5\n")
+
+    with pytest.raises(ValueError) as raised:
+        PolicyManager(policy if scope == "explicit" else None)
+    text = exception_text(raised.value)
+    last = safe_traceback_text(raised.value).rstrip("\n").rsplit("\n", 1)[-1]
+    for rendered in (text, last):
+        assert str(policy) in rendered, rendered
+        if scope == "user":
+            assert "Refusing to start" in rendered, rendered
````

### Patch — `tests/test_pkgid_panel_fixes.py`

````diff
--- a/tests/test_pkgid_panel_fixes.py
+++ b/tests/test_pkgid_panel_fixes.py
@@ -273 +273,3 @@
-    assert "npm_config_registry" in registered.message
+    # rev 12 (Consiliency/pmcp#297): the rule is named, never the name.
+    assert "NPM_CONFIG_* family" in registered.message
+    assert "npm_config_registry" not in registered.message
@@ -329 +331,4 @@
-    assert "'X$(touch CANARY)\\x1b[2J_TOKEN'" in registered.message
+    # rev 12 (Consiliency/pmcp#297): the refused name is not echoed at all,
+    # only the rule it broke.
+    assert "CANARY" not in registered.message
+    assert "1 not credential-shaped" in registered.message
````

### Patch — `tests/test_scoped_advisor_audit.py`

````diff
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -1362,20 +1362,11 @@
-#: The pre-existing log lines this plan does not own, matched as whole
-#: messages so that one carrying anything more stays in the oracle. Both come
-#: from `call_tool`'s `except Exception`, `f"Tool execution error: {e}"`:
-#: - pydantic's `InvokeInput` error for a non-dict `meta` (it reaches the
-#:   model by alias), which echoes the value -- on main too; the channel
-#:   Consiliency/pmcp#297 names. It is the only `InvokeInput` error the sweep
-#:   produces (measured: 216 lines, all this shape);
-#: - `Unknown tool: <name>`, excluded only for the exact name of the call
-#:   that produced it (see `_LoggedCalls`); unreachable under the scoped
-#:   policy, whose allowlist is four registered names.
-_FOREIGN_INVOKE_INPUT = re.compile(
-    r"Tool execution error: 1 validation error for InvokeInput\n"
-    r"meta\n"
-    r"  Input should be a valid dictionary "
-    r"\[type=dict_type, input_value=[^\n]*, input_type=[A-Za-z_]+\]\n"
-    r"    For further information visit https://errors\.pydantic\.dev/[0-9.]+/v/dict_type"
-)
-#: Only for `gateway.invoke` may a generated key change the status and so the
-#: result digest: an undeclared `meta` reaches `InvokeInput` by alias.
-_INVOKE_ONLY_EXEMPT = frozenset({"terminal_status", "redacted_result_digest"})
+#: An undeclared `meta` reaches `InvokeInput` by name (`populate_by_name`)
+#: and, when it is not a dict, fails it: since Consiliency/pmcp#297 that call
+#: is an `audit.rejection` naming only the tool and `["meta"]`, so a
+#: generated key no longer changes any field of an invocation record.
+_META_REJECTION = {
+    "event": "audit.rejection",
+    "gateway_tool": "gateway.invoke",
+    "terminal_status": "invalid_arguments",
+    "rejected_argument_path": ["meta"],
+    "rejected_argument_validator": None,
+}
@@ -1509,0 +1501,4 @@
+    # `pmcp.parsing` (Consiliency/pmcp#297, rev 7): (kind, source).
+    "YAMLParseError": ("YAML", "stub handler failed"),
+    "JSONParseError": ("JSON", "stub handler failed"),
+    "TimestampParseError": ("timestamp", "stub handler failed"),
@@ -1591,0 +1587,21 @@
+def _call_records(audit_path: Path) -> list[dict[str, Any]]:
+    """One record per call: its invocation, or its argument rejection."""
+    return [
+        _stable(r)
+        for r in validate_scoped_advisor_audit(audit_path)
+        if r["event"] in ("audit.invocation", "audit.rejection")
+    ]
+
+
+def _assert_against(
+    tool_name: str, label: str, record: dict, reference: dict, exempt: frozenset
+) -> None:
+    """`record` equals the declared-arguments `reference` bar `exempt`, unless
+    it is the one rejection a generated key can cause (`_META_REJECTION`)."""
+    if record["event"] == "audit.rejection":
+        assert tool_name == "gateway.invoke", (tool_name, label)
+        assert {k: record.get(k) for k in _META_REJECTION} == _META_REJECTION, label
+        return
+    _assert_pair(tool_name, label, record, reference, exempt)
+
+
@@ -1605,8 +1620,0 @@
-def _is_foreign(message: str, name: str | None = None) -> bool:
-    """Whether `message` is exactly one of the lines above -- the whole of it.
-    `name` is the tool name of the call that logged it, if known."""
-    if _FOREIGN_INVOKE_INPUT.fullmatch(message):
-        return True
-    return name is not None and message == f"Tool execution error: Unknown tool: {name}"
-
-
@@ -1637 +1645,3 @@
-    `exc_info` traceback -- bar the exact foreign lines above."""
+    `exc_info` traceback. Nothing is excluded: the two pre-existing echoes
+    the sweep used to skip (a non-dict `meta`, an unknown tool's name) are
+    fixed by Consiliency/pmcp#297."""
@@ -1641 +1651 @@
-    excluded = calls.foreign if calls is not None else set()
+    del calls  # every call's log is in `caplog`
@@ -1643 +1653 @@
-        formatter.format(shown)
+        formatter.format(record)
@@ -1645 +1654,0 @@
-        if (shown := _oracle_view(record, excluded)) is not None
@@ -1650,24 +1658,0 @@
-#: What stands in for an excluded message when its record carries diagnostics.
-_EXCLUDED_MESSAGE = "<excluded pre-existing line>"
-
-
-def _has_diagnostics(record: logging.LogRecord) -> bool:
-    return bool(record.exc_info or record.exc_text or record.stack_info)
-
-
-def _oracle_view(
-    record: logging.LogRecord, excluded: set[int]
-) -> logging.LogRecord | None:
-    """The record as the log oracle sees it. An excluded line (one of the
-    exact foreign messages above) is dropped only when it carries no
-    traceback, exception text or stack; otherwise only its message is
-    replaced, and the rendered diagnostics stay in the oracle."""
-    if id(record) not in excluded and not _is_foreign(record.getMessage()):
-        return record
-    if not _has_diagnostics(record):
-        return None
-    shown = logging.makeLogRecord(record.__dict__)
-    shown.msg, shown.args = _EXCLUDED_MESSAGE, None
-    return shown
-
-
@@ -1690 +1675 @@
-def _normalized_log(records: list[logging.LogRecord], excluded: set[int]) -> str:
+def _normalized_log(records: list[logging.LogRecord]) -> str:
@@ -1692 +1677 @@
-        _normalized(shown, formatter)
+        _normalized(record, formatter)
@@ -1694 +1678,0 @@
-        if (shown := _oracle_view(record, excluded)) is not None
@@ -1708,3 +1691,0 @@
-        #: Records that are exactly `Tool execution error: Unknown tool: <name>`
-        #: for the name of the call that logged them.
-        self.foreign: set[int] = set()
@@ -1717,7 +1698 @@
-            produced = self.caplog.records[start:]
-            self.foreign |= {
-                id(record)
-                for record in produced
-                if _is_foreign(record.getMessage(), name)
-            }
-            logs.append(_normalized_log(produced, self.foreign))
+            logs.append(_normalized_log(self.caplog.records[start:]))
@@ -1764 +1739 @@
-    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
+    exempt: frozenset[str] = frozenset()
@@ -1828 +1803 @@
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
@@ -1835,2 +1810,2 @@
-        _assert_pair(tool_name, label, first, second, case_exempt - _INVOKE_ONLY_EXEMPT)
-        _assert_pair(tool_name, label, first, reference, case_exempt)
+        _assert_pair(tool_name, label, first, second, case_exempt)
+        _assert_against(tool_name, label, first, reference, case_exempt)
@@ -1844 +1819 @@
-        _assert_pair(tool_name, label, first, reference, exempt)
+        _assert_against(tool_name, label, first, reference, exempt)
@@ -1882 +1857 @@
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
@@ -1888,3 +1863 @@
-        _assert_pair(
-            "gateway.invoke", f"E6 {shape}", first, reference, _INVOKE_ONLY_EXEMPT
-        )
+        _assert_against("gateway.invoke", f"E6 {shape}", first, reference, frozenset())
@@ -2034 +2007 @@
-    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
+    exempt: frozenset[str] = frozenset()
@@ -2054 +2027 @@
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
@@ -2059 +2032 @@
-        _assert_pair(tool_name, f"real {shape}", first, reference, exempt)
+        _assert_against(tool_name, f"real {shape}", first, reference, exempt)
@@ -2079,2 +2052,2 @@
-    count_a = _normalized_log([_log_record("argument_count=0x28", 1.0)], set())
-    count_b = _normalized_log([_log_record("argument_count=0x2e", 2.0)], set())
+    count_a = _normalized_log([_log_record("argument_count=0x28", 1.0)])
+    count_b = _normalized_log([_log_record("argument_count=0x2e", 2.0)])
@@ -2083,2 +2056,2 @@
-    same_a = _normalized_log([_log_record("started", 1.0)], set())
-    same_b = _normalized_log([_log_record("started", 1_000_000.5)], set())
+    same_a = _normalized_log([_log_record("started", 1.0)])
+    same_b = _normalized_log([_log_record("started", 1_000_000.5)])
@@ -2086,2 +2059,2 @@
-    repr_a = _normalized_log([_log_record("<a.B object at 0x7f00aa>", 1.0)], set())
-    repr_b = _normalized_log([_log_record("<a.B object at 0x7f00bb>", 1.0)], set())
+    repr_a = _normalized_log([_log_record("<a.B object at 0x7f00aa>", 1.0)])
+    repr_b = _normalized_log([_log_record("<a.B object at 0x7f00bb>", 1.0)])
@@ -2090,33 +2063,2 @@
-    assert _normalized_log(
-        [_log_record("fp 0x7f00aa>", 1.0)], set()
-    ) != _normalized_log([_log_record("fp 0x7f00bb>", 1.0)], set())
-
-
-def test_the_foreign_log_exclusion_matches_only_the_exact_lines() -> None:
-    """Regression (PR 306 board): a line that starts like a foreign one but
-    carries anything more stays in the oracle."""
-    with pytest.raises(ValidationError) as raised:
-        InvokeInput.model_validate({"tool_id": "a::b", "meta": "caller_marker_x"})
-    invoke_input = f"Tool execution error: {raised.value}"
-    assert _is_foreign(invoke_input)
-    assert not _is_foreign(invoke_input + " (3 args)")
-    assert not _is_foreign(invoke_input.replace("meta\n", "meta\npayload=x\n", 1))
-    unknown = "Tool execution error: Unknown tool: gateway.caller_name"
-    assert _is_foreign(unknown, "gateway.caller_name")
-    assert not _is_foreign(unknown)
-    assert not _is_foreign(unknown, "gateway.other")
-    payload = "Tool execution error: Unknown tool: payload={'k': 'caller_marker_x'}"
-    assert not _is_foreign(payload)
-    assert not _is_foreign(payload, "gateway.caller_name")
-
-
-class _Records:
-    """Stands in for `caplog` where `_log_text` needs only `.records`."""
-
-    def __init__(self, records: list[logging.LogRecord]) -> None:
-        self.records = records
-
-
-def _excluded_record(message: str, marker: str, diagnostics: str) -> logging.LogRecord:
-    record = logging.LogRecord(
-        "pmcp.server", logging.ERROR, __file__, 1, message, None, None
+    assert _normalized_log([_log_record("fp 0x7f00aa>", 1.0)]) != _normalized_log(
+        [_log_record("fp 0x7f00bb>", 1.0)]
@@ -2124,11 +2065,0 @@
-    if diagnostics == "exc_info":
-        try:
-            raise ValueError(marker)
-        except ValueError:
-            record.exc_info = sys.exc_info()
-    elif diagnostics == "exc_text":
-        record.exc_text = f"Traceback (most recent call last):\nValueError: {marker}"
-    else:
-        record.stack_info = f"Stack (most recent call last):\n  {marker}"
-    return record
-
@@ -2136,4 +2066,0 @@
-def _invoke_input_line() -> str:
-    with pytest.raises(ValidationError) as raised:
-        InvokeInput.model_validate({"tool_id": "a::b", "meta": "not-a-dict"})
-    return f"Tool execution error: {raised.value}"
@@ -2141,5 +2068,3 @@
-
-@pytest.mark.parametrize("diagnostics", ["exc_info", "exc_text", "stack_info"])
-@pytest.mark.parametrize("shape", ["invoke-input", "unknown-tool"])
-def test_an_excluded_line_keeps_its_traceback_and_stack_in_the_oracle(
-    shape: str, diagnostics: str
+@pytest.mark.asyncio
+async def test_the_formerly_excluded_log_echoes_are_gone(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture
@@ -2147,19 +2072,8 @@
-    """Regression (PR 306 board, round 2): an exactly-excluded message may
-    hide only itself. A traceback, exception text or stack attached to it is
-    rendered into the oracle, so a caller value there is caught by both the
-    forbidden-set check and the pair differential."""
-    name = "gateway.caller_name"
-    message = (
-        _invoke_input_line()
-        if shape == "invoke-input"
-        else f"Tool execution error: Unknown tool: {name}"
-    )
-    assert _is_foreign(message, name)
-    records = {
-        tag: _excluded_record(message, f"caller_marker_{tag}", diagnostics)
-        for tag in _TAGS
-    }
-    excluded = (
-        {id(record) for record in records.values()}
-        if shape == "unknown-tool"
-        else set()
+    """The sweep's log oracle used to skip two exact lines from `call_tool`'s
+    `except Exception`: pydantic's `InvokeInput` error for a non-dict `meta`
+    (value included) and `Unknown tool: <name>`. Consiliency/pmcp#297 fixes
+    both, so nothing is excluded; this pins the fixed lines."""
+    caplog.set_level(logging.DEBUG)
+    server, audit_path = _scoped_server(tmp_path)
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: True
@@ -2167,16 +2081,8 @@
-    normalized = {
-        tag: _normalized_log([record], excluded) for tag, record in records.items()
-    }
-    for tag in _TAGS:
-        assert f"caller_marker_{tag}" in normalized[tag], (shape, diagnostics)
-        assert _EXCLUDED_MESSAGE in normalized[tag]
-        assert message.splitlines()[0] not in normalized[tag]
-    assert normalized[_TAGS[0]] != normalized[_TAGS[1]]
-    calls = _LoggedCalls.__new__(_LoggedCalls)
-    calls.foreign = excluded
-    text = _log_text(_Records(list(records.values())), calls)  # type: ignore[arg-type]
-    with pytest.raises(AssertionError):
-        _assert_nothing_generated_leaked("", text, 1)
-    # Without diagnostics the excluded line is dropped entirely.
-    bare = logging.LogRecord(
-        "pmcp.server", logging.ERROR, __file__, 1, message, None, None
+    await _call(
+        server,
+        "gateway.invoke",
+        {
+            "tool_id": "firecrawl::search",
+            "meta": "caller_marker_meta",
+            **_correlations(),
+        },
@@ -2184 +2090,14 @@
-    assert _normalized_log([bare], excluded | {id(bare)}) == ""
+    await _call(server, "gateway.caller_marker_name", {})
+    await server.shutdown()
+    text = _log_text(caplog)
+    assert "caller_marker" not in text, text
+    assert (
+        "Tool execution error: invalid arguments for gateway.invoke: "
+        "$.meta: must be an object"
+    ) in text
+    assert "Tool execution error: unknown gateway tool" in text
+    rejections = _invocations(audit_path, "audit.rejection")
+    assert [{k: r.get(k) for k in _META_REJECTION} for r in rejections] == [
+        _META_REJECTION
+    ]
+    assert "caller_marker" not in audit_path.read_text()
````

### Patch — `tests/test_task_units.py`

````diff
--- a/tests/test_task_units.py
+++ b/tests/test_task_units.py
@@ -370,2 +370,4 @@
-    downstream reply into a task (calls `_task_info_from_payload`) has a row in
-    `REPLY_PATHS`, and so does each one with a no-task fallback branch."""
+    downstream reply into a task (calls `_task_info_from_payload`, or
+    `task_answer_of`, the recogniser that is that parser -- Consiliency/pmcp#297)
+    has a row in `REPLY_PATHS`, and so does each one with a no-task fallback
+    branch."""
@@ -372,0 +375 @@
+    readers = {"_task_info_from_payload", "task_answer_of"}
@@ -377,2 +380,3 @@
-        and name != "_task_info_from_payload"
-        and any(_called(n) == "_task_info_from_payload" for n in ast.walk(func))
+        # the recogniser's own module helpers are not operations
+        and name not in readers | {"usable_task_response"}
+        and any(_called(n) in readers for n in ast.walk(func))
@@ -959,2 +963,3 @@
-        "raw=payload; ttl=task_duration_from_wire('ttl', payload.get('ttl'))",
-        "THE inbound converter",
+        "raw=_usable_task_raw(payload); "
+        "ttl=task_duration_from_wire('ttl', payload.get('ttl'))",
+        "THE inbound converter; `raw` without the values dropped (#297)",
@@ -967,2 +972,3 @@
-        "raw=result",
-        "no duration: task_id, status, updated_at; `raw` is verbatim by design",
+        "",
+        "no duration: task_id, status, updated_at; no `raw`: nothing in an "
+        "unparsed answer was read (Consiliency/pmcp#297)",
````

### Patch — `tests/test_tools.py`

````diff
--- a/tests/test_tools.py
+++ b/tests/test_tools.py
@@ -3942 +3942,3 @@
-        assert result.env_var == "BAD-NAME"
+        # rev 12 (Consiliency/pmcp#297): a rejected name is not echoed back.
+        assert result.env_var is None
+        assert "BAD-NAME" not in result.message
````

### Patch — `tests/test_trust_boundaries_e2e.py`

````diff
--- a/tests/test_trust_boundaries_e2e.py
+++ b/tests/test_trust_boundaries_e2e.py
@@ -650 +650,3 @@
-    assert "npm_config_registry" in refused.message
+    # rev 12 (Consiliency/pmcp#297): the rule is named, never the name.
+    assert "NPM_CONFIG_* family" in refused.message
+    assert "npm_config_registry" not in refused.message
````

## Measurement scripts

### `mutants.py`

Run it as `PYTHONDONTWRITEBYTECODE=1 python mutants.py <worktree> <out-dir> [M4 ...]`; `NO_STATIC=1` deselects both sink checks. Without the bytecode setting, a same-size first mutant written in the checkout's mtime second leaves a stale `.pyc` (see *Mutation evidence*).

To rebuild it, take the block in `a449dd9`. Then `patch -p1` it with the `mutants.py` diffs of `48b7a89`, `8b45ddd`, `440d170`, `e6c248f`, `360fe3e`, `0dc22a4` and `40e2ba4`, in that order. Then apply this diff (rev 19: M129–M133).

````diff
--- a/mutants.py
+++ b/mutants.py
@@ -134,0 +135,9 @@
+ ("M129 the discovered-policy refusal raised inside its handler", O, [("        except Exception as e:\n            failure = exception_text(e)\n        else:\n            logger.info(f\"Loaded policy from {policy_path}\")\n            return policy\n        if fatal:\n", "        except Exception as e:\n            if not fatal:\n                raise ValueError(\n                    f\"Invalid policy file {policy_path}: {exception_text(e)}. \"\n                    \"Refusing to start rather than fall back to an unrestricted gateway.\"\n                ) from e\n            failure = exception_text(e)\n        else:\n            logger.info(f\"Loaded policy from {policy_path}\")\n            return policy\n        if fatal:\n")]),
+ ("M130 the explicit-policy refusal raised inside its handler", O, [("        except Exception as e:\n            failure = exception_text(e)\n        else:\n            logger.info(f\"Loaded policy from {policy_path}\")\n", "        except Exception as e:\n            failure = exception_text(e)\n            if fatal:\n                raise ValueError(f\"Failed to load explicit policy {policy_path}: {failure}\") from e\n        else:\n            logger.info(f\"Loaded policy from {policy_path}\")\n")]),
+ ("M131 exception-group members not walked", A, [("        members = getattr(current, \"exceptions\", None)\n", "        members = None\n")]),
+ ("M132 the trust-store parse refusal raised inside its handler", K, [("    except ValueError as exc:\n        failure = exception_text(exc)\n    if failure is not None:\n        # Outside the handler: chains nothing (Consiliency/pmcp#297 rev 19).\n        raise TrustStoreError(", "    except ValueError as exc:\n        failure = exception_text(exc)\n        raise TrustStoreError(f\"Cannot parse trust store {path}: {failure}\") from exc\n    if failure is not None:\n        # Outside the handler: chains nothing (Consiliency/pmcp#297 rev 19).\n        raise TrustStoreError(")]),
+ ("M133 the package-approval entry refusal raised inside its handler", "src/pmcp/package_approvals.py", [("    except ValueError as exc:\n        failure = exception_text(exc)\n    if failure is not None:\n        raise PackageApprovalError(f\"Invalid package approval entry: {failure}\")\n", "    except ValueError as exc:\n        raise PackageApprovalError(f\"Invalid package approval entry: {exception_text(exc)}\") from exc\n    if failure is not None:\n        raise PackageApprovalError(f\"Invalid package approval entry: {failure}\")\n")]),
+ ("M134 the SDK's rejections forwarded as sent", W, [("                    held_start = message\n                    return\n", "                    pass\n")]),
+ ("M135 the envelope validation rejection keeps pydantic's text", W, [("    elif code == INVALID_PARAMS and isinstance(message, str):\n", "    elif False:\n")]),
+ ("M136 the parse rejection keeps the parser's text", W, [("    if code == PARSE_ERROR and isinstance(message, str):\n", "    if False:\n")]),
+ ("M137 an unsupported version's `requested` returned as sent", W, [("            if isinstance(requested, str) and _PROTOCOL_REVISION.fullmatch(requested)\n", "            if isinstance(requested, str)\n")]),
````
