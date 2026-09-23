# Detailed plan: shape-based secret redaction — separator-anchored keywords, opaque-run scoring, and a prose corpus (Consiliency/pmcp#234)

> **Revision 6 (2026-09-23) — three rules inside the span design, replaced.**
> Rev 4 claimed both defect classes were "closed by construction". That was
> overstated: span collection fixed how passes *compose*, but three rules
> inside the design leaked, and two of them made rev 5 **worse than main**
> (found by a code-running board seat; every case reproduced here on rev 5,
> then re-measured). **(1)** The URL pass emitted one span for the whole URL
> with a *rewritten* replacement, and the containment rule dropped every
> keyed, shape and policy span inside its query — `?pwd=hunter2`,
> `?access=ghp_…`, `?aws_secret_access_key=…` all leaked where main redacted
> them. Now the URL pass yields **component spans** (userinfo dropped, each
> `AUTH_SECRET_QUERY_KEYS` value `[REDACTED]`, fragment dropped) and never a
> rewrite; containment drops an inner span only under a *covering*
> replacement (`[REDACTED]` or empty), anything else merges. `redact_auth_url`
> is byte-identical to `main` again. **(2)** `process_output` sliced a window,
> redacted it and returned the *whole* redacted window when redaction had
> shrunk it under the cap — the window's far edge is a second, unredacted
> cut. Now the invariant is explicit: no character from past the cap is
> emitted except inside a span (the kept text is the cap extended to the end
> of any span starting before it). **(3)** A literal `[REDACTED]` in the
> *input* disarmed every span touching it — attacker-controlled. Now an
> existing marker is itself a span that merges with whatever touches it.
> Three new properties pin these: every span the redactor collected is absent
> from the output (2 544 texts incl. query-string and marker generators,
> 7 304 spans, 0 leaks); the window edge never emits an unredacted value (80
> cuts, 13 straddling the edge, 0 leaks); and a 42-input, 50-secret corpus of
> what `main` redacts still vanishes on both surfaces. Mutation rows M28-M30
> (one per blocking fix), M32, M33. Non-blocking items are each addressed or
> restated below (Authorization gate and same-line separator; `==` is a
> comparison; source-line assignments are redacted *by design* and the
> "tracebacks survive" claim is narrowed; the expansion case is unchanged
> from `main` and stated). 129 tests.
>
> **Revision 5 (2026-09-23) — key coverage as a property.** The lead's own
> property test on rev 4 (different generator, 0 leaks in 4 094 cuts) found
> the one remaining gap: keys the redactor *names* as sensitive but did not
> redact in every form. `passwd`/`pwd` were named only by the policy default
> pattern, which a JSON closing quote defeats; a quoted `Authorization` value
> stopped at the first space or punctuation; `private_key`, `credentials`
> and bare `auth` were covered by nothing. Rev 5 widens
> `AUTH_DIAGNOSTIC_SECRET_KEYS` (`passwd`, `pwd`, `private_key`,
> `secret_access_key`, `aws_secret`, `aws_access`, `credential(s)`, `auth`),
> introduces the public `WEAK_SECRET_KEYS` (`auth`, `code`, `credential(s)`:
> a plain word or number after them is kept — `credentials: include`,
> `auth=basic`), redacts a quoted `Authorization` value whole between its
> quotes, lets a bare value start with punctuation, and makes an existing
> `[REDACTED]` inert to every span. The test is a **property derived from
> the source**: the union of `AUTH_DIAGNOSTIC_SECRET_KEYS` and every key
> named in `DEFAULT_REDACTION_PATTERNS`, each in JSON, single-quoted, `key=`
> and header form, with values that start with punctuation and contain
> spaces — 31 keys, 6 158 strings, **0 leaks** on both surfaces (per-key
> table below). M27 drops `passwd` from the strong set and the property goes
> red. Every rev 1-4 test is kept (82 tests).
>
> **Revision 4 (2026-09-23) — architecture, not cases.** Round 3 found two
> more leaks on rev 3 (the URL pass removed the backslash that escaped a
> quote; the truncation cut after a backslash), the next instances of the two
> classes behind five of the seven defects so far: **A** — a pass rewrites the
> text and destroys an anchor a later pass needs; **B** — truncation destroys
> an anchor the redactor needs, because `main` truncates *first*. Rev 4 closes
> both by construction: every pass reads the original text and returns spans
> that are merged and applied once (Class A cannot occur; the ordering audit
> is gone), and `process_output` redacts a bounded window (`max_bytes` +
> 16 KiB) *before* it cuts (Class B cannot occur; the trailing-run back-up and
> the end-of-line quoted-value rule are gone). Hand-picked cases are replaced
> by property tests: 3 000 random printable passwords × 5 keyed forms on both
> surfaces (10 470 strings, 0 leaks), a truncation sweep of 23 713 cuts with
> 17 377 landing inside a value (0 leaks; coverage asserted), and 2 000
> generated prose lines (byte-identical). Mutation rows M25 (mutate in place)
> and M26 (truncate then redact) are red under the property tests. All rev
> 1-3 regression cases are kept. `uv run mypy src/` clean.
>
> **How to apply.** **Patch:** lines 810-1587 of this file (the content between the ```diff fences; `sed -n '810,1587p' <plan> > 234.patch && git apply --check 234.patch` on `main`). **Test file:** lines 1596-2862 (between the ```python fences; `sed -n '1596,2862p' <plan> > tests/test_redaction.py`).
>
> **Revision 3 (2026-09-23).** Rev 2 boarded again (codex, static tracing; the
> lead reproduced both on the rev-2 patch; grok DEGRADED, below quorum) with
> **two BLOCKING defects**, both reproduced here before fixing and kept below
> as `REV 2 WAS WRONG`: (d) pass ordering — the `Bearer` pass ran before the
> keyword pass with a value class that accepted quotes and braces, so
> `{"password": "hunter2 Bearer test-token"}` lost its closing quote and brace
> and `hunter2` stayed visible on both surfaces; the `Authorization` rule had
> the same class *and* missed a JSON-quoted key entirely; (e) the truncation
> cut landing inside a quoted multi-word password after a space — no trailing
> token run ends there and the closing quote is gone — leaked the first word
> (128 of 1 452 swept cuts). Fixed generally: complete keyed values are
> redacted **first**, every looser value class stops at quotes and brackets,
> and a quoted value with no closing quote on its line runs to the end of the
> line. Sweep now 0 of 1 452 with 200 cuts inside a value. `uv run mypy src/`
> is in the verification and was run: clean. Mutation rows M21-M24 added.
>
> **Revision 2 (2026-09-23).** Rev 1 (PR Consiliency/pmcp#288) boarded with
> **three BLOCKING defects** (codex, by static tracing; the lead reproduced all
> three against the embedded patch on current main; this revision reproduced
> them again before fixing). Each is kept below as `REV 1 WAS WRONG` with its
> before/after measurement, because each one is a trap: (a) the payload bound
> was checked *after* path splitting, so a slash-leading base64 image
> (`/9j/…`, a `/` every ~64 chars) came back as `[REDACTED]/[REDACTED]/…`;
> (b) the truncation back-up was bounded on the whole trailing *run*, so a
> credential after a long path (`/bbb…(68)/ghp_16C7e`) was left as a prefix;
> (c) quoted values stopped at an escaped quote, so `{"password": "a\"hunter2"}`
> left `hunter2"` behind. Three mutation rows (M18-M20) now pin the fixes.
>
> **Provenance.** This plan resumes an unfinished, unmeasured spike left in the
> `plan/234-redactor` worktree by a previous planner (uncommitted edits to
> `src/pmcp/auth.py`, `src/pmcp/policy/policy.py`, `tests/test_auth.py`, and an
> untracked `tests/test_redaction.py`). The spike was read critically, probed
> against an extended corpus, and **ten defects were found and fixed** (see
> "Verdict on the inherited spike"). Every measurement below was taken in this
> session; nothing is inherited as fact. The commit that carries this plan is
> plan-only: `src/` and `tests/test_auth.py` are byte-identical to `main` @
> `860636a`, and the test file's bodies are embedded under `## Test bodies`.

## Task

Close Consiliency/pmcp#234. The secret redactor that stands between a
downstream server's error text and the agent's prompt-injectable context is
**keyword-anchored**: it redacts the word after `token`, `secret`, `session`,
`password`, `bearer` whether or not that word is a credential, and it lets
credentials that carry no keyword (`AKIA…`, `ghp_…`, `xoxb-…`, a token in a URL
*path*) straight through. Both directions are bugs: a false negative leaks a
credential into an injectable surface; a false positive trains readers to
ignore `[REDACTED]`. Consiliency/pmcp#225 widened the surface by routing far
more exception text — including tracebacks — through `sanitize_auth_diagnostic`.

Deliver **separator-anchored keyword rules AND shape-based rules**, plus a
**prose corpus test** in which ordinary English and ordinary diagnostic text
must survive byte-identically. Both directions are acceptance criteria.

## Research summary

### Surfaces and callers (unchanged by this plan)

- `sanitize_auth_diagnostic(value, *, max_length=400)` in `src/pmcp/auth.py`
  — the engine. Called from `client/manager.py` (`describe_exception`, and the
  traceback path at `manager.py:2672` with `max_length=None`), `cli.py` (status,
  `next=` step, errors), `cli_commands/doctor.py`, `tools/handlers.py`
  (`_sanitize_error`, feedback events), and inside `auth.py` itself
  (`ResourceServerAuthError.description`, `parse_www_authenticate`
  `error_description`, `normalize_auth_metadata` diagnostics).
- `PolicyManager.redact_secrets(output)` in `src/pmcp/policy/policy.py` — the
  engine with `max_length=None`, then the operator's `_redaction_regexes`
  (defaults from `DEFAULT_REDACTION_PATTERNS`), each match split at its first
  `:`/`=`. Called from `process_output` (`gateway.invoke` when
  `options.redact_secrets` is set or the result belongs to a task;
  `gateway.tasks_result` **by default**), task `status_message`/`raw`
  sanitising, and feedback scrubbing.
- `redact_auth_url(url)` in `src/pmcp/auth.py` — userinfo + auth query keys.
  Reached from the engine's URL pass **and** from `sanitize_public_auth_url` →
  `sanitize_url_elicitation_url`, i.e. it produces **the URL the operator must
  open to authorize**. Nothing in `tests/test_auth.py` pins an opaque path
  segment through that route today (the fixtures are `/cb`, `/meta`).

### Measured on `main` @ `860636a` — both surfaces

Taken from a throwaway detached worktree of HEAD (`git worktree add --detach
/mnt/HC_Volume_105438154/worktrees/pmcp-234-head-probe HEAD`, imported via
`PYTHONPATH`, `pmcp.__file__` printed to prove which tree answered), never by
reverting the spike in place.

| input | engine (`sanitize_auth_diagnostic`) | policy (`redact_secrets`) | class |
|---|---|---|---|
| `token bucket rate limiting is enabled` | `token [REDACTED] rate limiting is enabled` | same | **FP** — `[\s:=]+` separator |
| `the secret ingredient is love` | `the secret [REDACTED] is love` | same | **FP** |
| `the secret to good code` | unchanged | unchanged | survives only because `to` < 3 chars |
| `session expired, password reset sent` | `session [REDACTED], password [REDACTED] sent` | same | **FP** |
| `Missing bearer token` / `the bearer of bad news` | `Missing bearer [REDACTED]` / `the bearer [REDACTED] bad news` | same | **FP** — `(\bbearer\s+)[^\s,;]+` |
| `status_code=401 error_code=invalid_grant` | `status_code=[REDACTED] error_code=[REDACTED]` | same | **FP** — `code` matched anywhere in the key |
| `exit code 137` | `exit code [REDACTED]` | same | **FP** |
| `token_endpoint=https://auth.example/oauth/token` | `token_endpoint=[REDACTED]://auth.example/oauth/token` | same | **FP** — `token` matched as a substring of the key |
| `token=secret-bearer failed` | `token=[REDACTED] [REDACTED]` | same | **FP on `failed`**: `\bbearer` fires after the hyphen in `secret-bearer` and eats the *next* word |
| `https://api.example.com/v1/sk-live-abc123def456/status` | unchanged | `…/v1/[REDACTED]/status` | **FN (engine)** — path credential; the policy surface only catches it via the `sk-` default |
| `AKIAIOSFODNN7EXAMPLE` | unchanged | unchanged | **FN** both |
| `ghp_16C7e42F292c6912E7710c838347Ae178B4a` | unchanged | `[REDACTED]` | **FN (engine)** — policy catches it via the `ghp_` default only |
| `xoxb-2444-2444-abcdefghijklmnop` | unchanged | unchanged | **FN** both |
| `bare 4eC39HqLyjWDarjtT1zdp7dc here` (bare random alnum) | unchanged | unchanged | **FN** both |
| `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` (AWS secret key) | unchanged | unchanged | **FN** both |
| `AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBxY` | unchanged | unchanged | **FN** both |
| `sk_test_4eC39HqLyjWDar7dc` | unchanged | unchanged | **FN** both (the `sk-` default wants a hyphen) |
| `https://hooks.example/services/T0123ABCD/B0123ABCD/a1B2c3D4e5F6g7H8i9J0k1L2` | unchanged | unchanged | **FN** both — webhook secret in the path |
| `{"password": "hunter2"}` | unchanged | unchanged | **FN** both — the quote after the key defeats `[\s:=]+` |
| `{"code": -32601, "message": "x"}` | unchanged | unchanged | correct today; the spike broke it (below) |
| `<Foo object at 0x7f3a2b1c4d50>`, `pod pmcp-7d9f8b6c5-x2k9q` | unchanged | unchanged | correct today; the spike broke both (below) |
| custom operator pattern `[A-Za-z0-9+/]{16,}={1,2}` on `basic dXNlcjpwYXNzd29yZA== auth` | — | `basic dXNlcjpwYXNzd29yZA= [REDACTED] auth` | **leak** — `redact_secrets` splits at the first `=`, which here is base64 padding: the secret survives and the padding is "redacted" |

Mechanism of the keyword FP (HEAD `auth.py:598-603`): the separator class
`[\s:=]+` includes whitespace, so `<keyword><space><any 3+ char word>` loses the
word. Mechanism of the keyword FN: the rule needs the keyword; a bare credential
has none. Mechanism of the `bearer` FP: `\b` treats `-` as a boundary.

Other facts established this session:

- `policy_digest` hashes the loaded `GatewayPolicy` models only
  (`policy.py:552-563`); `DEFAULT_REDACTION_PATTERNS` are applied at
  compile time and are **not** in the payload, so changing the defaults does
  not move any operator's telemetry digest.
- `tests/test_project_source_consent_policy.py::test_an_explicit_user_redaction_list_is_not_dropped_by_the_project`
  asserts `"ghp_abcdefghijklmnop" in PolicyManager().redact_secrets("ghp_abcdefghijklmnop")`
  once the defaults are displaced, and the two C-13 proofs assert the same
  probe **is** redacted while a default is present. The engine must therefore
  stay blind to `ghp_` + a whole-alpha body, or those proofs go vacuous.
- `GatewayPolicy.max_output_bytes` defaults to **50000** (`types.py:1054`).
  Engine throughput on this host, 1 MB inputs: HEAD 0.42 s (prose) / 0.31 s
  (one base64 run); revised 0.84 s / 0.63 s; MIME-wrapped base64 0.72 s;
  random alnum words 0.81 s. At the default cap that is ~40 ms per call.
- `gateway.tasks_result` calls `process_output(..., redact=True)` by default
  (`handlers.py:6522`) and `process_output` JSON-dumps non-string results, so
  `ImageContent.data` and any base64 payload a tool returns **does** go through
  the engine. A shape rule with no upper bound on run length replaces the whole
  image with `[REDACTED]` (measured on the spike: an 8 000-char blob →
  `[REDACTED]`).

### Verdict on the inherited spike

**Kept** (its design is sound): keyword rules with the secret key as the
*last* segment of the identifier; a whitespace-separated keyword only redacts a
credential-shaped value; a small documented vendor supplement (AWS key-id
family, Slack `xox*`, Google `AIza`, PEM blocks); a `prefix[_-]body` rule for
structured vendor tokens whose body must mix letters and digits (so the C-13
probe stays invisible); the character-class-transition score for bare opaque
runs with uniform-hex exempt; redact-then-cut in the engine; backing the
`truncate_output` cut off a partial token; the same two prose/credential corpora
as the test design; the fixes to `DEFAULT_REDACTION_PATTERNS`.

**Replaced or fixed** (each defect measured on the spiked tree with
`scratchpad/probe/probe_spike.py` before the fix, re-measured after):

| # | spike behaviour | why it is wrong | fix in this plan |
|---|---|---|---|
| 1 | `redact_auth_url` redacted opaque **path** segments | `redact_auth_url` is what `sanitize_url_elicitation_url` returns — the login URL. `https://dev-1.okta.com/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize` became `…/oauth2/[REDACTED]/v1/authorize`: an unopenable OAuth flow. The diagnostic surface never needed it: the engine's whole-text shape pass already reaches a URL path. | `redact_auth_url` **unchanged from HEAD**; guard test that it keeps the Okta id byte-identical; path-credential test moved to the engine |
| 2 | `{"code": -32601, "message": …}` → `{"code": [REDACTED], …}` | JSON-RPC error codes are every MCP error. The spike's `code` gate ("has a digit, ≥4 chars, ≥5 if all digits") accepted `-32601` because of the sign. | `code` redesigned (D5): bare or OAuth-qualified only, and never a word or number |
| 3 | `Missing bearer token` → `Missing bearer [REDACTED]`; `the bearer of bad news`; `Bearer Token is required` | the most common auth diagnostic phrase in existence | `Bearer` value must not be a plain word or number (D3) |
| 4 | `token secret-bearer failed` → `token secret-bearer [REDACTED]` | inherited HEAD's `\bbearer`: the credential survives and `failed` is redacted | `(?<![A-Za-z0-9_-])` instead of `\b` |
| 5 | `<Foo object at 0x7f3a2b1c4d50>` → `<Foo object at [REDACTED]>` | every traceback and repr; the `x` defeated the uniform-hex exemption | `_UNIFORM_HEX_RE` accepts an optional `0x` |
| 6 | an 8 000-char base64 blob → `[REDACTED]` | image data through `tasks_result` (see facts above) | `_OPAQUE_MAX_RUN = 256`; prefixed-body bound `{12,256}` |
| 7 | `pod pmcp-7d9f8b6c5-x2k9q` → `pod [REDACTED]` | hyphen-joined short pieces, concatenated, score as random | a run containing `-`/`_` is scored per segment only, never whole |
| 8 | `Missing bearer token:\nthe bearer…` → next line's first word redacted (found by extending the prose corpus) | the key/value separator `\s*` spanned the newline (HEAD's defaults have the same `[\s]*`) | `[ \t]*` in `_KEYWORD_SEP_RE` **and** in the four key/value `DEFAULT_REDACTION_PATTERNS` |
| 9 | `token code=404` / `token expires_in=3600` → `token [REDACTED]` | a `param=value` after a keyword is another parameter, not the keyword's value | `(?![A-Za-z_-]+=[^=])` lookahead in `_KEYWORD_WS_RE` (the spike already had it for `Bearer realm=`) |
| 10 | `redact_secrets` split-at-first-separator kept | HEAD bug, measured above: base64 padding taken as the separator | split only at a separator that has a value after it |

**The `tests/test_auth.py` edit is dropped.** The spike changed two existing
fixtures — `error_description="token secret-bearer failed"` → `token=secret-bearer
failed` (line 764) and `error_description="code=super-secret"` → `token=super-secret`
with the assertion `code=[REDACTED]` → `token=[REDACTED]` (lines 1116-1121) —
because its own gates ("value must contain a digit") could not satisfy them. Under
this plan's gates both original fixtures pass **unchanged**: `secret-bearer` is a
punctuated 13-character value (not a plain word) so the whitespace rule redacts
it, and bare `code=` with a non-word value is the OAuth callback parameter and is
redacted. Measured: `tests/test_auth.py` restored from HEAD → **128 passed** against
the revised engine. Neither old assertion was wrong; the spike's gate was.

### Rev 6 board findings — before/after, measured (both surfaces)

| # | input | main | rev 5 | rev 6 |
|---|---|---|---|---|
| 1 | `https://example.com/?aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY` | `?aws_secret_access_key=[REDACTED]` | **unredacted** | `?aws_secret_access_key=[REDACTED]` |
| 1 | `https://api.github.com/repos?access=ghp_16C7e…` | policy redacts | **unredacted** | `?access=[REDACTED]` |
| 1 | `https://example.com/login?pwd=hunter2` | policy redacts | **unredacted** | `?pwd=[REDACTED]` |
| 1 | `password=https://h.example/?a=1,b=2` | — | `password=https://h.example/?a=1%2Cb%3D2` | `password=[REDACTED],b=2` (policy: `password=[REDACTED]`) |
| 2 | PEM (66 236 `x`) + `\n`×pad + `{"password": "hunter2 tail"}` at the default cap | — | `hunter2` at pads 70-75 | 0 of pads 20-109 |
| 3 | `password=hunter2[REDACTED]` | `password=[REDACTED][REDACTED]` | **unchanged** | `password=[REDACTED]` |
| 3 | `api_key=abc123def456[REDACTED]` | redacted | **unchanged** | `api_key=[REDACTED]` |
| 3 | `{"password": "hunter2 [REDACTED]"}` | unchanged | unchanged | `{"password": [REDACTED]}` |
| nb | `authorization: none` / `Set the Authorization:\nheader first` | — | `[REDACTED]` | unchanged |
| nb | `if token == expected:` | — | `if token =[REDACTED] expected:` | unchanged |
| — | composition property | — | — | 2 544 texts, 7 304 spans, 0 leaks |
| — | never worse than main | — | 3 regressions | 42 inputs / 50 secrets, 0 failures |

### Rev 5 lead findings — before/after, measured

Reproduced on the rev-4 patch, re-measured after the change (both surfaces).

| input | rev 4 | rev 5 |
|---|---|---|
| `{"passwd": "Xk9#mQ2vL"}`, `{"pwd": …}` | unredacted (named only by the policy default, which the JSON quote defeats) | `{"passwd": [REDACTED]}` |
| `{"Authorization": "Xk9 mQ2vL"}` | `{"Authorization": "[REDACTED] mQ2vL"}` | `{"Authorization": "[REDACTED]"}` |
| `{"Authorization": "(Xk9mQ2vL"}` | unredacted (value could not start on `(`) | `{"Authorization": "[REDACTED]"}` |
| `{"private_key": …}`, `{"credentials": …}`, `{"auth": …}` | unredacted | `[REDACTED]` (the last two weak: `credentials: include`, `auth=basic` kept) |
| `password=(Xk9mQ2vL)` | engine unredacted; policy `password= [REDACTED]` | `password=[REDACTED]` on both |
| derived-key property | — | 31 keys × 4 forms, 6 158 strings, **0 leaks** |

### Rev 4 board findings — before/after, measured

Both reproduced on the rev-3 patch in this worktree, then re-measured after
the architecture change.

| # | input | rev 3 | rev 4 |
|---|---|---|---|
| f | `{"password": "https://example.test/?token=abc123def456\"hunter2"}` | `{"password": [REDACTED]hunter2"}` (both surfaces: the URL pass removed the escaping backslash) | `{"password": [REDACTED]}` (both) |
| g | `"a"*177 + " " + json.dumps({"password": "hunter2\\tail"}) + "c"*100`, `process_output(redact=True, max_bytes=300)` | `…{"password": "hunter2\\` + marker — `hunter2` visible | `…{"password": [REDACTED` + marker |
| — | static property: 3 000 passwords × 5 forms | (rev 3 had hand-picked cases) | 10 470 strings, 0 skipped, **0 leaks** on both surfaces |
| — | truncation property: 300 passwords × forms × every cut across the value | (rev 3: one 1 452-cut sweep of three passwords) | 1 056 values, **23 713 cuts, 17 377 inside a value, 0 leaks** |
| — | prose property: 2 000 generated lines | — | byte-identical on both surfaces |
| — | `uv run mypy src/` | clean | `Success: no issues found in 49 source files` |

### Rev 3 board findings — before/after, measured

Both reproduced on the rev-2 patch in this worktree, then re-measured after
the fix.

| # | input | rev 2 | rev 3 |
|---|---|---|---|
| d | `{"password": "hunter2 Bearer test-token"}` | `{"password": "hunter2 Bearer [REDACTED]` (both surfaces; closing quote and brace gone, `hunter2` visible) | `{"password": [REDACTED]}` (both) |
| d | `{"authorization": "Bearer abc123def456", "x": 1}` | **unchanged** — not matched at all | `{"authorization": "[REDACTED]", "x": 1}` |
| d | `{"note": "see Bearer abc123def456"}` | `{"note": "see Bearer [REDACTED]` | `{"note": "see Bearer [REDACTED]"}` |
| e | `"a"*177 + ' {"password": "hunter2 tail"}' + "c"*100`, `process_output(redact=True, max_bytes=300)` | `…aaa {"password": "hunter2 ` + marker — `hunter2` visible | `…aaa {"password": [REDACTED]` + marker |
| e | sweep: 4 prefixes × 3 passwords × 121 cuts = 1 452 | **128 leak** the first word | **0 leak**; 200 cuts inside a quoted value (coverage asserted) |
| — | `uv run mypy src/` | clean (lead) | `Success: no issues found in 49 source files` |

### Rev 2 board findings — before/after, measured

All three reproduced on the rev-1 patch applied to this worktree (main @
`860636a`; the lead reproduced them on current main), then re-measured after
the fix. `TOKEN = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"`.

| # | input | rev 1 (both surfaces unless noted) | rev 2 |
|---|---|---|---|
| a | `"/" + "4eC39HqLyjWDarjtT1zdp7dc" + "/" + "A"*8190` | `/[REDACTED]/AAAA…` | byte-identical |
| a | `"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgH" + 6 000 chars of seeded random base64 (101 slashes)` | corrupted from offset 28; 82 `[REDACTED]`s (`redact_secrets` and engine) | byte-identical |
| a | 360-char REST route ending `/secrets/4eC39HqLyjWDarjtT1zdp7dc/versions/latest` | `…/secrets/[REDACTED]/versions/latest` | same (still split: reads as a route) |
| b | `"a"*120 + " /" + "b"*68 + "/" + TOKEN + " " + "c"*100`, `process_output(redact=True, max_bytes=300)` | result contains `ghp_16C7e` (trailing run 79 chars > 64) | no `ghp_`; result ends `…aaa ` + marker |
| b | `"a"*20 + " /" + "b"*300 + "/" + TOKEN + …`, `max_bytes=450` | (not covered by rev 1's tests) | no `ghp_`; ends `…bbb` + marker (last segment backed out) |
| b | `"b"*400`, `max_bytes=300` | `bbb…(200)` shown (offset-0 guard) | marker only |
| b | `"b"*1000`, `max_bytes=600` | 500 `b`s | 500 `b`s (giant run still truncates) |
| c | `{"password": "a\"hunter2"}` | `{"password": [REDACTED]hunter2"}` | `{"password": [REDACTED]}` |
| c | `{'password': 'a\'hunter2'}` | `{'password': [REDACTED]hunter2'}` | `{'password': [REDACTED]}` |

## Design

Two mechanisms, both new in rev 4, close the two defect classes the board
kept finding **by construction**; the detection rules underneath are the ones
revs 1-3 tuned, unchanged.

### Composition: span collection (Class A)

`sanitize_auth_diagnostic` no longer rewrites the text pass by pass. Every pass
reads the **same, immutable** text and returns spans — `(start, end,
replacement)` over that text — and `apply_redaction_spans` applies them all
in one step:

```python
text = apply_redaction_spans(text, collect_redaction_spans(text))
return text if max_length is None else text[:max_length]
```

No pass ever sees another pass's output, so no pass can consume a boundary a
later pass needs. That is the whole of the fix for the rev-2 `Bearer` defect
(it ate a closing quote) and the rev-3 URL defect (it removed the backslash
that escaped a quote): the keyed-value pass sees `{"password":
"https://example.test/?token=abc123def456\"hunter2"}` exactly as written,
its span covers the whole quoted value, and the URL's span lies inside it.
The rev-3 "pass-ordering audit" is gone because there is no order.

`apply_redaction_spans` merge rules, each with the case it exists for:

- Spans are sorted by `(start, -end, replacement != "[REDACTED]")`.
- A span **inside** another is dropped only when the outer replacement
  *covers* it — `[REDACTED]` or the empty string (a URL inside a quoted
  password → `[REDACTED]` with the password; a query value inside a dropped
  userinfo/fragment). Under any other replacement the two merge to
  `[REDACTED]`. (`REV 5 WAS WRONG`: unconditional — see pass 1.)
- Two spans that **partially overlap** are merged and their union becomes
  `[REDACTED]` — the conservative direction; it never leaves a fragment.
- For **identical** ranges `[REDACTED]` beats any other replacement
  (`token=https://…` → `token=[REDACTED]`, not a rewritten URL).
- Every `[REDACTED]` already in the text is **itself a span** and merges
  with whatever touches it: `password=hunter2[REDACTED]` → `password=
  [REDACTED]`, `{"password": "hunter2 [REDACTED]"}` → `{"password":
  [REDACTED]}`; a marker nothing touches is replaced by itself, which is what
  keeps `password=[REDACTED]`, `password= [REDACTED]` and `{"password":
  [REDACTED]}` fixed points on both surfaces. `REV 5 WAS WRONG`: it dropped
  any span touching an existing marker, so a downstream server could shield
  a value by writing the literal next to it (338/600 random values leaked in
  the board's sweep; `main` redacted them).
- A bare keyed value stops at `&` as well as whitespace, quotes and `,`/`;`,
  so a keyed value inside a query string covers one pair, not the rest of
  the query (`?token=x&page=2` keeps `&page=2`).

The passes, as span producers (`collect_redaction_spans`):

1. **URLs** — `_url_spans` → `_url_component_spans`: for each
   `https?://[^\s"'<>]+` match (trailing `).,;` handed back), the **parts**
   `redact_auth_url` would strip or redact, as spans over the surrounding
   text — the userinfo (replacement empty), each value under an
   `AUTH_SECRET_QUERY_KEYS` key (`[REDACTED]`), the fragment (empty). Never a
   rewrite. `REV 5 WAS WRONG`: it emitted one span for the whole URL whose
   replacement was `redact_auth_url(url)`, and the containment rule dropped
   every keyed, shape and policy span inside the query as "contained" —
   `?aws_secret_access_key=wJal…`, `?access=ghp_…`, `?pwd=hunter2`, a JWT
   under a harmless key, all unredacted on both surfaces where `main`
   redacted them. The path is left to the shape pass like any other text
   (`/v1/[REDACTED]/status`); `redact_auth_url` is **byte-identical to
   `main`** (no `path_transform`), so the elicitation URL is untouched (M16).
   Diagnostic output for a redacted query value is now `?token=[REDACTED]`
   rather than `main`'s re-quoted `?token=%5BREDACTED%5D`; the rest of the
   URL is left exactly as written (no re-quoting, no IPv6 re-bracketing).
2. **`<key><sep><value>`** — `_keyword_sep_spans` (D1): the span is the
   value. `sep` is `:` or `=` on the same line, quotes allowed around key and
   value (JSON). The key's **last** segment (`_`/`-` joined or camelCase)
   must be a secret key from `AUTH_DIAGNOSTIC_SECRET_KEYS` (or `api[_-]?key`),
   optionally suffixed `_id`/`_key`/`s`: `access_token=`, `X-Auth-Token:`,
   `accessToken=`, `"password": "…"`, `session_id=`, `Set-Cookie:` fire;
   `token_type=`, `token_endpoint=`, `secret_arn=`, `password_hash=`,
   `tokenizer=` do not. The key set (`AUTH_DIAGNOSTIC_SECRET_KEYS`) is the
   union of everything either surface ever named: rev 5 adds `passwd`,
   `pwd`, `private_key` (also `private-key`/`privateKey`),
   `secret_access_key`, `aws_secret`, `aws_access`, `credential`,
   `credentials`, `auth`. **Strong** keys redact **any** value. **Weak** keys
   (`WEAK_SECRET_KEYS` = `auth`, `code`, `credential`, `credentials`) keep a
   plain word or number — `credentials: include` is a fetch mode, `auth=basic`
   a scheme, `{"code": -32601}` a JSON-RPC error — and redact everything else
   (`auth=user:s3cret`, `credentials="…"`). `code` additionally needs to be
   bare or OAuth-qualified (pass 5). `==` is a comparison, not a separator
   (`if token == expected:` survives). A bare value ends at whitespace, a
   quote, a list separator (`,` `;`) or a query separator (`&`) and at
   nothing else, and never *ends* on a closing bracket: `password=(Xk9mQ2vL)` is one value, punctuation and all, while the
   `}` of `{"password": [REDACTED]}` stays with the object. A quoted value runs
   to its **closing** quote, past escaped ones (`"(?:[^"\\\n]|\\.)*"` and
   the single-quote twin), never across a newline (a JSON string cannot hold
   one), and it needs that closing quote: every surface redacts before it
   cuts (below), so the redactor always sees whole values, and rev 3's
   "unterminated value runs to end of line" rule is **dropped** (one regex
   branch fewer; the case it served no longer arises).
3. **`Authorization: …`** — `_authorization_spans`: the key may be
   JSON-quoted (`"authorization": "Bearer x"`), the separator is on the same
   line (`Set the Authorization:\nheader first` is a sentence). A **quoted**
   value is redacted whole between its quotes (`"Authorization": "Xk9 mQ2vL"`
   → `"Authorization": "[REDACTED]"`); a bare value is an optional HTTP auth
   scheme word (`Bearer`, `Basic`, `Digest`, `Negotiate`, `NTLM`, `Token`) then
   a run to whitespace, a quote or a list separator, never ending on a
   closing bracket — unless it is a plain word or number (`authorization:
   none`), which is prose (rev 5 had no such gate).
4. **`Bearer <value>`** — `_bearer_spans` (D3): the HTTP scheme, so the value
   is a token unless it is a plain word or number (`bearer token`, `bearer
   of`, `Bearer Token`) or a challenge parameter (`Bearer realm="…"`); not
   when `Bearer` is itself a value (`token_type=Bearer`). Boundary
   `(?<![A-Za-z0-9_-])`, never `\b`; the value stops at `"'()[]{}`.
5. **`<keyword><whitespace><value>`** — `_keyword_ws_spans` (D2), including
   `--flag value`: redacts only a value that *could be a credential* — not a
   plain word or number, and either digit-bearing and ≥ 6 chars
   (`abc123def456`, `hunter2`) or punctuated and ≥ 8 (`secret-bearer`,
   `correct-horse-battery-staple`). `token bucket`, `session expired`,
   `password reset`, `token v2`, `token 3` all survive; a `param=value` after
   the keyword is not its value. **`code`** (D5) is a credential only in the
   OAuth sense — bare (`code=`) or qualified by
   `auth`/`authorization`/`oauth`/`device`/`user` — and even then a plain word
   or number is kept (`{"code": -32601}`, `{"code": "not_found"}`,
   `code=404`); any other qualifier (`status_code=`, `error_code=`) leaves
   the value to the shape rules, which still catch an opaque one. `code`
   never fires on whitespace.
6. **Shape** — `_shape_spans` (D4): JWT (three dot-joined base64url
   segments); the vendor supplement (AWS access-key-id family, Slack
   `xox[abeprs]-…`, Google `AIza` + 35, PEM private-key blocks — a
   supplement, not the defence); prefixed tokens (`[a-z]{2,8}` prefix, up to
   two short qualifiers, `[_-]`, then 12-256 alphanumerics containing both a
   letter and a digit — whole-alpha bodies deliberately excluded so the C-13
   probe `ghp_abcdefghijklmnop` stays invisible); and **opaque runs**: every
   maximal `[A-Za-z0-9_+/-]` run (`={0,2}` padding allowed) is scored by
   `_run_spans` — lower/upper/digit classes change hands ≥ 3 times at > 25 %
   of positions, ≥ 2 digit runs (or, with one, the `xAB` mixed-case
   signature and ratio > 0.35), under 16 chars no lower-case word longer
   than 4; uniform hex (optionally `0x`-prefixed) never; runs with `-`/`_`
   per segment only (`pmcp-7d9f8b6c5-x2k9q` survives). **The 256-char
   payload bound applies to the whole run before any path splitting** (rev
   1 split first and destroyed JPEG base64, which starts `/9j/` and carries
   a `/` every ~64 chars); a longer run is still split when it reads as a
   route (every piece ≤ 64 chars, two of them plain words), so a 360-char
   REST path still loses only its opaque segment. A `/`-run that reads as a
   route yields one span per opaque piece (`/v1/[REDACTED]/status`).

Then the cut to `max_length`. Redaction ran on the whole text first, so the
cut can shorten `[REDACTED]` (to `[REDAC`, cosmetic) but never expose a token
prefix (`test_the_engine_redacts_before_it_truncates`, M11).

### Truncation: redact first, over a bounded window (Class B)

`main`'s `process_output` truncates first and redacts the truncated text.
That order is the root of every truncation leak the board found: the cut
lands inside a credential — after a run boundary, after a space in a quoted
value, after a backslash — and the fragment left behind is a shape no rule
recognises. Revs 2-3 patched three instances (a trailing-run back-up, a path
segment back-up, "unterminated value runs to end of line"); rev 4 removes the
cause and those three patches.

```python
if redact:
    max_size = max_bytes or self.get_max_output_bytes()
    window = output_str[: max_size + _REDACTION_WINDOW_SLACK]
    final_str, truncated, _ = self.truncate_output(
        self.redact_secrets(window), max_bytes, original_size=raw_size
    )
```

The redactor collects spans over the first `max_bytes + K` **characters** of
the output (characters ≥ bytes, so the window always reaches past the byte
cut by at least K). **Invariant: no character from past original offset
`max_bytes` is emitted except inside a span.** The text kept is the cap,
extended to the end of any span that starts before it (`keep`, iterated in
span order so a chain of spans extends it once each); the spans are applied
to that head, and the redacted head is byte-cut with the marker. `REV 5 WAS
WRONG`: it applied the spans to the *whole* window and, when redaction had
shrunk the window under the cap, returned all of it — including the window's
far edge, a second cut nobody redacted. Measured at the default cap with an
oversized PEM block (66 236 `x`) as the shrinkable prefix and a quoted
password padded to straddle offset 66 384: `hunter2` leaked at pads 70-75;
after the fix, 0 of the same sweep, and 0 of 80 cuts (13 straddling) in the
property test at `max_bytes=1000`. Every value the cut could land in is
still seen whole. **K = `_REDACTION_WINDOW_SLACK` = 16384**: it
must exceed the longest keyed value the redactor can be asked to match
across the cap — a 4096-bit RSA private key in PEM is ~3.2 KB, a JWT with
generous claims a few KB, a SAML assertion in a `saml=`/`assertion=` value
under ~16 KB — and the cost is bounded by it: ~1 ms per KB on this host
(0.84 s/MB), so ≤ ~17 ms of slack at the 50 000-byte default cap. The
residual is a single keyed value longer than 16 KiB that starts before the
cap and ends after it — not a credential shape; a larger K only moves that
line. Below the cap nothing changes (the window is the whole output). When
the redacted window fits under the cap but text was dropped, the marker is
still appended with the real size (`truncate_output(original_size=…)`,
additive keyword parameter; `_truncation_marker` factored out so both paths
write the same marker). `redact_secrets` is span-based too: the engine's
spans and the operator's pattern spans are collected over the same text and
applied together, so operator patterns never see the engine's rewriting
(its `key<sep>` split rule and the base64-padding guard are unchanged).

Does any other surface receive already-cut text? Checked every
`sanitize_auth_diagnostic` caller: `describe_exception` (manager) returns
`sanitize_auth_diagnostic(exc)` — redact-then-cut inside; `last_error` is
always the result of `describe_exception`; the CLI re-sanitises those
already-sanitised strings (idempotent); `_sanitize_error` (handlers) cuts
`msg[:400]` **after** `sanitize_auth_diagnostic(e)`; the traceback path
passes `max_length=None`. **No internal surface pre-cuts**, so no other
window is needed and design (b) is not needed either. Text a *downstream
server* itself truncated (`{"password": "hunter2 ta` in its own error body)
is the server's text: the value is unterminated because it sent it so, and
the same server could send `hunter2` bare; that is not ours to guess at.

Two things this does **not** do, both unchanged from `main` and stated: the
cap is not enforced when redaction *expands* an output that was under it
(297 bytes of `password=a ` × 27 at `max_bytes=300` → 540 bytes,
`truncated=False`; every `[REDACTED]` is longer than `a`) — an under-cap
output is returned whole on `main` too, and enforcing the cap there would
mark as truncated an output nothing was cut from; and the summary's
`first_line` is taken from the redacted text, as before.

**Tracebacks — the claim, narrowed.** Traceback *frames* (`File "…", line N,
in f`, reprs, `0x…` addresses, exception messages, pip/git/TLS lines) survive
byte-identically (the prose corpus and the prose property). A *source line*
that assigns to or annotates a secret-named variable does not:
`token = await self._get_token()` → `token = [REDACTED] self._get_token()`,
`def login(username: str, password: str)` → `password: [REDACTED])`,
`auth = aiohttp.BasicAuth(user, pw)` → `auth = [REDACTED], pw)`. That is by
design: in a log, what follows `token =` is a secret far more often than a
keyword, and exempting plain words after a *strong* key would leave
`password=hunter` visible everywhere. `if token == expected:` does survive
(`==` is not a separator). Consiliency/pmcp#225 routes tracebacks through
this engine; the source line under the frame may lose its right-hand side.

### History (each line was a shipped rev's mistake; each is a trap)

- Rev 1: keyword-anchored `[\s:=]+` mangled prose; keyword-less credentials
  passed; `\bbearer` fired inside `secret-bearer` and ate the *next* word.
- Rev 1 → 2: the payload bound was checked *after* path splitting (JPEG
  base64 destroyed); the truncation back-up was bounded on the whole run;
  quoted values stopped at an escaped quote.
- Rev 2 → 3: the `Bearer` pass ran before the keyword pass and ate a closing
  quote; the cut inside a quoted multi-word password leaked its first word.
- Rev 3 → 4: the URL pass removed an escaping backslash; the cut after a
  backslash leaked. Both were the *next instance* of the two classes above,
  which is why rev 4 changed the composition and the truncation order rather
  than adding cases 8 and 9.

### What this design still will not catch, and why that is acceptable

| residual | why | why acceptable |
|---|---|---|
| **Bare uniform-hex secrets** in free text (a 20-hex GitHub OAuth code with no `code=`, `key-<hex32>` Mailgun keys, plain hex API tokens) | shape-identical to git SHAs, digests, UUIDs and request ids, which every traceback and pip/docker log is full of; redacting them would make diagnostics unreadable | still caught when keyed (`code=`, `token=`, `?code=` in a URL) or prefixed (`dop_v1_<hex>` via pass 9); a hex token with no key and no prefix is the rarer issuance shape |
| A single unbroken run **longer than 256** characters | it is a payload (image data, an encoded file) far more often than a credential; the longest single-run vendor tokens seen are ~164 (`sk-proj-`) | JWTs (dot-joined, unbounded rule) and private keys (PEM rule) are the long credential shapes, and both are covered |
| A **bare** opaque segment inside a > 256-char slash-joined run that does not read as a route (a piece > 64 chars, or fewer than two plain-word pieces) | the payload bound must win over path splitting or JPEG base64 is destroyed (rev 1's defect) | prefixed (`sk-…`, `ghp_…`) and vendor-shaped credentials in such a run are still caught by passes 8-9, which run on the whole text before pass 10; routes up to 64 chars per piece with two words are still split |
| A quoted password containing a **raw newline** (`{"password": "hunter2\nmore"}`, invalid JSON) | a quoted value never spans a line, and it needs its closing quote | JSON and every log format escape newlines inside strings; the shape rules still see each line |
| Truncation: a single keyed value **longer than 16 KiB** that starts before the cap and ends after it | the redaction window is `max_bytes + 16384` characters | not a credential shape (PEM keys ~3 KB, JWTs a few KB, SAML assertions under ~16 KB); a larger K only moves the line, and the cost is linear in K |
| Truncation: the cut can land inside a marker and leave `[REDAC` | the cut is taken on redacted text | cosmetic — the marker's own text, never a credential fragment |
| base64url secrets whose `-`/`_` fall every < 10 characters | per-segment scoring (needed for pod names and hostnames) | expected gap between such characters in base64url is 32; measured examples all have a ≥ 10-char segment |
| A digitless dev password after a whitespace keyword (`--password hunter`) and short digit-bearing ones (`--token 1a2b`) | indistinguishable from `password reset`, `token v2` | `password=hunter`, `password: hunter`, `"password": "hunter"` are all still redacted (strong key with a separator) |
| Numeric one-time codes under `code` (`code=123456`) | indistinguishable from JSON-RPC and HTTP codes | single-use, minutes-lived, and a diagnostic from an untrusted server that quotes one gives the attacker nothing they did not already have |
| `Set-Cookie: a=b; c=d` — only the first pair | the value class stops at `;` (HEAD behaviour, unchanged) | the session pair is conventionally first; the residual is HEAD's |
| Low-entropy values under a non-OAuth `*_code=` (`error_code=super-secret`) | by design (pass 6) | the fail-closed direction: an opaque value is still caught by pass 10 |
| **False positive:** prefixed opaque *identifiers* — `req_011CfKTgoiRuc27pR2Po`, `cus_J1x2Yz3AbCd4Ef`, an Okta `aus…` id inside a *diagnostic* | shape-identical to `sk_live_…`; a denylist of secret prefixes fails open | a correlation id lost from a diagnostic is a support inconvenience; a token leaked is a compromise. The elicitation URL itself is untouched (defect 1) |
| **False positive:** MIME-wrapped base64 (76-column lines) and `data:` URIs under 256 chars | each line is a ≤ 256-char opaque run | one-run payloads (the common MCP `ImageContent` shape) survive; document as known |
| A plain word or number after a **weak** key (`credentials: include`, `auth: none`, `auth=s3cret` if it happens to be all letters) | `WEAK_SECRET_KEYS` keep a word or number by design | those keys name a mode or a scheme far more often than a secret; an all-letter password under `auth=` is the price, and `password=`/`secret=` remain strong |
| An **unterminated** quote in a bare form (`password="abc` with no closing quote) | a quoted value needs its closing quote and a bare value cannot start on one | malformed input; every surface redacts before it cuts, so it is never our truncation that produced it |
| A bare value that contains `&` (`password=a&b`) loses its tail; a bare value that begins with `=` (`key==value`) is not a value | `&` is a query separator and `==` a comparison, and the bare syntax cannot say otherwise | quoted forms carry both; `main` cut at `&` too |
| Hex signatures in signed URLs (`X-Amz-Signature=<64 hex>`) under keys not in `AUTH_SECRET_QUERY_KEYS` | uniform hex is never opaque; the key set is `main`'s | the same key set governed `main`; widening it is a one-line follow-up the implementer may take |
| **False positive:** an 8+ char punctuated non-word after a bare keyword (`session re-issued-twice`) | the price of catching `--password correct-horse-battery-staple` | rare in diagnostics; the corpus holds `session re-use` (6 chars) as the boundary |

## Changes

Every change names file, entity, action, reason. The exact code is in
`### Patch (measured)` below — it is the code every number in this plan was
measured against; implement it verbatim and then run the mutation table.

### `src/pmcp/auth.py`

- **`Span`, `REDACTED`** — add — the span type `(start, end, replacement)`
  and the marker constant (both imported by `policy.py`).
- **module constants `_OPAQUE_RUN_RE`, `_UNIFORM_HEX_RE`, `_SEGMENT_SPLIT_RE`,
  `_IDENTIFIER_JOINER_RE`, `_OPAQUE_MIN_SEGMENT`, `_OPAQUE_MIN_RUN`,
  `_OPAQUE_MAX_RUN`, `_OPAQUE_MIN_TRANSITIONS`, `_OPAQUE_MIN_TRANSITION_RATIO`,
  `_PREFIXED_TOKEN_RE`, `_VENDOR_SHAPE_RES`, `_CAMEL_BREAKER_RE`,
  `_DIGIT_RUN_RE`, `_LOWER_RUN_RE`, `_ROUTE_MAX_PIECE`, `_ROUTE_WORD_RE`,
  `_WORDY_PIECES_RE`** — add, after `_JWT_RE` — the shape layer's tunables,
  each with the false positive or negative it exists for in its comment.
- **`_char_class`, `_looks_opaque`, `_run_is_opaque`, `_reads_as_route`,
  `_run_spans`, `_shape_spans`** — add — the opaque-run scorer, the route
  test, and the shape pass as a span producer (JWT, vendor, prefixed, runs).
- **`_PLAIN_WORD_RE`, `_NUMBER_RE`, `_is_plain_word_or_number`,
  `_value_could_be_a_credential`, `_CODE_QUALIFIERS`** — add — the value
  gates (D2, D3, D5).
- **`_secret_key_alternation`, `_KEYWORD_SEP_RE`, `_KEYWORD_WS_RE`,
  `_BEARER_RE`, `_AUTHORIZATION_RE`, `_URL_RE`** — add — the keyword, Bearer,
  Authorization and URL patterns. `_KEYWORD_SEP_RE`'s quoted-value branches
  close at the quote (escape-aware, never across a newline); the `Bearer` and
  `Authorization` value classes stop at `"'()[]{}`.
- **`_keyword_sep_spans`, `_keyword_ws_spans`, `_bearer_spans`,
  `_authorization_spans`, `_url_component_spans`, `_url_spans`** — add —
  one span producer per pass, each reading the text it is given and nothing
  else; the URL producer yields component spans (userinfo → empty, secret
  query values → `[REDACTED]`, fragment → empty), never a rewritten URL.
- **`collect_redaction_spans`, `apply_redaction_spans`** — add (public; used
  by `policy.py`) — the union of every pass's spans, and the single-step
  applier with the merge rules in the Design section.
- **`redact_auth_url`** — **no change** (byte-identical to `main`; rev 4's
  `path_transform` parameter is gone with the rewrite it served).
- **`sanitize_auth_diagnostic`** — modify — the body becomes
  `apply_redaction_spans(text, collect_redaction_spans(text))` followed by
  the unchanged `text[:max_length]` cut. The `bearer` `re.sub`, the
  `secret_keys` containing-match `re.sub` and the `redact_url_match` closure
  are removed.
- `from urllib.parse import …, unquote` — add `unquote` (query keys are compared decoded, as `parse_qsl` did).
- `AUTH_DIAGNOSTIC_SECRET_KEYS`, `AUTH_SECRET_QUERY_KEYS`, `_JWT_RE` — no
  change.

### `src/pmcp/policy/policy.py`

- **imports** — modify — `from pmcp.auth import REDACTED, Span,
  apply_redaction_spans, collect_redaction_spans` replaces
  `sanitize_auth_diagnostic`.
- **`_REDACTION_WINDOW_SLACK = 16384`** — add, before
  `DEFAULT_REDACTION_PATTERNS` — the window slack, with the sizing argument
  in its comment.
- **`DEFAULT_REDACTION_PATTERNS`** — modify — as in D8 (`\btoken[ \t]*[:=]…`
  replaces `(bearer|token)[\s]+…`; `(?<![A-Za-z0-9:.])` on the `secret`
  group; `[ \t]*` around every separator).
- **`PolicyManager.truncate_output`** — modify — gains keyword-only
  `original_size: int | None = None`; when the original exceeded the cap the
  same `max_size - 100` cut and marker apply whatever the window's own size
  (slicing a short window is a no-op). No back-up of the cut (revs 2-3's
  `_TRAILING_*` machinery is not added).
- **`PolicyManager._truncation_marker`** — add (staticmethod) — the marker
  string, used by both paths.
- **`PolicyManager.redaction_spans`** — add — `collect_redaction_spans(output)`
  plus one span per operator-pattern match (value part after the first
  `:`/`=` that has a value after it, replacement `" [REDACTED]"`; whole match
  otherwise).
- **`PolicyManager.redact_secrets`** — modify — `apply_redaction_spans(output,
  self.redaction_spans(output))`. Never calls the engine on rewritten text.
- **`PolicyManager.process_output`** — modify — with `redact=True`: window =
  `output_str[: max_size + _REDACTION_WINDOW_SLACK]`; `spans =
  self.redaction_spans(window)`; `keep` = the character count of the first
  `max_size` bytes, extended to the end of every span that starts before it;
  `apply_redaction_spans(window[:keep], spans ending ≤ keep)`, then
  `truncate_output(…, original_size=raw_size)`. With `redact=False`,
  unchanged.

### `tests/test_redaction.py` (new)

Both corpora, the composition tests, and three property tests; bodies under
`## Test bodies`. Node ids, all validated with `--collect-only` this session
(129 tests, 10.9 s):

- `test_prose_survives_the_engine_byte_identical`,
  `test_prose_survives_the_policy_surface_byte_identical` — the prose corpus
  on both surfaces.
- `test_the_keyword_rule_needs_a_separator_or_a_credential_shaped_value`,
  `test_bearer_is_a_scheme_not_a_word`,
  `test_code_is_a_credential_only_in_the_oauth_sense` — the keyword rules'
  boundaries, one probe per clause.
- `test_the_policy_defaults_probe_stays_invisible_to_the_engine` — the C-13
  invariant.
- `test_the_engine_redacts_the_credential_and_keeps_its_surroundings[…]`,
  `test_the_policy_surface_redacts_the_credential_and_keeps_its_surroundings[…]`
  — 28 credential classes × 2 surfaces.
- `test_a_url_path_credential_is_redacted_in_diagnostics_not_in_the_url` —
  the elicitation-URL guard.
- `test_identifiers_that_are_not_credentials_are_kept`,
  `test_payload_sized_runs_are_data_not_credentials`,
  `test_a_slash_leading_payload_is_not_split_into_scored_pieces` — the shape
  layer's exemptions, including the JPEG/payload cases.
- `test_redaction_is_idempotent`,
  `test_redact_secrets_keeps_a_key_but_never_splits_inside_a_secret`,
  `test_the_engine_redacts_before_it_truncates`,
  `test_a_quoted_value_runs_to_its_closing_quote`,
  `test_an_earlier_pass_never_eats_the_boundary_a_later_pass_needs` — the
  rev 1-3 regression cases, all kept.
- `test_process_output_never_ends_on_a_partial_token`,
  `test_process_output_never_ends_on_a_partial_token_after_a_path`,
  `test_process_output_still_truncates_a_single_giant_run`,
  `test_truncation_never_leaks_a_quoted_multi_word_password`,
  `test_truncation_after_a_backslash_cannot_expose_a_password`,
  `test_the_redaction_window_reaches_past_the_cap` — truncation: the rev 1-3
  cases, the round-3 backslash case, and the window (a 4 000-char value
  straddling the cap).
- `test_no_pass_can_consume_a_boundary_another_pass_needs` — the round-3
  URL-eats-escape case, and a URL outside a keyed value keeping its host.
- **Properties:** `test_property_every_keyed_password_is_redacted_whole`
  (3 000 seeded passwords over every printable ASCII character plus
  `éßñ日本語🙂`, length 4-24; forms: `json.dumps` ASCII-escaped and not,
  single-quoted with `\'`/`\\` escaping, and for delimiter-free passwords
  `password=…` and `X-Api-Key: …`; 10 470 strings, 0 skipped, both surfaces;
  the probe is the first four characters of the value *as encoded*, skipped
  when the surrounding template already contains it);
  `test_property_truncation_never_exposes_a_keyed_password` (300 seeded
  passwords × the same forms, `process_output(redact=True)` at every
  `max_bytes` from two before the value's first byte to two past its last:
  1 056 values, 23 713 cuts, 17 377 inside a value — asserted per value —
  0 leaks); `test_property_prose_survives_byte_identical` (2 000 generated
  lines of words, statuses, identifiers, digests, ids, timestamps, JSON-RPC
  errors, `Bearer Word`, `--flag word`; a keyword is followed only by a word
  or a number, which is the documented boundary of D2);
  **`test_property_every_declared_key_is_redacted_in_every_form`** (rev 5:
  `_declared_secret_keys()` = `AUTH_DIAGNOSTIC_SECRET_KEYS` ∪ every name in
  the first alternation group of each `DEFAULT_REDACTION_PATTERNS` entry,
  `[_-]?` expanded, plus `private-key`/`privateKey`; 60 seeded values per
  key — half from every printable character, half from the bare-value
  alphabet (printable minus `"',;`), a third starting with punctuation, a
  third containing a space — in JSON, single-quoted, `key=value` and
  `Key: value` form; for a weak key a plain word/number value is skipped by
  design, for a strong key nothing is; 31 keys, 6 158 strings, 0 leaks on
  both surfaces). Per-key results (rev 6 generator: `&` is a bare
  delimiter too):

| key | weak? | json | single-quoted | key=value | header | leaks (engine/policy) |
|---|---|---|---|---|---|---|
| `access_token` | strong | 60 | 60 | 33 | 33 | 0 |
| `api-key` | strong | 60 | 60 | 38 | 38 | 0 |
| `api_key` | strong | 60 | 60 | 38 | 38 | 0 |
| `apikey` | strong | 60 | 60 | 36 | 36 | 0 |
| `assertion` | strong | 60 | 60 | 38 | 38 | 0 |
| `auth` | weak | 59 | 59 | 39 | 39 | 0 |
| `aws_access` | strong | 60 | 60 | 45 | 45 | 0 |
| `aws_secret` | strong | 60 | 60 | 44 | 44 | 0 |
| `client_secret` | strong | 60 | 60 | 40 | 40 | 0 |
| `code` | weak | 60 | 60 | 37 | 37 | 0 |
| `cookie` | strong | 60 | 60 | 32 | 32 | 0 |
| `credential` | weak | 60 | 60 | 41 | 41 | 0 |
| `credentials` | weak | 60 | 60 | 43 | 43 | 0 |
| `id_token` | strong | 60 | 60 | 38 | 38 | 0 |
| `jwt` | strong | 60 | 60 | 44 | 44 | 0 |
| `passwd` | strong | 60 | 60 | 32 | 32 | 0 |
| `password` | strong | 60 | 60 | 39 | 39 | 0 |
| `private-key` | strong | 60 | 60 | 39 | 39 | 0 |
| `privateKey` | strong | 60 | 60 | 40 | 40 | 0 |
| `private_key` | strong | 60 | 60 | 36 | 36 | 0 |
| `pwd` | strong | 60 | 60 | 44 | 44 | 0 |
| `refresh_token` | strong | 60 | 60 | 41 | 41 | 0 |
| `saml` | strong | 60 | 60 | 44 | 44 | 0 |
| `secret` | strong | 60 | 60 | 36 | 36 | 0 |
| `secret_access_key` | strong | 60 | 60 | 42 | 42 | 0 |
| `session` | strong | 60 | 60 | 38 | 38 | 0 |
| `set-cookie` | strong | 60 | 60 | 40 | 40 | 0 |
| `sid` | strong | 60 | 60 | 35 | 35 | 0 |
| `tenant-id` | strong | 60 | 60 | 38 | 38 | 0 |
| `tenant_id` | strong | 60 | 60 | 38 | 38 | 0 |
| `token` | strong | 60 | 60 | 35 | 35 | 0 |

keys: 31; strings checked: 6124; total leaks: 0

- **Rev 6:** `test_secrets_inside_a_url_query_are_redacted_whatever_the_key`,
  `test_a_literal_marker_in_the_input_is_not_a_shield`,
  `test_authorization_is_a_header_not_a_word` — the three blocking classes
  and the non-blocking Authorization/`==` cases, named;
  `test_property_every_collected_span_is_gone_from_the_output` (every span
  the redactor collected with a covering replacement, ≥ 4 chars, occurring
  once, is absent from the output, both surfaces; corpora: 28 credential
  texts, 1 716 keyed forms of 500 random passwords, 400 query-string texts
  with 24-char secrets under `AUTH_SECRET_QUERY_KEYS` *and* non-secret keys
  (some with userinfo, some wrapped in `password=`/JSON), 400 texts with a
  secret next to or inside quotes with a literal `[REDACTED]`; 2 544 texts,
  7 304 spans, 0 leaks);
  `test_property_the_window_edge_never_emits_an_unredacted_value`
  (`max_bytes=1000`, a PEM block sized to the edge, 80 pads, 13 straddling
  the edge — asserted — 0 leaks, output ≤ cap);
  `test_never_worse_than_main[…]` (42 inputs whose main-removed pieces —
  measured by running `main` @ `860636a`'s engine and policy over them,
  minus main's documented collateral `next`/`home`/`access_token` — must
  still vanish on both surfaces: 50 secrets).


### `tests/test_auth.py`

- **No change.** See the verdict above; 128 passed unchanged.

### Patch (measured)

`git diff` of the revised tree against `main` @ `860636a`, `src/` only. This
is the exact text the mutation table and every probe in this plan ran against
(`sha256` of the revised files: `auth.py 52897722…157d`, `policy.py
f2b95c6b…3cb9`).

```diff
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index f40ccbb..25babeb 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -12,7 +12,7 @@ from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
 from itertools import product
 from typing import Any, Literal
 from urllib.error import HTTPError
-from urllib.parse import parse_qsl, quote, urlparse, urlunparse
+from urllib.parse import parse_qsl, quote, urlparse, urlunparse, unquote
 from urllib.request import HTTPRedirectHandler, Request, build_opener
 
 import aiohttp
@@ -70,15 +70,24 @@ AUTH_DIAGNOSTIC_SECRET_KEYS = {
     "api_key",
     "apikey",
     "assertion",
+    "auth",
+    "aws_access",
+    "aws_secret",
     "client_secret",
     "code",
     "cookie",
+    "credential",
+    "credentials",
     "id_token",
     "jwt",
+    "passwd",
     "password",
+    "private_key",
+    "pwd",
     "refresh_token",
     "saml",
     "secret",
+    "secret_access_key",
     "session",
     "set-cookie",
     "sid",
@@ -87,12 +96,506 @@ AUTH_DIAGNOSTIC_SECRET_KEYS = {
     "token",
 }
 
+#: Keys that name a credential only sometimes. A plain word or number after
+#: them is kept: `credentials: include` (a fetch mode), `auth=basic`,
+#: `auth: none`, `{"code": -32601}` (every JSON-RPC error), `{"code":
+#: "not_found"}`. Anything else -- a token, `user:pass`, a path -- is
+#: redacted. Every other key in `AUTH_DIAGNOSTIC_SECRET_KEYS` is strong: its
+#: value is redacted whatever it looks like. `tests/test_redaction.py`
+#: derives its key-coverage property from both sets, so a key named here but
+#: not redacted in every form fails that test.
+WEAK_SECRET_KEYS = frozenset({"auth", "code", "credential", "credentials"})
+
 _JWT_RE = re.compile(
     r"(?<![A-Za-z0-9_-])"
     r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
     r"(?![A-Za-z0-9_-])"
 )
 
+# --- shape-based redaction (Consiliency/pmcp#234) -----------------------------
+#
+# The keyword rules below only fire on `<key><sep><value>`; everything else a
+# credential can look like is caught by *shape*: a run of token characters whose
+# character classes alternate the way random bytes do and English, identifiers,
+# hex digests and timestamps do not. tests/test_redaction.py holds the two
+# corpora this is tuned against: prose that must survive byte-identical, and
+# credentials that must not survive at all. Both are acceptance criteria; a
+# redactor tested on only one of them drifts toward redacting everything or
+# nothing.
+
+#: A maximal run of token characters. `.` is excluded on purpose so module
+#: paths, versions and hostnames split into short words (JWTs, which are
+#: dot-joined, have their own rule above). `/` and `+` are included so a classic
+#: base64 blob stays one run and is scored whole.
+_OPAQUE_RUN_RE = re.compile(r"[A-Za-z0-9_+/-]+={0,2}")
+#: Uniform hex, optionally `0x`-prefixed: git SHAs, digests, UUIDs, request ids
+#: and the `<Foo object at 0x7f3a2b1c4d50>` in every traceback. Kept.
+_UNIFORM_HEX_RE = re.compile(r"^(?:0[xX])?(?:[0-9a-f]+|[0-9A-F]+)$")
+_SEGMENT_SPLIT_RE = re.compile(r"[_+/-]+")
+#: `-` and `_` join identifiers (`pmcp-7d9f8b6c5-x2k9q`, `x86_64-linux-gnu`);
+#: a run containing them is scored segment by segment, never as a whole, or
+#: the concatenation of short hyphenated pieces reads as random.
+_IDENTIFIER_JOINER_RE = re.compile(r"[_-]")
+
+#: A segment (or a whole run) is "opaque" when it is at least this long ...
+_OPAQUE_MIN_SEGMENT = 10
+_OPAQUE_MIN_RUN = 16
+#: ... and at most this long. Longer is a payload, not a credential: base64
+#: image data and encoded files come back through `process_output` with
+#: redaction on, and no vendor issues a single unbroken token this long (JWTs
+#: are dot-joined and have their own rule; private keys are PEM blocks).
+_OPAQUE_MAX_RUN = 256
+#: ... and its lower/upper/digit classes change hands at least this many times,
+#: at more than this fraction of adjacent positions. camelCase identifiers
+#: (`ResourceServerAuthError`: 3/22) and timestamps (`20260922T101500Z`: 3/15)
+#: sit under the ratio; random alphanumerics sit far above it.
+_OPAQUE_MIN_TRANSITIONS = 3
+_OPAQUE_MIN_TRANSITION_RATIO = 0.25
+
+#: Short lowercase prefix, up to two short qualifiers, then a body of 12-256
+#: alphanumerics that mixes letters and digits: `sk-live-…`, `ghp_…`,
+#: `glpat-…`, `hf_…`, `npm_…`. A prefix is evidence in itself, so the body is
+#: held to a weaker test than a bare run (a letter and a digit rather than the
+#: transition score). Whole-alpha and whole-numeric bodies are NOT matched:
+#: `ghp_abcdefghijklmnop` is the probe tests/test_project_source_consent_policy.py
+#: uses to prove the *policy* defaults still apply, and it must stay invisible
+#: to this engine or that proof goes vacuous.
+_PREFIXED_TOKEN_RE = re.compile(
+    r"(?<![A-Za-z0-9_-])"
+    r"[a-z]{2,8}(?:[_-][a-z0-9]{1,8}){0,2}[_-]"
+    r"(?=[A-Za-z0-9]*[0-9])(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,256}"
+    r"(?![A-Za-z0-9_-])"
+)
+
+#: Documented fixed vendor shapes the transition score cannot see. This list is
+#: a supplement to the shape rules, not the defence: a prefix nobody listed is
+#: still caught above if its body is random. Each entry says why it is here.
+_VENDOR_SHAPE_RES = (
+    # AWS access key ids: a 4-letter family prefix + 16 upper-alnum. Uniformly
+    # upper-case with few digits, so the transition score does not fire.
+    re.compile(
+        r"(?<![A-Za-z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|APKA)"
+        r"[A-Z0-9]{16}(?![A-Za-z0-9])"
+    ),
+    # Slack tokens: `xox[abeprs]-` then numeric segments; only the last segment
+    # carries transitions, and on a short one the score misses.
+    re.compile(r"(?<![A-Za-z0-9])xox[abeprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9])"),
+    # Google API keys: `AIza` + 35 base64url; caught by shape too, listed so the
+    # whole key is one replacement.
+    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
+    # PEM private-key blocks: one replacement for the block, not one per line.
+    re.compile(
+        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
+        re.DOTALL,
+    ),
+)
+
+
+#: camelCase never puts two capitals after a lower-case letter; random
+#: mixed-case text does so within a few characters.
+_CAMEL_BREAKER_RE = re.compile(r"[a-z][A-Z]{2}")
+_DIGIT_RUN_RE = re.compile(r"[0-9]+")
+_LOWER_RUN_RE = re.compile(r"[a-z]+")
+
+
+def _char_class(char: str) -> int:
+    if char.isdigit():
+        return 2
+    return 1 if char.isupper() else 0
+
+
+def _looks_opaque(alnum: str, minimum: int) -> bool:
+    """Does an alphanumeric string read as random bytes rather than a name?
+
+    Each clause names the false positive it exists to prevent; the corpora in
+    tests/test_redaction.py hold the evidence.
+    """
+    length = len(alnum)
+    if length < minimum or _UNIFORM_HEX_RE.match(alnum):
+        return False
+    transitions = sum(
+        1 for a, b in zip(alnum, alnum[1:]) if _char_class(a) != _char_class(b)
+    )
+    ratio = transitions / (length - 1)
+    if transitions < _OPAQUE_MIN_TRANSITIONS or ratio <= _OPAQUE_MIN_TRANSITION_RATIO:
+        return False  # `ResourceServerAuthError`, `20260922T101500Z`, `sha256sum`
+    digit_runs = len(_DIGIT_RUN_RE.findall(alnum))
+    if digit_runs < 2:
+        # Identifiers embed ONE number (`X509Cert`, `Rsa2048Key`,
+        # `Oauth2ClientError`, `UnicodeDecodeError`); random text scatters
+        # digits. With at most one, demand the mixed-case signature camelCase
+        # cannot produce, and a ratio above what acronym-bearing names reach
+        # (`parseJSON2Dict`: 0.31, `toHTML5String`: 0.33).
+        return ratio > 0.35 and _CAMEL_BREAKER_RE.search(alnum) is not None
+    if length < _OPAQUE_MIN_RUN:
+        # Short, with two numbers: `s3bucket2024` still carries a whole word.
+        longest_word = max(len(run) for run in _LOWER_RUN_RE.findall(alnum + "a"))
+        return longest_word <= 4
+    return True
+
+
+def _run_is_opaque(run: str) -> bool:
+    if len(run) > _OPAQUE_MAX_RUN:
+        return False  # a payload (base64 image data, an encoded file)
+    alnum = "".join(c for c in run if c.isalnum())
+    if not alnum or _UNIFORM_HEX_RE.match(alnum):
+        return False
+    if _IDENTIFIER_JOINER_RE.search(run) is None and _looks_opaque(
+        alnum, _OPAQUE_MIN_RUN
+    ):
+        return True  # a bare alphanumeric or classic-base64 run, scored whole
+    return any(
+        _looks_opaque(segment, _OPAQUE_MIN_SEGMENT)
+        for segment in _SEGMENT_SPLIT_RE.split(run.rstrip("="))
+    )
+
+
+def _reads_as_route(run: str) -> bool:
+    """Is a long `/`-joined run a URL path rather than a base64 payload?
+
+    A route is short pieces, at least two of them plain words
+    (`/api/v1/organizations/acme/secrets/<id>/versions/latest`). Base64 puts
+    a `/` every ~64 characters at random, so a payload of any length has
+    pieces over `_ROUTE_MAX_PIECE` and, in practice, no two word pieces.
+    """
+    pieces = run.split("/")
+    if max(len(piece) for piece in pieces) > _ROUTE_MAX_PIECE:
+        return False
+    return sum(1 for piece in pieces if _ROUTE_WORD_RE.fullmatch(piece)) >= 2
+
+
+#: One replacement: ``text[start:end]`` becomes ``replacement``. Every pass
+#: reads the ORIGINAL text and returns spans; nothing is rewritten until
+#: `apply_redaction_spans` applies them all at once, so no pass can consume a
+#: boundary -- a closing quote, an escaping backslash -- that another pass
+#: needs (Consiliency/pmcp#234, revs 2-3: the `Bearer` pass ate a closing
+#: quote, the URL pass ate the backslash that escaped one).
+Span = tuple[int, int, str]
+
+REDACTED = "[REDACTED]"
+
+
+def _run_spans(start: int, run: str) -> list[Span]:
+    if len(run) > _OPAQUE_MAX_RUN and not _reads_as_route(run):
+        # A payload (JPEG base64 starts `/9j/` and carries a `/` every ~64
+        # characters): the bound applies to the whole run BEFORE any path
+        # splitting, or every piece between two slashes is scored on its own
+        # and an ordinary image comes back as `[REDACTED]/[REDACTED]/...`.
+        return []
+    if "/" in run and (run.startswith("/") or _WORDY_PIECES_RE.search(run)):
+        # A path: keep the route and replace only the credential-shaped
+        # pieces (`/v1/[REDACTED]/status`), so the reader still learns which
+        # endpoint failed. Base64 with `/` in it (an AWS secret key) has no
+        # leading slash and no words, and is scored -- and replaced -- whole.
+        spans: list[Span] = []
+        offset = start
+        for piece in run.split("/"):
+            if _run_is_opaque(piece):
+                spans.append((offset, offset + len(piece), REDACTED))
+            offset += len(piece) + 1
+        return spans
+    return [(start, start + len(run), REDACTED)] if _run_is_opaque(run) else []
+
+
+#: Two `/`-separated pieces that are plain lower-case words: a route, not a blob.
+#: For a run past `_OPAQUE_MAX_RUN`: the longest piece a route may have and
+#: the plain-word piece shape `_reads_as_route` counts.
+_ROUTE_MAX_PIECE = 64
+_ROUTE_WORD_RE = re.compile(r"[a-z]{3,}")
+_WORDY_PIECES_RE = re.compile(r"(?:^|/)[a-z]{3,}/(?:[^/]*/)*[a-z]{3,}(?:/|$)")
+
+
+def _shape_spans(text: str) -> list[Span]:
+    """Spans of every credential-shaped run in ``text``: JWTs, the vendor
+    supplement, prefixed tokens and scored opaque runs."""
+    spans: list[Span] = [(m.start(), m.end(), REDACTED) for m in _JWT_RE.finditer(text)]
+    for vendor_re in _VENDOR_SHAPE_RES:
+        spans.extend((m.start(), m.end(), REDACTED) for m in vendor_re.finditer(text))
+    spans.extend(
+        (m.start(), m.end(), REDACTED) for m in _PREFIXED_TOKEN_RE.finditer(text)
+    )
+    for m in _OPAQUE_RUN_RE.finditer(text):
+        spans.extend(_run_spans(m.start(), m.group(0)))
+    return spans
+
+
+#: A word (`bucket`, `Token`, `not_found`, `invalid_grant`) or a number (`401`,
+#: `-32601`, `0x80070005`), sentence punctuation allowed after it (`token:`,
+#: `bucket.`): the values prose and status payloads put after a keyword, and
+#: never what a credential looks like.
+_PLAIN_WORD_RE = re.compile(r"[A-Za-z_]+")
+_NUMBER_RE = re.compile(r"[+-]?[0-9]+|0[xX][0-9a-fA-F]+")
+
+
+def _is_plain_word_or_number(value: str) -> bool:
+    value = value.strip("\"'").rstrip(".:!?")
+    return bool(_PLAIN_WORD_RE.fullmatch(value) or _NUMBER_RE.fullmatch(value))
+
+
+def _value_could_be_a_credential(value: str) -> bool:
+    """The test a whitespace-separated keyword value must pass to be redacted.
+
+    Not a plain word or number, and either digit-bearing and 6+ characters
+    (`abc123def456`, `hunter2`) or punctuated and 8+ (`secret-bearer`,
+    `correct-horse-battery`). `token bucket`, `session expired`, `token v2`
+    and `code 401` all fail it; `--token abc123def456` passes.
+    """
+    value = value.strip("\"'")
+    if _is_plain_word_or_number(value):
+        return False
+    if any(c.isdigit() for c in value):
+        return len(value) >= 6
+    return len(value) >= 8
+
+
+#: `code` names a credential only in the OAuth sense -- bare (`code=`, the
+#: callback parameter) or under one of these qualifiers (`auth_code=`,
+#: `device_code=`). Under any other qualifier (`status_code=401`,
+#: `error_code=invalid_grant`, `exit_code=137`) it names a status, and its
+#: value is left to the shape rules, which still catch an opaque one. Even in
+#: the OAuth sense a plain word or number is kept: `{"code": -32601}` is every
+#: JSON-RPC error and `{"code": "not_found"}` every REST one.
+_CODE_QUALIFIERS = frozenset({"", "auth", "authorization", "oauth", "device", "user"})
+
+
+def _secret_key_alternation() -> str:
+    return "|".join(
+        [
+            *sorted(re.escape(key) for key in AUTH_DIAGNOSTIC_SECRET_KEYS),
+            r"api[_-]?key",
+            r"private[_-]?key",
+        ]
+    )
+
+
+#: `<identifier><sep><value>` where the identifier's LAST segment is a secret
+#: key. Last, not any: `token_type=Bearer`, `token_endpoint=` and `secret_arn=`
+#: name a type, an endpoint and an ARN, and the pre-#234 containing match
+#: (`unicode`, `encoded`, `tokenizer`) is how prose got mangled. Segments are
+#: `_`/`-` joined or camelCase (`accessToken`). The separator is `:` or `=`
+#: with optional quotes around key and value so JSON (`"password": "x"`) is
+#: covered -- a quoted value runs to its CLOSING quote, past any escaped one
+#: (`"a\\"hunter2"`), or the tail after the escape survives, and never past
+#: a newline (a JSON string cannot hold one). It needs its closing quote:
+#: the redactor sees whole values because every surface redacts BEFORE it
+#: cuts (`sanitize_auth_diagnostic`, and `PolicyManager.process_output` over a
+#: bounded window), so an unterminated value is the server's own text, not
+#: ours to guess at. Bare whitespace is NOT a separator here (see
+#: `_KEYWORD_WS_RE`); `==` is a comparison (`if token == expected`), not a
+#: separator. A bare value ends at whitespace, a quote or a list
+#: separator (`,`, `;`, or `&` -- a query string's) and at nothing else,
+#: except that it never ENDS on a closing bracket: `password=(Xk9mQ2vL)` is one value, punctuation and all,
+#: while the `}` of `{"password": [REDACTED]}` stays with the object.
+_KEYWORD_SEP_RE = re.compile(
+    r"(?P<key>(?P<qualifier>(?<![A-Za-z0-9:.])(?:[A-Za-z0-9]+[_-])*"
+    r"(?:(?-i:[a-z]+(?=[A-Z])))?)"
+    rf"(?P<name>{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?)"
+    r"(?P<sep>[\"']?[ \t]*[:=](?!=)[ \t]*)"
+    r"(?P<value>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'"
+    r"|[^\s\"',;&]*[^\s\"',;&)\]}])",
+    re.IGNORECASE,
+)
+
+#: A bare keyword (or `--keyword` flag) followed by whitespace and a value that
+#: could be a credential (`_value_could_be_a_credential`). This is what keeps
+#: `--token abc123def456` redacted without redacting the second word of
+#: `token bucket`, `secret ingredient` or `session expired`. A `param=value`
+#: after the keyword (`token expires_in=3600`) is not its value either. `code`
+#: never fires here: `exit code 137`, `status code 401`, `zip code 94105`.
+_KEYWORD_WS_RE = re.compile(
+    r"(?P<key>(?<![A-Za-z0-9_-])(?:--)?"
+    rf"(?P<name>{_secret_key_alternation()})s?)"
+    r"(?P<sep>[ \t]+)"
+    r"(?![A-Za-z_-]+=[^=])(?P<value>[^\s\"',;()\[\]{}]+)",
+    re.IGNORECASE,
+)
+
+#: `Bearer <token>` -- the HTTP scheme, so anything after it that is not a word
+#: is a token. Not `token_type=Bearer expires_in=3600` (bearer as a VALUE, the
+#: lookbehinds), not `Bearer realm="x"` (a challenge's own parameters, the
+#: lookahead), not `Missing bearer token` or `the bearer of bad news` (plain
+#: words, the callback). `(?<![A-Za-z0-9_-])` rather than `\b`: on main
+#: `\bbearer` fired inside `secret-bearer failed` and redacted `failed`. The
+#: value stops at a quote or bracket: `{"password": "hunter2 Bearer x"}` must
+#: keep its closing quote for the keyword pass, not lose it to this one.
+_BEARER_RE = re.compile(
+    r"(?<![=:\"'])(?<![=:\"'] )(?<![A-Za-z0-9_-])"
+    r"(?P<key>bearer[ \t]+)(?![A-Za-z_-]+=[^=])(?P<value>[^\s,;\"'()\[\]{}]+)",
+    re.IGNORECASE,
+)
+
+
+#: `Authorization: Bearer x`, `Authorization=x`, and JSON `"authorization":
+#: "Bearer x"` (the key may be quoted). A quoted value is redacted WHOLE
+#: between its quotes -- a header value can hold spaces (`Basic dXNl cg==`
+#: is malformed but leaks the same) and punctuation -- and a bare value is
+#: an optional HTTP auth scheme word (`Basic dXNl…` goes whole, as on main)
+#: then a run to whitespace, a quote or a list separator -- unless it is a plain word
+#: or number (`authorization: none`, `Authorization: required`), which is
+#: prose. The separator is on the same line: `Set the Authorization:\nheader
+#: first` is a sentence, not a header.
+_AUTHORIZATION_RE = re.compile(
+    r"authorization[\"']?[ \t]*[:=][ \t]*"
+    r"(?:(?P<quoted>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')"
+    r"|(?P<bare>(?:(?:bearer|basic|digest|negotiate|ntlm|token)[ \t]+)?"
+    r"[^\s,;\"']*[^\s,;\"')\]}]))",
+    re.IGNORECASE,
+)
+
+#: A URL in free text; trailing sentence punctuation is handed back.
+_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
+
+
+def _keyword_sep_spans(text: str) -> list[Span]:
+    spans: list[Span] = []
+    for match in _KEYWORD_SEP_RE.finditer(text):
+        name = match.group("name").lower()
+        if name in WEAK_SECRET_KEYS and _is_plain_word_or_number(match.group("value")):
+            continue
+        if name == "code":
+            qualifier = match.group("qualifier").rstrip("_-").lower()
+            if qualifier not in _CODE_QUALIFIERS:
+                continue
+        spans.append((match.start("value"), match.end("value"), REDACTED))
+    return spans
+
+
+def _keyword_ws_spans(text: str) -> list[Span]:
+    return [
+        (match.start("value"), match.end("value"), REDACTED)
+        for match in _KEYWORD_WS_RE.finditer(text)
+        if match.group("name").lower() != "code"
+        and _value_could_be_a_credential(match.group("value"))
+    ]
+
+
+def _bearer_spans(text: str) -> list[Span]:
+    return [
+        (match.start("value"), match.end("value"), REDACTED)
+        for match in _BEARER_RE.finditer(text)
+        if not _is_plain_word_or_number(match.group("value"))
+    ]
+
+
+def _authorization_spans(text: str) -> list[Span]:
+    spans: list[Span] = []
+    for match in _AUTHORIZATION_RE.finditer(text):
+        if match.group("quoted") is not None:
+            spans.append((match.start("quoted") + 1, match.end("quoted") - 1, REDACTED))
+        elif not _is_plain_word_or_number(match.group("bare")):
+            spans.append((match.start("bare"), match.end("bare"), REDACTED))
+    return spans
+
+
+def _url_component_spans(base: int, raw_url: str) -> list[Span]:
+    """The parts of one URL `redact_auth_url` would strip or redact, as spans
+    over the text the URL sits in: the userinfo (dropped), each value under an
+    `AUTH_SECRET_QUERY_KEYS` key (`[REDACTED]`), and the fragment (dropped).
+
+    Spans, not a rewrite: a rewritten URL would be one span containing the
+    query, and every keyed or shape span inside that query (`?pwd=hunter2`,
+    `?access=ghp_…`, a JWT under a harmless key) would be dropped as
+    "contained" -- rev 5's regression against main. The path is left to the
+    shape pass, which sees it like any other text.
+    """
+    spans: list[Span] = []
+    scheme_end = raw_url.find("://")
+    netloc_start = scheme_end + 3 if scheme_end >= 0 else 0
+    netloc_end = len(raw_url)
+    for stop in "/?#":
+        index = raw_url.find(stop, netloc_start)
+        if index >= 0:
+            netloc_end = min(netloc_end, index)
+    at = raw_url.rfind("@", netloc_start, netloc_end)
+    if at >= 0:
+        spans.append((base + netloc_start, base + at + 1, ""))
+    fragment = raw_url.find("#")
+    if fragment >= 0:
+        spans.append((base + fragment, base + len(raw_url), ""))
+    query_start = raw_url.find("?")
+    if query_start >= 0:
+        query_end = fragment if fragment > query_start else len(raw_url)
+        position = query_start + 1
+        for pair in raw_url[query_start + 1 : query_end].split("&"):
+            key, equals, value = pair.partition("=")
+            if equals and value and unquote(key).lower() in AUTH_SECRET_QUERY_KEYS:
+                value_start = position + len(key) + 1
+                spans.append(
+                    (base + value_start, base + value_start + len(value), REDACTED)
+                )
+            position += len(pair) + 1
+    return spans
+
+
+def _url_spans(text: str) -> list[Span]:
+    spans: list[Span] = []
+    for match in _URL_RE.finditer(text):
+        raw_url = match.group(0)
+        while raw_url and raw_url[-1] in ").,;":
+            raw_url = raw_url[:-1]
+        spans.extend(_url_component_spans(match.start(), raw_url))
+    return spans
+
+
+def collect_redaction_spans(text: str) -> list[Span]:
+    """Every redaction the engine would make to ``text``, as spans over it.
+
+    Each pass reads the same, unmodified ``text``. The order of the list does
+    not matter: `apply_redaction_spans` merges overlaps.
+    """
+    return [
+        *_url_spans(text),
+        *_keyword_sep_spans(text),
+        *_authorization_spans(text),
+        *_bearer_spans(text),
+        *_keyword_ws_spans(text),
+        *_shape_spans(text),
+    ]
+
+
+def apply_redaction_spans(text: str, spans: list[Span]) -> str:
+    """Apply ``spans`` to ``text`` in one pass.
+
+    Overlaps are resolved conservatively. A span inside another is dropped
+    only when the outer replacement *covers* it -- `[REDACTED]` or the empty
+    string (a URL inside a quoted password becomes `[REDACTED]` with the
+    password); under any other replacement the two are merged and their union
+    becomes `[REDACTED]`, as are two spans that partially overlap. For
+    identical ranges `[REDACTED]` beats any other replacement.
+
+    Every `[REDACTED]` already in the text is itself a span. An existing
+    marker therefore merges with whatever touches it -- `password=hunter2
+    [REDACTED]` becomes `password=[REDACTED]` rather than surviving because a
+    downstream server wrote the marker -- and a marker nothing touches is
+    replaced by itself, which is what keeps every surface idempotent
+    (`password=[REDACTED]` and `password= [REDACTED]` are fixed points).
+    """
+    markers: list[Span] = [
+        (m.start(), m.end(), REDACTED) for m in re.finditer(re.escape(REDACTED), text)
+    ]
+    candidates = [*spans, *markers]
+    ordered = sorted(
+        (span for span in candidates if span[0] < span[1]),
+        key=lambda span: (span[0], -span[1], span[2] != REDACTED),
+    )
+    merged: list[Span] = []
+    for start, end, replacement in ordered:
+        if merged and start < merged[-1][1]:
+            previous_start, previous_end, previous_replacement = merged[-1]
+            if end <= previous_end and previous_replacement in (REDACTED, ""):
+                continue
+            merged[-1] = (previous_start, max(end, previous_end), REDACTED)
+            continue
+        merged.append((start, end, replacement))
+    pieces: list[str] = []
+    position = 0
+    for start, end, replacement in merged:
+        pieces.append(text[position:start])
+        pieces.append(replacement)
+        position = end
+    pieces.append(text[position:])
+    return "".join(pieces)
+
 
 def redact_auth_url(url: str) -> str:
     """Strip URL userinfo and redact auth-bearing query values."""
@@ -576,35 +1079,10 @@ def sanitize_url_elicitation_url(
 def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
     """Return a display-safe diagnostic string for auth failures."""
     text = str(value)
-
-    def redact_url_match(match: re.Match[str]) -> str:
-        raw_url = match.group(0)
-        suffix = ""
-        while raw_url and raw_url[-1] in ").,;":
-            suffix = raw_url[-1] + suffix
-            raw_url = raw_url[:-1]
-        return redact_auth_url(raw_url) + suffix
-
-    text = re.sub(r"https?://[^\s\"'<>]+", redact_url_match, text)
-    text = re.sub(
-        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
-        r"\1[REDACTED]",
-        text,
-    )
-    text = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
-    secret_keys = "|".join(
-        [
-            *[re.escape(key) for key in AUTH_DIAGNOSTIC_SECRET_KEYS],
-            r"api[_-]?key",
-        ]
-    )
-    text = re.sub(
-        rf"(?i)\b([A-Za-z0-9_-]*(?:{secret_keys})[A-Za-z0-9_-]*)"
-        r"([\s:=]+)([A-Za-z0-9._~+/=-]{3,})",
-        r"\1\2[REDACTED]",
-        text,
-    )
-    text = _JWT_RE.sub("[REDACTED]", text)
+    # Every pass reads the same text; the replacements land in one step
+    # (Consiliency/pmcp#234). The cut is taken afterwards, so truncation can
+    # only ever shorten `[REDACTED]`, never expose a prefix.
+    text = apply_redaction_spans(text, collect_redaction_spans(text))
     return text if max_length is None else text[:max_length]
 
 
diff --git a/src/pmcp/policy/policy.py b/src/pmcp/policy/policy.py
index cac2702..d9fe620 100644
--- a/src/pmcp/policy/policy.py
+++ b/src/pmcp/policy/policy.py
@@ -22,7 +22,12 @@ from pmcp.types import (
     ServerPolicy,
     ToolPolicy,
 )
-from pmcp.auth import sanitize_auth_diagnostic
+from pmcp.auth import (
+    REDACTED,
+    Span,
+    apply_redaction_spans,
+    collect_redaction_spans,
+)
 
 if TYPE_CHECKING:
     # Annotation only. `pmcp.manifest`'s package `__init__` imports the loader and
@@ -44,12 +49,29 @@ _ListPolicy = ServerPolicy | ToolPolicy | ResourcePolicy | PromptPolicy
 #: would otherwise return the wrong limit or raise at runtime.
 _LimitField = Literal["max_tools_per_server", "max_output_bytes", "max_output_tokens"]
 
+#: `process_output` redacts BEFORE it truncates, over the first
+#: ``max_bytes + _REDACTION_WINDOW_SLACK`` characters of the output, so the cut
+#: never lands inside a credential the redactor has not yet seen whole
+#: (Consiliency/pmcp#234). The slack is the longest keyed value the redactor
+#: can be asked to match across the cut: a 4096-bit RSA PEM block is ~3.2 KB,
+#: a JWT with generous claims a few KB, a SAML assertion under ~16 KB. The
+#: cost is bounded by it (~1 ms per KB on this host), and the residual is a
+#: single value longer than the slack straddling the cap -- not a credential
+#: shape. The window's far edge is never emitted: see `process_output`.
+_REDACTION_WINDOW_SLACK = 16384
+
 DEFAULT_REDACTION_PATTERNS = [
-    # Common secret patterns (case-insensitive)
-    r"(api[_-]?key|apikey)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
-    r"(secret|password|passwd|pwd)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
-    r"(bearer|token)[\s]+[a-zA-Z0-9._-]+",
-    r"(aws_secret|aws_access)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
+    # Common secret patterns (case-insensitive). The separator is `:` or `=`
+    # on the same line: `[ \t]*`, not `[\s]*`, which let `token:\nthe` (a
+    # sentence ending in the keyword) redact the first word of the next line.
+    r"(api[_-]?key|apikey)[ \t]*[:=][ \t]*[\"']?([^\s\"']+)",
+    # Not after `:` or `.`: `arn:…:secret:Name` names a secret, it is not one.
+    r"(?<![A-Za-z0-9:.])(secret|password|passwd|pwd)[ \t]*[:=][ \t]*[\"']?([^\s\"']+)",
+    # `token` needs a real separator: the pre-#234 `(bearer|token)\s+…` form
+    # redacted the word after "token" in prose ("token bucket"). Bearer values
+    # are handled unconditionally by `sanitize_auth_diagnostic`.
+    r"\btoken[ \t]*[:=][ \t]*[\"']?([^\s\"']+)",
+    r"(aws_secret|aws_access)[ \t]*[:=][ \t]*[\"']?([^\s\"']+)",
     r"\bsk-[A-Za-z0-9_-]{6,}\b",
     r"\bghp_[A-Za-z0-9_]{10,}\b",
     r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
@@ -666,18 +688,31 @@ class PolicyManager:
         return self._composed_limit("max_output_tokens")
 
     def truncate_output(
-        self, output: str, max_bytes: int | None = None
+        self,
+        output: str,
+        max_bytes: int | None = None,
+        *,
+        original_size: int | None = None,
     ) -> tuple[str, bool, int]:
         """
         Truncate output to max size.
 
+        ``original_size`` is the size to report when ``output`` is already a
+        window of a larger text (see `process_output`); the marker then names
+        the real size, and a window that fits under the cap but dropped text
+        still counts as truncated and is cut where any text is.
+
         Returns: (result, truncated, original_size)
         """
         max_size = max_bytes or self.get_max_output_bytes()
-        original_size = len(output.encode("utf-8"))
+        if original_size is None:
+            original_size = len(output.encode("utf-8"))
 
         if original_size <= max_size:
             return (output, False, original_size)
+        # A window that already fits still takes the same cut (`max_size -
+        # 100`, room for the marker) so the cap holds wherever the text came
+        # from; slicing a short window is a no-op.
 
         # Truncate to max bytes, being careful with UTF-8
         encoded = output.encode("utf-8")
@@ -687,29 +722,46 @@ class PolicyManager:
         truncated_str = truncated_bytes.decode("utf-8", errors="ignore")
 
         # Add truncation indicator
-        truncated_str += (
-            f"\n\n[... OUTPUT TRUNCATED: {original_size} bytes -> {max_size} bytes ...]"
-        )
+        truncated_str += self._truncation_marker(original_size, max_size)
 
         return (truncated_str, True, original_size)
 
+    @staticmethod
+    def _truncation_marker(original_size: int, max_size: int) -> str:
+        return (
+            f"\n\n[... OUTPUT TRUNCATED: {original_size} bytes -> {max_size} bytes ...]"
+        )
+
     def redact_secrets(self, output: str) -> str:
-        """Redact secrets from output."""
-        result = sanitize_auth_diagnostic(output, max_length=None)
+        """Redact secrets from output.
 
-        for regex in self._redaction_regexes:
+        The engine's spans and the operator's pattern spans are all collected
+        over the same, unmodified ``output`` and applied in one step, so the
+        operator's patterns never see -- and never depend on -- the engine's
+        rewriting (Consiliency/pmcp#234).
+        """
+        return apply_redaction_spans(output, self.redaction_spans(output))
 
-            def replace_match(match: re.Match[str]) -> str:
+    def redaction_spans(self, output: str) -> list[Span]:
+        """Every redaction either surface would make to ``output``, as spans."""
+        spans: list[Span] = collect_redaction_spans(output)
+        for regex in self._redaction_regexes:
+            for match in regex.finditer(output):
                 full_match = match.group(0)
-                # Find the separator (: or =)
+                # A `key<sep>value` match keeps its key: split at the first
+                # separator (: or =) that has a value after it. A separator
+                # with nothing but separators after it is base64 padding
+                # (`dXNlcjpwYXNzd29yZA==`), and splitting there kept the whole
+                # secret and replaced the `=`.
                 for i, char in enumerate(full_match):
-                    if char in ":=":
-                        return full_match[: i + 1] + " [REDACTED]"
-                return "[REDACTED]"
-
-            result = regex.sub(replace_match, result)
-
-        return result
+                    if char in ":=" and full_match[i + 1 :].strip(" \t:="):
+                        spans.append(
+                            (match.start() + i + 1, match.end(), " " + REDACTED)
+                        )
+                        break
+                else:
+                    spans.append((match.start(), match.end(), REDACTED))
+        return spans
 
     def process_output(
         self,
@@ -731,11 +783,38 @@ class PolicyManager:
 
         raw_size = len(output_str.encode("utf-8"))
 
-        # Truncate first
-        truncated_str, truncated, _ = self.truncate_output(output_str, max_bytes)
-
-        # Redact if requested
-        final_str = self.redact_secrets(truncated_str) if redact else truncated_str
+        if redact:
+            # Redact BEFORE the cut, over a window that reaches past the cap
+            # by `_REDACTION_WINDOW_SLACK`: the redactor then sees every value
+            # the cut could land in whole, and the cut can only ever shorten a
+            # `[REDACTED]` (Consiliency/pmcp#234). Cutting first left the
+            # first characters of a credential -- a shape no rule recognises.
+            #
+            # Invariant: no character from past the cap is emitted except
+            # inside a span. The window's far edge is a second cut, and it is
+            # not redacted either; so the text kept is the cap, extended only
+            # to the end of any span that starts before it. Rev 5 applied the
+            # spans to the whole window and returned it when redaction had
+            # shrunk it under the cap -- edge included.
+            max_size = max_bytes or self.get_max_output_bytes()
+            window = output_str[: max_size + _REDACTION_WINDOW_SLACK]
+            spans = self.redaction_spans(window)
+            keep = len(
+                window.encode("utf-8")[:max_size].decode("utf-8", errors="ignore")
+            )
+            for start, end, _ in sorted(spans):
+                if start < keep < end:
+                    keep = end
+            head = window[:keep]
+            final_str, truncated, _ = self.truncate_output(
+                apply_redaction_spans(
+                    head, [span for span in spans if span[1] <= keep]
+                ),
+                max_bytes,
+                original_size=raw_size,
+            )
+        else:
+            final_str, truncated, _ = self.truncate_output(output_str, max_bytes)
 
         # Generate summary if truncated
         summary: str | None = None
```

## Test bodies

`tests/test_redaction.py`, verbatim (sha256 `02145a79…5dd3`; 129 tests; ruff
clean):

```python
"""Both directions of secret redaction, pinned together (Consiliency/pmcp#234).

The redactor stands between a downstream server's error text and a
prompt-injectable context window. A false negative puts a credential into that
window; a false positive teaches readers that `[REDACTED]` means nothing. So a
redactor tested only on secrets gets tuned until it redacts everything, and one
tested only on prose gets tuned until it redacts nothing. This file holds both
corpora and ranks them equally: `PROSE` must survive byte-identical, and every
`CREDENTIALS` entry must vanish with its surroundings intact.

Both surfaces are covered -- `sanitize_auth_diagnostic` (the engine, used
directly by the client manager, the CLI and the doctor) and
`PolicyManager.redact_secrets` (the engine plus the operator's patterns).
"""

from __future__ import annotations

import base64
import json
import random
import re
import string
import uuid

import pytest

from pmcp.auth import (
    AUTH_DIAGNOSTIC_SECRET_KEYS,
    AUTH_SECRET_QUERY_KEYS,
    REDACTED,
    WEAK_SECRET_KEYS,
    collect_redaction_spans,
    redact_auth_url,
    sanitize_auth_diagnostic,
)
from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager

# --------------------------------------------------------------------------- #
# The prose corpus. Every line is text a downstream server, pip, git, httpx or
# the interpreter has produced or could produce, and none of it is a credential.
# Each keyword the pre-#234 rule mangled appears at least once in the position
# that mangled it. Extend it when a false positive is found; never trim it to
# make a rule pass.
# --------------------------------------------------------------------------- #

PROSE = """\
The token bucket rate limiter refused the call; the secret ingredient is
patience. Your session expired, so the cookie consent banner reappeared and the
password reset flow sent a status code 401 back. The tokenizer choked on unicode
input and the encoded payload was base64. Set the PMCP_FEEDBACK_TOKEN environment
variable to enable submission; the API key is read from the env store. Keys are
rotated weekly. A secret to good code is small functions. Missing bearer token:
the bearer of bad news said a Bearer Token is required. Session re-use is off.

Traceback (most recent call last):
  File "/home/u/.venv/lib/python3.10/site-packages/httpx/_client.py", line 1013, in send
    response = self._send_handling_auth(
  File "/home/u/code/pmcp/src/pmcp/client/manager.py", line 2641, in _run_transport
    raise ResourceServerAuthError("invalid_token", str(exc)) from exc
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0
httpx.HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.example.com/v1/tools'
<pmcp.client.manager.ClientManager object at 0x7f3a2b1c4d50> pod pmcp-7d9f8b6c5-x2k9q
JSONDecodeError SSLCertVerificationError IPv6Address Ed25519PrivateKey X509Cert
Rsa2048Key Oauth2ClientError Base64UrlEncoder Sha256HashAlgorithm parseJSON2Dict

status_code=401 error_code=invalid_grant token_type=Bearer expires_in=3600
token_endpoint=https://auth.example/oauth/token code=404 token v2 is out
{"code": -32601, "message": "Method not found", "data": {"code": "not_found"}}
{"code": "not_found", "message": "no such tool", "request_id": "550e8400-e29b-41d4-a716-446655440000"}
WWW-Authenticate: Bearer realm="api", error="insufficient_scope", scope="read write"
secret_arn=arn:aws:secretsmanager:us-east-1:123456789012:secret:MySecret-a1b2c3
exit code 137; error code 0x80070005; zip code 94105; status code 503
error_code=AADSTS50011 sqlstate_code=42P01 error_codes=[50011] reason_code=E-1234

commit 3843d2f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6 (HEAD -> main, origin/main)
Author: Example <dev@example.test>
Date:   2026-09-23T04:12:00+00:00
    docs(plans): plan CONSENT and PKGID (#239)
sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
Successfully installed pmcp-1.19.2 httpx-0.27.0 anyio-4.4.0 exceptiongroup-1.2.1
Downloading pmcp-1.19.2-py3-none-any.whl (312 kB) for x86_64-linux-gnu
TLS: ECDHE-RSA-AES256-GCM-SHA384 / TLS_AES_128_GCM_SHA256 negotiated at 20260923T041200Z
tests/test_redaction.py::test_s11_an_approved_project_policy_cannot_widen_the_user_policy PASSED
detailed-234-redactor-shape-based-20260922-1130.md build-20260922123456 s3bucket2024
[REDACTED] [REDACTED_EMAIL] ghp_ sk- xoxb- AKIA
"""

# --------------------------------------------------------------------------- #
# The credential corpus: (label, text, must_vanish, must_survive). The label is
# the CLASS the entry stands for, not the vendor; a vendor nobody listed is
# still caught if its shape is here. The four the issue measured come first.
#
# Samples deliberately do NOT match GitHub secret-scanning detectors (a Stripe
# key with 24 body characters, a DigitalOcean token with 64 hex, a Slack token
# with a numeric segment and a mixed-case body): push protection rejects a
# commit that carries one, however synthetic. Each sample keeps the SHAPE the
# rule needs and nothing more; the Slack rule is exercised by the issue's own
# `xoxb-2444-2444-abcdefghijklmnop`, which the detector leaves alone.
# --------------------------------------------------------------------------- #

CREDENTIALS: list[tuple[str, str, str, list[str]]] = [
    (
        "credential-in-url-path",
        "failed to fetch https://api.example.com/v1/sk-live-abc123def456/status",
        "sk-live-abc123def456",
        ["failed to fetch https://api.example.com/v1/", "/status"],
    ),
    (
        "aws-access-key-id",
        "unexpected value AKIAIOSFODNN7EXAMPLE in the request",
        "AKIAIOSFODNN7EXAMPLE",
        ["unexpected value", "in the request"],
    ),
    (
        "prefixed-random-body",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "16C7e42F292c6912E7710c838347Ae178B4a",
        [],
    ),
    (
        "slack-documented-shape",
        "xoxb-2444-2444-abcdefghijklmnop",
        "2444-2444-abcdefghijklmnop",
        [],
    ),
    (
        "bare-random-alnum",
        "bare 4eC39HqLyjWDarjtT1zdp7dc here",
        "4eC39HqLyjWDarjtT1zdp7dc",
        ["bare", "here"],
    ),
    (
        "base64-with-slashes",
        "aws secret wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY leaked",
        "wJalrXUtnFEMI",
        ["aws secret", "leaked"],
    ),
    (
        "base64-padded",
        "basic dXNlcjpwYXNzd29yZA== auth",
        "dXNlcjpwYXNzd29yZA",
        ["basic", "auth"],
    ),
    (
        "prefixed-hex-body",
        "dop_v1_9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822c",
        "9f86d081884c7d659a2feaa0c55ad015",
        [],
    ),
    (
        "prefixed-short-sample",
        "sk_test_4eC39HqLyjWDar7dc",
        "4eC39HqLyjWDar7dc",
        [],
    ),
    (
        "prefixed-low-entropy-with-digits",
        "sk-abcdef123456",
        "abcdef123456",
        [],
    ),
    (
        "prefixed-long-body",
        "sk-proj-Ab3dEf6GhI9jKl2MnO5pQr8StU1vWx4Yz7AbCdEfGhIjKlMnOpQrStUvWxYz",
        "Ab3dEf6GhI9jKl2MnO5pQr8StU1vWx4Yz7AbCdEfGhIjKlMnOpQrStUvWxYz",
        [],
    ),
    (
        "google-api-key",
        "AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBxY",
        "9tSrke72PouQMnMX",
        [],
    ),
    (
        "webhook-path-keeps-route",
        "https://hooks.example/services/T0123ABCD/B0123ABCD/a1B2c3D4e5F6g7H8i9J0k1L2",
        "a1B2c3D4e5F6g7H8i9J0k1L2",
        ["https://hooks.example/services/"],
    ),
    (
        "pem-block",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ\n-----END RSA PRIVATE KEY-----",
        "MIIEow",
        [],
    ),
    (
        "json-quoted-value",
        '{"password": "hunter2"}',
        "hunter2",
        ['"password"'],
    ),
    (
        "flag-with-shaped-value",
        "--token abc123def456 --password hunter2",
        "abc123def456",
        ["--token", "--password"],
    ),
    (
        "flag-with-passphrase",
        "--password correct-horse-battery-staple",
        "correct-horse-battery-staple",
        ["--password"],
    ),
    (
        "camelcase-key",
        "accessToken=AbC123dEf456GhI",
        "AbC123dEf456GhI",
        ["accessToken="],
    ),
    (
        "oauth-code-bare",
        "code=super-secret",
        "super-secret",
        ["code="],
    ),
    (
        "oauth-code-qualified",
        "auth_code=SplxlOBeZQQYbYS6WxSbIA",
        "SplxlOBeZQQYbYS6WxSbIA",
        ["auth_code="],
    ),
    (
        "oauth-code-hex",
        "code=a1b2c3d4e5f6a7b8c9d0",
        "a1b2c3d4e5f6a7b8c9d0",
        ["code="],
    ),
    (
        "keyword-colon-low-entropy",
        "password: hunter2",
        "hunter2",
        ["password:"],
    ),
    (
        "header-with-qualifier",
        "X-Auth-Token: abc123",
        "abc123",
        ["X-Auth-Token:"],
    ),
    (
        "cookie-header",
        "Set-Cookie: session=abc; Path=/",
        "abc",
        ["Set-Cookie:", "Path=/"],
    ),
    (
        "session-id",
        "session=013G8iK4noj1iNbSTqJVFEX6",
        "013G8iK4noj1iNbSTqJVFEX6",
        ["session="],
    ),
    (
        "bearer-header",
        "Authorization: Bearer abc.def",
        "abc.def",
        ["Authorization:"],
    ),
    (
        "bearer-bare",
        "sent Bearer test-token",
        "test-token",
        ["sent Bearer"],
    ),
    (
        "jwt",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
        "eyJzdWIiOiJzZWNyZXQifQ",
        [],
    ),
]

TOKEN = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"


def _engine(text: str) -> str:
    return sanitize_auth_diagnostic(text, max_length=None)


def _policy(text: str) -> str:
    return PolicyManager().redact_secrets(text)


# === the prose direction ================================================== #


def test_prose_survives_the_engine_byte_identical() -> None:
    assert _engine(PROSE) == PROSE


def test_prose_survives_the_policy_surface_byte_identical() -> None:
    assert _policy(PROSE) == PROSE


def test_the_keyword_rule_needs_a_separator_or_a_credential_shaped_value() -> None:
    """`token bucket` is prose; `--token abc123def456` is a flag with a secret.

    The pre-#234 rule treated whitespace as a separator, so any 3+ letter word
    after a keyword vanished. `the secret to good code` survived only because
    `to` is two letters -- an accident, not a guard.
    """
    assert _engine("token bucket rate limiting is enabled") == (
        "token bucket rate limiting is enabled"
    )
    assert _engine("the secret ingredient is love") == "the secret ingredient is love"
    assert _engine("session expired, password reset sent") == (
        "session expired, password reset sent"
    )
    assert _engine("--token abc123def456") == "--token [REDACTED]"
    assert _engine("token secret-bearer failed") == "token [REDACTED] failed"


def test_bearer_is_a_scheme_not_a_word() -> None:
    """`Bearer <token>` is redacted; `bearer token` (the phrase) is not.

    On main `\\bbearer` also fired *inside* `secret-bearer failed` and redacted
    `failed` -- the word after the credential rather than the credential. A
    hyphenated word that ends in `bearer` is a word, not the scheme: what
    follows `secret-bearer` or `non-bearer` is not a token.
    """
    assert _engine("Missing bearer token") == "Missing bearer token"
    assert _engine("the bearer of bad news") == "the bearer of bad news"
    assert _engine("Bearer Token is required") == "Bearer Token is required"
    assert _engine("Bearer test-token") == "Bearer [REDACTED]"
    assert _engine("Bearer hunter2") == "Bearer [REDACTED]"
    assert _engine("secret-bearer hunter2") == "secret-bearer hunter2"
    assert _engine("non-bearer 2024-01-01 report") == "non-bearer 2024-01-01 report"
    assert _engine('Bearer realm="api", error="x"') == 'Bearer realm="api", error="x"'
    assert _engine("token_type=Bearer expires_in=3600") == (
        "token_type=Bearer expires_in=3600"
    )


def test_code_is_a_credential_only_in_the_oauth_sense() -> None:
    """Bare `code=` is the OAuth callback parameter; `status_code=` is a status.

    JSON-RPC (`{"code": -32601}`) is every MCP error and REST (`{"code":
    "not_found"}`) every other one, so even bare `code` keeps a word or number.
    """
    assert _engine('{"code": -32601, "message": "x"}') == (
        '{"code": -32601, "message": "x"}'
    )
    assert _engine('{"code": "not_found"}') == '{"code": "not_found"}'
    assert _engine("status_code=401 error_code=invalid_grant") == (
        "status_code=401 error_code=invalid_grant"
    )
    assert _engine("code=404 exit code 137") == "code=404 exit code 137"
    assert _engine("code=super-secret") == "code=[REDACTED]"
    assert _engine("auth_code=SplxlOBeZQQYbYS6WxSbIA") == "auth_code=[REDACTED]"
    assert _engine("device_code=a1b2c3d4e5f6a7b8c9d0") == "device_code=[REDACTED]"


def test_the_policy_defaults_probe_stays_invisible_to_the_engine() -> None:
    """`ghp_abcdefghijklmnop` proves the policy DEFAULTS apply (SECURITY.md C-13).

    Those proofs (`tests/test_project_source_consent_policy.py`,
    `tests/test_trust_boundaries_e2e.py`) assert the probe is redacted *because
    a default pattern is still present*. If the engine ever caught it too they
    would pass with the defaults dropped. The probe is deliberately whole-alpha
    after its prefix; keep it that way and keep the engine blind to it.
    """
    assert _engine("ghp_abcdefghijklmnop") == "ghp_abcdefghijklmnop"
    assert "ghp_abcdefghijklmnop" not in _policy("ghp_abcdefghijklmnop")


# === the credential direction ============================================= #


@pytest.mark.parametrize(
    ("text", "must_vanish", "must_survive"),
    [entry[1:] for entry in CREDENTIALS],
    ids=[entry[0] for entry in CREDENTIALS],
)
def test_the_engine_redacts_the_credential_and_keeps_its_surroundings(
    text: str, must_vanish: str, must_survive: list[str]
) -> None:
    out = _engine(text)
    assert must_vanish not in out, out
    assert "[REDACTED]" in out
    for kept in must_survive:
        assert kept in out, out


@pytest.mark.parametrize(
    ("text", "must_vanish", "must_survive"),
    [entry[1:] for entry in CREDENTIALS],
    ids=[entry[0] for entry in CREDENTIALS],
)
def test_the_policy_surface_redacts_the_credential_and_keeps_its_surroundings(
    text: str, must_vanish: str, must_survive: list[str]
) -> None:
    out = _policy(text)
    assert must_vanish not in out, out
    for kept in must_survive:
        assert kept in out, out


def test_a_url_path_credential_is_redacted_in_diagnostics_not_in_the_url() -> None:
    """The engine reaches a path segment; `redact_auth_url` deliberately does not.

    `redact_auth_url` is what `sanitize_url_elicitation_url` returns -- the URL
    the operator must OPEN to authorize -- and an IdP's authorization-server id
    in that path (`/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize`) is exactly the
    shape a credential has. A diagnostic can lose it; the login flow cannot.
    """
    webhook = (
        "https://hooks.example/services/T0123ABCD/B0123ABCD/a1B2c3D4e5F6g7H8i9J0k1L2"
    )
    assert _engine(f"failed: {webhook}") == (
        "failed: https://hooks.example/services/T0123ABCD/B0123ABCD/[REDACTED]"
    )
    authorize = (
        "https://dev-1.okta.com/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize?state=ok"
    )
    assert redact_auth_url(authorize) == authorize


def test_identifiers_that_are_not_credentials_are_kept() -> None:
    """The shape rule's named exemptions, one probe each.

    hex digests and UUIDs (uniform hex), `0x` addresses (uniform hex behind a
    prefix), camelCase (no `xAB` signature), identifiers with one embedded
    number (`X509Cert`), timestamps (low transition ratio), and hyphen-joined
    short pieces (`pmcp-7d9f8b6c5-x2k9q`: scored per segment, never whole).
    """
    for kept in [
        "3843d2f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
        "550e8400-e29b-41d4-a716-446655440000",
        "0x7f3a2b1c4d50",
        "UnicodeDecodeError",
        "IPv6Address",
        "Ed25519PrivateKey",
        "20260923T041200Z",
        "x86_64-linux-gnu",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "pmcp-7d9f8b6c5-x2k9q",
    ]:
        assert _engine(kept) == kept


def test_payload_sized_runs_are_data_not_credentials() -> None:
    """A base64 image or encoded file survives; a 200-character token does not.

    `gateway.tasks_result` redacts by default and `process_output` JSON-dumps
    the whole result, so `ImageContent.data` goes through this engine. No
    vendor issues an unbroken token past 256 characters (JWTs are dot-joined
    and have their own rule; private keys are PEM blocks).
    """
    blob = base64.b64encode(bytes(range(256)) * 24).decode()
    assert len(blob) > 8000
    assert _engine(blob) == blob
    assert _policy(blob) == blob
    rng = random.Random(234)
    token = "".join(
        rng.choice(string.ascii_letters + string.digits) for _ in range(200)
    )
    assert _engine(f"leaked {token} here") == "leaked [REDACTED] here"


def test_a_slash_leading_payload_is_not_split_into_scored_pieces() -> None:
    """The payload bound applies to the WHOLE run, before any path splitting.

    JPEG base64 always starts `/9j/` and carries a `/` every ~64 characters;
    scoring each piece between slashes on its own turned an ordinary image into
    `[REDACTED]/[REDACTED]/...` (board finding on the first revision). A long
    run that reads as a route -- short pieces, two of them plain words -- is
    still split, so a credential segment deep in a REST path is still caught.
    """
    leading = "/" + "4eC39HqLyjWDarjtT1zdp7dc" + "/" + "A" * 8190
    assert _engine(leading) == leading
    assert _policy(leading) == leading
    rng = random.Random(234)
    body = base64.b64encode(bytes(rng.getrandbits(8) for _ in range(4500))).decode()
    jpeg = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgH" + body
    assert body.count("/") > 50  # the shape that broke: a slash every ~64 chars
    assert _engine(jpeg) == jpeg
    assert _policy(jpeg) == jpeg
    route = (
        "/api/v1/organizations/acme-corp/projects/"
        + "/".join(f"segment{i}" for i in range(30))
        + "/secrets/4eC39HqLyjWDarjtT1zdp7dc/versions/latest"
    )
    assert len(route) > 256
    assert _engine(route) == route.replace("4eC39HqLyjWDarjtT1zdp7dc", "[REDACTED]")


# === composition ========================================================== #


def test_redaction_is_idempotent() -> None:
    """`[REDACTED]` must not itself be re-matched; surfaces apply the engine twice."""
    for text in [PROSE, *[entry[1] for entry in CREDENTIALS]]:
        once = _engine(text)
        assert _engine(once) == once, text
        twice = _policy(text)
        assert _policy(twice) == twice, text


def test_redact_secrets_keeps_a_key_but_never_splits_inside_a_secret() -> None:
    """The post-pass splits `key=value` at the separator, and only there.

    On main the split took the FIRST `:`/`=` in the match, so an operator
    pattern for a base64 shape kept the secret and replaced its padding:
    `dXNlcjpwYXNzd29yZA= [REDACTED]`. The probe value is deliberately
    low-entropy (uniform hex letters) so the engine's shape pass leaves it
    alone and only the operator pattern -- and therefore the split -- is
    exercised; a real base64 secret would be gone before the post-pass ran.
    """
    manager = PolicyManager()
    manager._redaction_regexes = [re.compile(r"[A-Za-z0-9+/]{16,}={1,2}")]
    assert (
        sanitize_auth_diagnostic("abcdabcdabcdabcdabcd==") == "abcdabcdabcdabcdabcd=="
    )
    assert manager.redact_secrets("basic abcdabcdabcdabcdabcd== auth") == (
        "basic [REDACTED] auth"
    )
    manager._redaction_regexes = [re.compile(r"mykey\s*[:=]\s*\S+")]
    assert manager.redact_secrets("mykey: abcdef") == "mykey: [REDACTED]"


def test_the_engine_redacts_before_it_truncates() -> None:
    """The cut lands on `[REDACTED]`, never on a token prefix.

    The token starts at offset 391 and `max_length` is 400: were the cut taken
    first, nine characters of it would survive and no longer have a redactable
    shape.
    """
    text = "x" * 390 + " " + TOKEN
    out = sanitize_auth_diagnostic(text, max_length=400)
    assert len(out) == 400
    assert "ghp_" not in out
    assert out.endswith(" [REDACTED")


def test_process_output_never_ends_on_a_partial_token() -> None:
    """The byte cut is taken BEFORE redaction, so it must not split a token.

    With `max_bytes=300` the cut falls 200 bytes in, nine characters into the
    token; `ghp_16C7e` has no shape any rule recognises. Backing the cut up to
    the preceding boundary removes the exposed prefix.
    """
    policy = PolicyManager()
    output = "a" * 190 + " " + TOKEN + " " + "c" * 100
    processed = policy.process_output(output, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert "ghp_" not in processed["result"], processed["result"]
    assert processed["result"].startswith("a" * 190)


def test_process_output_never_ends_on_a_partial_token_after_a_path() -> None:
    """The trailing run is the PATH plus the token, and the whole run is backed
    out of (board finding on the first revision: a 64-character bound measured
    on the run left `/bbb.../ghp_16C7e` in the output). Past 256 characters the
    run is a payload or a long path, and its last `/`-segment -- where a
    credential in a path sits -- is still backed out of.
    """
    policy = PolicyManager()
    output = "a" * 120 + " /" + "b" * 68 + "/" + TOKEN + " " + "c" * 100
    processed = policy.process_output(output, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert "ghp_" not in processed["result"], processed["result"]
    assert processed["result"].startswith("a" * 120 + " ")
    long_path = "a" * 20 + " /" + "b" * 300 + "/" + TOKEN + " " + "c" * 100
    processed = policy.process_output(long_path, redact=True, max_bytes=450)
    assert processed["truncated"] is True
    assert "ghp_" not in processed["result"], processed["result"]
    assert processed["result"].startswith("a" * 20 + " /" + "b" * 300)


def test_process_output_still_truncates_a_single_giant_run() -> None:
    """A run of `b`s is not a credential, and the cut lands where it always
    did (`max_bytes - 100`): redaction ran BEFORE the cut, so nothing has to
    be backed out of (revs 2-3 backed the cut up to a run boundary; rev 4
    redacts the window first and the back-up is gone)."""
    processed = PolicyManager().process_output("b" * 1000, redact=True, max_bytes=600)
    assert processed["truncated"] is True
    assert processed["result"].startswith("b" * 500)
    processed = PolicyManager().process_output("b" * 400, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert processed["result"].startswith("b" * 200 + "\n\n[... OUTPUT TRUNCATED")


def test_a_quoted_value_runs_to_its_closing_quote() -> None:
    """An escaped quote inside a JSON string does not end the value.

    `"[^"]*"` stopped at the `\\"` in `{"password": "a\\"hunter2"}` and left
    `hunter2"` behind on both surfaces (board finding on the first revision).
    """
    for text, expected in [
        ('{"password": "a\\"hunter2"}', '{"password": [REDACTED]}'),
        ("{'password': 'a\\'hunter2'}", "{'password': [REDACTED]}"),
        (
            '{"password": "hunter2", "user": "bob"}',
            '{"password": [REDACTED], "user": "bob"}',
        ),
    ]:
        assert _engine(text) == expected
        assert _policy(text) == expected


def test_an_earlier_pass_never_eats_the_boundary_a_later_pass_needs() -> None:
    """Keyed values are redacted first, and the looser passes stop at quotes.

    Rev 2 ran the `Bearer` pass before the keyword pass with a value class of
    `[^\\s,;]+`, so on `{"password": "hunter2 Bearer test-token"}` it consumed
    the closing quote and brace and the keyword rule could no longer match:
    `{"password": "hunter2 Bearer [REDACTED]` on both surfaces, `hunter2`
    visible (board finding on rev 2). The `Authorization` rule had the same
    value class and also missed a JSON-quoted key entirely.
    """
    for text, expected in [
        ('{"password": "hunter2 Bearer test-token"}', '{"password": [REDACTED]}'),
        ("{'password': 'hunter2 Bearer x'}", "{'password': [REDACTED]}"),
        (
            '{"authorization": "Bearer abc123def456", "x": 1}',
            '{"authorization": "[REDACTED]", "x": 1}',
        ),
        ('Authorization: "Bearer abc.def"', 'Authorization: "[REDACTED]"'),
        ('{"note": "see Bearer abc123def456"}', '{"note": "see Bearer [REDACTED]"}'),
        ('token="Bearer abc"', "token=[REDACTED]"),
    ]:
        assert _engine(text) == expected, text
        assert "hunter2" not in _policy(text) and "abc" not in _policy(text), text


def test_truncation_never_leaks_a_quoted_multi_word_password() -> None:
    """Sweep the cut across quoted passwords that contain spaces.

    The byte cut can land inside a quoted value after a space: no trailing
    token run ends there, and with the closing quote gone the rev-2 keyword
    rule could not match, so the first word of the password stood in the
    output (board finding on rev 2: 128 of 1 452 cuts). A quoted value with
    no closing quote on its line now runs to the end of the line. The sweep
    asserts that it actually covers the value -- a range that misses the cut
    region reports zero leaks for the wrong reason.
    """
    policy = PolicyManager()
    leaks: list[str] = []
    for prefix in (50, 120, 177, 190):
        for password in ("hunter2 tail", "correct horse battery staple", "p4ss w0rd!"):
            output = "a" * prefix + ' {"password": "' + password + '"}' + "c" * 100
            opening = output.index('"' + password)
            closing = opening + len(password) + 1
            inside = 0
            for max_bytes in range(prefix + 80, prefix + 201):
                cut = max_bytes - 100  # `truncate_output` leaves room for the marker
                inside += opening < cut < closing
                result = policy.process_output(output, redact=True, max_bytes=max_bytes)
                if any(word in result["result"] for word in password.split()):
                    leaks.append(
                        f"prefix={prefix} password={password!r} max_bytes={max_bytes}"
                    )
            assert inside > 0, (prefix, password)  # the sweep reached the value
    assert leaks == []


def test_no_pass_can_consume_a_boundary_another_pass_needs() -> None:
    """Every pass reads the original text; replacements land in one step.

    Rev 3 ran the URL pass first and rewrote the text: redacting the query
    value removed the backslash that escaped a quote, which turned the escaped
    quote into a closing one, and `{"password": "https://example.test/?token=
    abc123def456\\"hunter2"}` came out as `{"password": [REDACTED]hunter2"}` on
    both surfaces (board finding on rev 3). With span collection the keyed
    value's span contains the URL's span and wins.
    """
    text = '{"password": "https://example.test/?token=abc123def456\\"hunter2"}'
    assert _engine(text) == '{"password": [REDACTED]}'
    assert _policy(text) == '{"password": [REDACTED]}'
    # a URL that is NOT inside a keyed value keeps its host and route
    assert _engine("see https://example.test/?token=abc123def456&page=2.") == (
        "see https://example.test/?token=[REDACTED]&page=2."
    )


def test_truncation_after_a_backslash_cannot_expose_a_password() -> None:
    """The cut lands right after the `\\` of an escaped character.

    Rev 3 cut first and then asked the redactor to make sense of `"hunter2\\`
    (board finding on rev 3: 48 of 2 880 escape-heavy cuts leaked). Rev 4
    redacts the window before the cut, so the redactor sees the whole value.
    """
    output = "a" * 177 + " " + json.dumps({"password": "hunter2\\tail"}) + "c" * 100
    processed = PolicyManager().process_output(output, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert "hunter2" not in processed["result"], processed["result"]


def test_the_redaction_window_reaches_past_the_cap() -> None:
    """A keyed value that straddles the cap is seen whole by the redactor.

    The window is `max_bytes + _REDACTION_WINDOW_SLACK` characters; a
    4 000-character value (PEM-sized) that starts before the cap and ends
    after it is inside the window and is redacted before the cut.
    """
    value = "".join(random.Random(4).choice(string.ascii_letters) for _ in range(4000))
    output = "a" * 190 + ' {"password": "' + value + '"}' + "c" * 100
    processed = PolicyManager().process_output(output, redact=True, max_bytes=400)
    assert processed["truncated"] is True  # the cut is at byte 300, inside the value
    assert value[:4] not in processed["result"], processed["result"][180:240]
    assert processed["result"].startswith("a" * 190 + ' {"password": [REDACTED]')


# === properties =========================================================== #

_PRINTABLE = (
    "".join(c for c in string.printable if c not in "\t\n\r\x0b\x0c") + "éßñ日本語🙂"
)
_DELIMITERS = frozenset(" \t\"',;&()[]{}")


def _random_passwords(rng: random.Random, count: int) -> list[str]:
    return [
        "".join(rng.choice(_PRINTABLE) for _ in range(rng.randint(4, 24)))
        for _ in range(count)
    ]


def _single_quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _keyed_forms(password: str) -> list[tuple[str, str, str]]:
    """(text, encoded value as it appears in the text, probe).

    The probe is the first four characters of the value as encoded -- what a
    leak would show. Bare forms (`password=x`, a header) exist only for
    passwords without delimiters: an unquoted syntax cannot carry a space or
    a quote at all.
    """
    forms: list[tuple[str, str, str]] = []
    for encoded, text in [
        (json.dumps(password), json.dumps({"password": password})),
        (
            json.dumps(password, ensure_ascii=False),
            json.dumps({"password": password}, ensure_ascii=False),
        ),
        (_single_quoted(password), "{'password': " + _single_quoted(password) + "}"),
    ]:
        forms.append((text, encoded, encoded[1:5]))
    if not any(c in _DELIMITERS for c in password) and not password.startswith("="):
        # `key==x` reads as a comparison (`if token == expected`), so a bare
        # value cannot begin with `=`; it can carry one anywhere else.
        forms.append((f"password={password}", password, password[:4]))
        forms.append((f"X-Api-Key: {password}", password, password[:4]))
    return forms


def _probe_is_usable(text: str, encoded: str, probe: str) -> bool:
    """Skip a probe shorter than four characters or one the surrounding text
    already contains (the assertion could not tell a leak from the template)."""
    return len(probe) == 4 and probe not in text.replace(encoded, "", 1)


def test_property_every_keyed_password_is_redacted_whole() -> None:
    """Thousands of random passwords from every printable character.

    Quotes, backslashes, spaces, brackets, unicode: in JSON (ASCII-escaped and
    not), single-quoted, and -- for delimiter-free passwords -- bare and as a
    header. The redacted output never contains the first four characters of
    the value as it appeared. The board's cases (`a\\"hunter2`, `hunter2 Bearer
    x`, `https://…\\"hunter2`) are samples of this property.
    """
    rng = random.Random(234)
    checked = skipped = 0
    for password in _random_passwords(rng, 3000):
        for text, encoded, probe in _keyed_forms(password):
            if not _probe_is_usable(text, encoded, probe):
                skipped += 1
                continue
            checked += 1
            assert probe not in _engine(text), (password, text, _engine(text))
            assert probe not in _policy(text), (password, text, _policy(text))
    assert checked > 10000, (checked, skipped)


def test_property_truncation_never_exposes_a_keyed_password() -> None:
    """The same forms, with the cut swept across every offset of the value.

    `process_output(redact=True)` must never show the first four characters of
    the value, wherever the cut lands: before it, inside it (after a space,
    after a backslash, after a quote), or after it. The test asserts that the
    sweep actually lands inside every value it tries -- a sweep that misses
    the cut region reports zero leaks for the wrong reason.
    """
    rng = random.Random(4234)
    policy = PolicyManager()
    cuts = inside = values = 0
    for password in _random_passwords(rng, 300):
        prefix = rng.choice((50, 120, 177, 190))
        for form, encoded, probe in _keyed_forms(password):
            text = "a" * prefix + " " + form + "c" * 100
            if not _probe_is_usable(text, encoded, probe):
                continue
            start = text.index(encoded)
            start_bytes = len(text[:start].encode("utf-8"))
            end_bytes = start_bytes + len(encoded.encode("utf-8"))
            landed = 0
            for max_bytes in range(start_bytes - 2 + 100, end_bytes + 3 + 100):
                cut = max_bytes - 100  # `truncate_output` leaves room for the marker
                result = policy.process_output(text, redact=True, max_bytes=max_bytes)
                cuts += 1
                landed += start_bytes < cut < end_bytes
                assert probe not in result["result"], (
                    password,
                    form,
                    max_bytes,
                    result,
                )
            assert landed > 0, (password, form)
            inside += landed
            values += 1
    assert values > 1000 and cuts > 20000 and inside > 15000, (values, cuts, inside)


_PROSE_WORDS = (
    "the token bucket secret ingredient session expired password reset cookie "
    "consent bearer of bad news rate limit refused call key keys rotated weekly "
    "code status exit error invalid grant not found request failed because "
    "timeout retry server client scope read write admin user tenant api jwt "
    "saml assertion ticket sid tokens secrets passwords sessions cookies"
).split()
_PROSE_IDENTIFIERS = (
    "ResourceServerAuthError UnicodeDecodeError IPv6Address Ed25519PrivateKey "
    "X509Cert Rsa2048Key Oauth2ClientError parseJSON2Dict toHTML5String sha256sum"
).split()


def _random_prose(rng: random.Random) -> str:
    """Prose and diagnostic text made of plain words, numbers, identifiers,
    digests, ids and timestamps. A keyword is followed only by a word or a
    number: the design says a digit-bearing non-word after a bare keyword is
    a credential, and this corpus is the other half of that contract."""

    def words() -> str:
        return " ".join(rng.choice(_PROSE_WORDS) for _ in range(rng.randint(2, 8)))

    def number() -> str:
        return str(rng.randint(0, 99999))

    def hexdigits(n: int) -> str:
        return "".join(rng.choice("0123456789abcdef") for _ in range(n))

    clauses = [
        words,
        lambda: f"{words()} {number()}",
        lambda: f"status_code={number()} error_code={rng.choice(_PROSE_WORDS)}_{rng.choice(_PROSE_WORDS)}",
        lambda: f"exit code {number()}",
        lambda: f"in {rng.choice(_PROSE_IDENTIFIERS)} at 0x{hexdigits(12)}",
        lambda: f"commit {hexdigits(40)}",
        lambda: f"request_id {uuid.UUID(int=rng.getrandbits(128))}",
        lambda: f"at 2026-09-{rng.randint(10, 28)}T{rng.randint(10, 23)}:{rng.randint(10, 59)}:00+00:00",
        lambda: f"installed pmcp-1.{rng.randint(0, 30)}.{rng.randint(0, 9)}",
        lambda: f"Bearer {rng.choice(_PROSE_WORDS).capitalize()}",
        lambda: f"code={number()}",
        lambda: f"--{rng.choice(_PROSE_WORDS)} {rng.choice(_PROSE_WORDS)}",
        lambda: f'{{"code": -{rng.randint(32000, 32768)}, "message": "{words()}"}}',
    ]
    return rng.choice([" ", ", ", "; ", ". ", "\n"]).join(
        rng.choice(clauses)() for _ in range(rng.randint(3, 6))
    )


def test_property_prose_survives_byte_identical() -> None:
    """Thousands of generated prose/diagnostic lines survive both surfaces."""
    rng = random.Random(9234)
    for _ in range(2000):
        text = _random_prose(rng)
        assert _engine(text) == text, text
        assert _policy(text) == text, text


def _declared_secret_keys() -> set[str]:
    """Every key the redactor DECLARES sensitive, from both surfaces' sources.

    `AUTH_DIAGNOSTIC_SECRET_KEYS`, plus every name in the first alternation
    group of each `DEFAULT_REDACTION_PATTERNS` entry (`(secret|password|
    passwd|pwd)`, `(api[_-]?key|apikey)` expanded to `api_key`, `api-key`,
    `apikey`, ...). A key named anywhere as sensitive but not redacted in every
    form below fails `test_property_every_declared_key_is_redacted_in_every_form`
    on its own -- no case has to be hand-picked (board finding on rev 4:
    `passwd`, `pwd`, `private_key`, `credentials`, `auth` were named or
    expected and covered by nothing).
    """
    keys = set(AUTH_DIAGNOSTIC_SECRET_KEYS)
    for pattern in DEFAULT_REDACTION_PATTERNS:
        group = re.search(r"\(([a-z_|\[\]?-]+)\)", pattern)
        if group is None:
            continue  # a bare token shape (`sk-`, `ghp_`), not a key
        for name in group.group(1).split("|"):
            if "[_-]?" in name:
                keys.update(name.replace("[_-]?", sep) for sep in ("_", "-", ""))
            else:
                keys.add(name)
    keys.add("private-key")
    keys.add("privateKey")
    return keys


#: Bare (unquoted) syntaxes cannot carry a quote or a list/query separator
#: (`,` `;` `&`) in a value; everything else, including leading punctuation
#: and spaces, is fair.
_BARE_VALUE_ALPHABET = "".join(c for c in _PRINTABLE if c not in "\"',;&")


def _key_forms(key: str, value: str) -> list[tuple[str, str, str]]:
    """(text, encoded value, probe) for one key: JSON, single-quoted, `key=`,
    header. The bare forms carry the value's first token with quotes and list
    separators removed, which is all an unquoted syntax can carry; the probe
    is the first four encoded characters."""
    bare_tokens = "".join(c for c in value if c not in "\"',;&").split()
    bare = bare_tokens[0] if bare_tokens else ""
    if not bare.strip(")]}"):
        bare = ""  # nothing but closing brackets is not a value
    forms = [
        (json.dumps({key: value}), json.dumps(value), json.dumps(value)[1:5]),
        (
            "{'" + key + "': " + _single_quoted(value) + "}",
            _single_quoted(value),
            _single_quoted(value)[1:5],
        ),
    ]
    if bare and not bare.startswith("="):  # `key==x` is a comparison, not a value
        forms.append((f"{key}={bare}", bare, bare[:4]))
        forms.append((f"{key}: {bare}", bare, bare[:4]))
    return forms


def test_property_every_declared_key_is_redacted_in_every_form() -> None:
    """Every declared key × every form × random values, both surfaces.

    Values are drawn from every printable character (quoted forms) or every
    printable character but quotes and list separators (bare forms); a third
    of them start with punctuation and a third contain a space. For a WEAK
    key (`WEAK_SECRET_KEYS`) a plain word or number is kept by design, so
    those values are skipped for those keys; for a strong key nothing is
    skipped. The per-key result table in the plan comes from this loop.
    """
    rng = random.Random(5234)
    checked = 0
    leaks: list[str] = []
    for key in sorted(_declared_secret_keys()):
        for _ in range(60):
            alphabet = _PRINTABLE if rng.random() < 0.5 else _BARE_VALUE_ALPHABET
            value = "".join(rng.choice(alphabet) for _ in range(rng.randint(4, 20)))
            roll = rng.random()
            if roll < 0.33:
                value = rng.choice("!#$%&()*+-./:<=>?@[\\]^_`{|}~") + value
            elif roll < 0.66:
                value = value[:2] + " " + value[2:]
            for text, encoded, probe in _key_forms(key, value):
                if not _probe_is_usable(text, encoded, probe):
                    continue
                if (
                    key.lower() in WEAK_SECRET_KEYS
                    and _is_plain_word_or_number_for_test(encoded)
                ):
                    continue
                if not encoded.strip("\"'").strip():
                    continue
                checked += 1
                for surface, out in (
                    ("engine", _engine(text)),
                    ("policy", _policy(text)),
                ):
                    if probe in out:
                        leaks.append(f"{surface} key={key!r} text={text!r} out={out!r}")
    assert checked > 5000, checked
    assert leaks == [], "\n".join(leaks[:20])


def _is_plain_word_or_number_for_test(encoded: str) -> bool:
    """The weak-key exemption, restated: a word (letters/underscores) or a
    number, quotes and trailing sentence punctuation stripped."""
    value = encoded.strip("\"'").rstrip(".:!?")
    return bool(re.fullmatch(r"[A-Za-z_]+|[+-]?[0-9]+|0[xX][0-9a-fA-F]+", value))


# === rev 6: the composition property and the regressions it replaces ====== #


def test_secrets_inside_a_url_query_are_redacted_whatever_the_key() -> None:
    """The URL pass yields component spans, never a rewritten URL.

    Rev 5 replaced the whole URL with `redact_auth_url(url)` and dropped every
    span inside it as "contained" -- so a keyed value or a token under a
    query key `redact_auth_url` does not know leaked, and main (which ran the
    keyword rule over the rewritten URL) did better (board finding on rev 5).
    """
    for text, expected in [
        (
            "https://example.com/?aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
            "https://example.com/?aws_secret_access_key=[REDACTED]",
        ),
        (
            "https://api.github.com/repos?access=" + TOKEN,
            "https://api.github.com/repos?access=[REDACTED]",
        ),
        (
            "https://example.com/login?pwd=hunter2",
            "https://example.com/login?pwd=[REDACTED]",
        ),
        (
            "https://user:pass@auth.example/cb?code=oauth-code&state=ok#access_token=abc.def",
            "https://auth.example/cb?code=[REDACTED]&state=ok",
        ),
        (
            "https://h.example/?q=4eC39HqLyjWDarjtT1zdp7dc&page=2",
            "https://h.example/?q=[REDACTED]&page=2",
        ),
    ]:
        assert _engine(text) == expected, text
        assert _policy(text) == expected, text
    assert _engine("password=https://h.example/?a=1,b=2") == "password=[REDACTED],b=2"
    assert "h.example" not in _policy("password=https://h.example/?a=1,b=2")
    # `redact_auth_url` itself is byte-for-byte main's: the elicitation URL
    assert (
        redact_auth_url("https://u:p@auth.example/cb?ticket=secret&x=1#frag")
        == "https://auth.example/cb?ticket=%5BREDACTED%5D&x=1"
    )


def test_a_literal_marker_in_the_input_is_not_a_shield() -> None:
    """A downstream server can write `[REDACTED]` itself; that must not disarm
    the rule next to it. Rev 5 dropped every span touching an existing marker
    (board finding: `password=hunter2[REDACTED]` survived, main redacted it).
    Existing markers are spans and merge with whatever touches them; a marker
    nothing touches is replaced by itself, so the surfaces stay idempotent.
    """
    for text, expected in [
        ("password=hunter2[REDACTED]", "password=[REDACTED]"),
        ("api_key=abc123def456[REDACTED]", "api_key=[REDACTED]"),
        ('{"password": "hunter2 [REDACTED]"}', '{"password": [REDACTED]}'),
        ('{"password": "[REDACTED] hunter2"}', '{"password": [REDACTED]}'),
        ("token=[REDACTED]abc123def456 tail", "token=[REDACTED] tail"),
        ("password=[REDACTED]", "password=[REDACTED]"),
        ('{"password": [REDACTED]}', '{"password": [REDACTED]}'),
        (
            "[REDACTED] [REDACTED_EMAIL] ghp_ sk-",
            "[REDACTED] [REDACTED_EMAIL] ghp_ sk-",
        ),
    ]:
        assert _engine(text) == expected, text
        assert "hunter2" not in _policy(text) and "abc123def456" not in _policy(text), (
            text
        )
    assert _policy("password= [REDACTED]") == _policy(_policy("password= [REDACTED]"))


def test_authorization_is_a_header_not_a_word() -> None:
    """`authorization: none` is prose; `if token == expected` is a comparison."""
    assert _engine("authorization: none") == "authorization: none"
    assert (
        _engine("Set the Authorization:\nheader first")
        == "Set the Authorization:\nheader first"
    )
    assert _engine("Authorization: Bearer abc.def") == "Authorization: [REDACTED]"
    assert _engine("Authorization=Basic dXNlcjpwYXNz") == "Authorization=[REDACTED]"
    assert _engine("if token == expected:") == "if token == expected:"
    assert (
        _engine("token = await self._get_token()")
        == "token = [REDACTED] self._get_token()"
    )


def _span_texts(text: str, spans: list[tuple[int, int, str]]) -> list[str]:
    """The source text of every covering span (replacement `[REDACTED]` or
    empty) of four or more characters that occurs exactly once in ``text``,
    so its absence from the output is unambiguous."""
    return [
        text[start:end]
        for start, end, replacement in spans
        if replacement in (REDACTED, "")
        and end - start >= 4
        and text.count(text[start:end]) == 1
    ]


_URL_SAFE = string.ascii_letters + string.digits + "-_.~%+"
_NON_SECRET_QUERY_KEYS = ("q", "page", "access", "u", "foo", "next", "redirect")


def _query_string_texts(rng: random.Random) -> list[str]:
    """Secrets in URL query strings under secret AND non-secret keys, with a
    keyed value around the URL sometimes."""
    texts = []
    for _ in range(400):
        secret = "".join(
            rng.choice(string.ascii_letters + string.digits) for _ in range(24)
        )
        key = rng.choice([*sorted(AUTH_SECRET_QUERY_KEYS), *_NON_SECRET_QUERY_KEYS])
        extra = "".join(rng.choice(_URL_SAFE) for _ in range(rng.randint(0, 8)))
        url = f"https://h.example/p/{extra}?{key}={secret}&page=2"
        if rng.random() < 0.3:
            url = f"https://u:{secret[:8]}@h.example/cb?{key}={secret}#frag"
        texts.append(
            rng.choice([url, f"see {url}.", f"password={url}", f'{{"note": "{url}"}}'])
        )
    return texts


def _marker_texts(rng: random.Random) -> list[str]:
    """Secrets next to, and inside quotes with, a literal `[REDACTED]`."""
    texts = []
    keys = ("password", "api_key", "token", "secret", "pwd")
    for _ in range(400):
        secret = "".join(
            rng.choice(string.ascii_letters + string.digits)
            for _ in range(rng.randint(6, 16))
        )
        key = rng.choice(keys)
        texts.append(
            rng.choice(
                [
                    f"{key}={secret}[REDACTED]",
                    f"{key}=[REDACTED]{secret}",
                    f'{{"{key}": "{secret} [REDACTED]"}}',
                    f'{{"{key}": "[REDACTED] {secret}"}}',
                    f"[REDACTED] {key}: {secret} [REDACTED]",
                ]
            )
        )
    return texts


def test_property_every_collected_span_is_gone_from_the_output() -> None:
    """For every span the redactor itself collected, the span's text is absent
    from the output -- on both surfaces, over the static corpora and over
    generators that put secrets inside URL query strings under secret and
    non-secret keys, and next to literal `[REDACTED]` markers. This is the
    property the containment rule and the marker rule broke on rev 5.
    """
    rng = random.Random(6234)
    texts = [entry[1] for entry in CREDENTIALS]
    for password in _random_passwords(rng, 500):
        texts.extend(text for text, _, _ in _keyed_forms(password))
    texts.extend(_query_string_texts(rng))
    texts.extend(_marker_texts(rng))
    policy = PolicyManager()
    checked = 0
    leaks: list[str] = []
    for text in texts:
        for surface, spans, out in (
            ("engine", collect_redaction_spans(text), _engine(text)),
            ("policy", policy.redaction_spans(text), policy.redact_secrets(text)),
        ):
            for piece in _span_texts(text, spans):
                checked += 1
                if piece in out:
                    leaks.append(f"{surface} {text!r} -> {out!r} still has {piece!r}")
    assert checked > 4000, checked
    assert leaks == [], "\n".join(leaks[:20])


def test_property_the_window_edge_never_emits_an_unredacted_value() -> None:
    """Nothing from past the cap is emitted except inside a span.

    A prefix the redactor shrinks (one oversized PEM block) put the window's
    far edge -- a second, unredacted cut -- into the returned text on rev 5:
    with the value straddling `max_bytes + 16384`, its head was emitted. The
    sweep asserts that some cuts straddle the edge.
    """
    policy = PolicyManager()
    max_bytes = 1000
    edge = max_bytes + 16384
    pem = (
        "-----BEGIN PRIVATE KEY-----" + "x" * (edge - 200) + "-----END PRIVATE KEY-----"
    )
    straddling = 0
    for pad in range(120, 200):
        text = pem + "\n" * pad + '{"password": "hunter2 tail"}' + "c" * 100
        start = text.index('"hunter2')
        end = start + len('"hunter2 tail"')
        straddling += start < edge < end
        result = policy.process_output(text, redact=True, max_bytes=max_bytes)
        assert "hunter2" not in result["result"], (pad, result["result"][-120:])
        assert len(result["result"].encode("utf-8")) <= max_bytes, pad
    assert straddling > 0


#: (input, secrets main removes on at least one surface). Built by running
#: main @ 860636a's engine and policy over these inputs and recording every
#: token-character piece (3+ chars) that disappeared, minus main's collateral
#: (`next`, `home`: main's keyword rule ate the whole query tail; `access_token`:
#: a key name in a dropped fragment). Every one must still disappear here.
_MAIN_REDACTS: list[tuple[str, list[str]]] = [
    (
        "https://example.com/?aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
        ["wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"],
    ),
    (
        "https://api.github.com/repos?access=ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        ["ghp_16C7e42F292c6912E7710c838347Ae178B4a"],
    ),
    ("https://example.com/login?pwd=hunter2", ["hunter2"]),
    ("https://example.com/login?password=hunter2&next=/home", ["hunter2"]),
    ("https://auth.example/cb?code=oauth-code&state=ok", ["oauth-code"]),
    (
        "https://auth.example/cb?bearer=secret-bearer&token=access-token",
        ["access-token", "secret-bearer"],
    ),
    (
        "https://auth.example/cb?jwt=eyJhbGciOiJIUzI1NiJ9.payload.sig",
        ["eyJhbGciOiJIUzI1NiJ9.payload.sig"],
    ),
    (
        "https://auth.example/cb?assertion=saml-secret&ticket=ticket-secret",
        ["saml-secret", "ticket-secret"],
    ),
    (
        "https://user:pass@auth.example/cb?code=oauth-code",
        ["oauth-code", "pass", "user"],
    ),
    ("https://auth.example/cb#access_token=abc.def.ghi", ["abc.def.ghi"]),
    ("password=hunter2", ["hunter2"]),
    ("password: hunter2", ["hunter2"]),
    ("api_key=abc123def456", ["abc123def456"]),
    ("secret=s3cr3t", ["s3cr3t"]),
    ("client_secret=abc", ["abc"]),
    ("access_token=AbC123dEf456GhI", ["AbC123dEf456GhI"]),
    ("refresh_token=xyz789xyz789", ["xyz789xyz789"]),
    ("authorization: Bearer abc.def", ["Bearer", "abc.def"]),
    ("Authorization=Basic dXNlcjpwYXNz", ["Basic"]),
    ("Bearer abc123def456", ["abc123def456"]),
    ("sk-live-abc123def456", ["sk-live-abc123def456"]),
    (
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        ["ghp_16C7e42F292c6912E7710c838347Ae178B4a"],
    ),
    (
        "github_pat_11ABCDEFG0abcdefghijklmnop_Ab3dEf6GhI9jKl2",
        ["github_pat_11ABCDEFG0abcdefghijklmnop_Ab3dEf6GhI9jKl2"],
    ),
    ("set-cookie: session=abc123; Path=/", ["abc123", "session"]),
    ("cookie: sid=abc123def", ["abc123def", "sid"]),
    ("sid=abc123def456", ["abc123def456"]),
    ("session=013G8iK4noj1iNbSTqJVFEX6", ["013G8iK4noj1iNbSTqJVFEX6"]),
    (
        "id_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
        ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"],
    ),
    ("saml=PHNhbWw6QXNzZXJ0aW9u", ["PHNhbWw6QXNzZXJ0aW9u"]),
    ("assertion=abc123def456", ["abc123def456"]),
    ("tenant_id=acme-123", ["acme-123"]),
    ("tenant-id: acme-123", ["acme-123"]),
    ("aws_secret=wJalrXUtnFEMI", ["wJalrXUtnFEMI"]),
    ("aws_access=AKIAIOSFODNN7EXAMPLE", ["AKIAIOSFODNN7EXAMPLE"]),
    ("passwd=hunter2", ["hunter2"]),
    ("pwd=hunter2", ["hunter2"]),
    ("apikey: abc123def456", ["abc123def456"]),
    ("X-Api-Key: abc123def456", ["abc123def456"]),
    (
        "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
        ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"],
    ),
    ("--token abc123def456 --password hunter2", ["abc123def456", "hunter2"]),
    ("token: abc123def456", ["abc123def456"]),
    ("auth_code=SplxlOBeZQQYbYS6WxSbIA", ["SplxlOBeZQQYbYS6WxSbIA"]),
]


@pytest.mark.parametrize(
    ("text", "secrets"), _MAIN_REDACTS, ids=[t[:40] for t, _ in _MAIN_REDACTS]
)
def test_never_worse_than_main(text: str, secrets: list[str]) -> None:
    for secret in secrets:
        assert secret not in _engine(text), (secret, _engine(text))
        assert secret not in _policy(text), (secret, _policy(text))
```

## Documentation impact

- `CHANGELOG.md` — add under `[Unreleased]`:
  - `### Fixed`: the redactor no longer redacts the word after `token`,
    `secret`, `session`, `password` or `bearer` in prose (`token bucket`,
    `Missing bearer token`, `session expired`), no longer redacts `status_code=`,
    `error_code=`, `token_endpoint=` values or the first word of the line after a
    keyword, and `redact_secrets` no longer splits an operator pattern's match
    at base64 padding (Consiliency/pmcp#234).
  - `### Security`: credentials with no keyword are now redacted by shape —
    AWS access-key ids, Slack and Google tokens, `prefix_body` vendor tokens
    (`sk-`, `sk_`, `ghp_`, `github_pat_`, `glpat-`, `dop_v1_` and any other
    short prefix with a mixed body), PEM private-key blocks, bare high-entropy
    strings, and credentials in a URL *path* — on both `sanitize_auth_diagnostic`
    and `redact_secrets`; `process_output` never ends its truncated text inside
    a token. Known residuals are listed in the plan.
- `SECURITY.md` lines 120-122 currently claim redaction of "bearer tokens, API
  keys, bare provider tokens (`sk-`, `ghp_`, `github_pat_`), common secrets, URL
  userinfo, authorization codes, and auth-bearing query parameters". This plan
  **widens** that claim (shape-based redaction, URL-path credentials) and adds
  a caveat (prose-preserving gates; residuals above). **The implementer edits
  SECURITY.md**, not this plan (the file is under the ledger checked by
  `scripts/check_security_claims.py`; the C-13 ledger rows are unaffected — the
  probe stays invisible to the engine, and `test_the_policy_defaults_probe_stays_invisible_to_the_engine`
  pins that).
- **Implementer note — GitHub push protection.** The first push of this plan
  was rejected (`GH013`, "Push cannot contain secrets") because three corpus
  samples matched GitHub's detectors exactly: a Stripe test key
  (`sk_test_` + 24), a DigitalOcean token (`dop_v1_` + 64 hex) and a Slack token
  (`xoxb-` + numeric segment(s) + a mixed-case body; 7-digit and single-segment
  variants were still caught, so that entry was dropped — the issue's own
  `xoxb-2444-2444-abcdefghijklmnop` exercises the same vendor rule and is not
  flagged). The corpus now uses samples that keep the *shape each rule needs*
  and nothing more (a 17-char Stripe body, 48 hex); the comment above `CREDENTIALS` says so. Any sample
  added later must be checked the same way, or the implementation PR will be
  blocked at push. `AKIAIOSFODNN7EXAMPLE` (AWS's documented example key),
  `ghp_16C7e42F292c6912E7710c838347Ae178B4a`, the `AIza…` and `sk-proj-…`
  samples were **not** flagged.
- `PolicyManager.truncate_output` gains an additive keyword-only
  `original_size` parameter and `redact_auth_url` an additive keyword-only
  `path_transform`; every existing call is unchanged. No CHANGELOG line for
  either beyond the `### Security` entry, which should say that redaction
  now runs before truncation and that passes no longer see each other's
  output.
- The diagnostic engine's URL output changes shape: a redacted query value
  is `[REDACTED]`, not `%5BREDACTED%5D`, and the URL is otherwise left as
  written; `sanitize_public_auth_url`/elicitation URLs keep `main`'s form.
- SECURITY.md's key list (line 120-122) should name the widened set, or
  refer to `AUTH_DIAGNOSTIC_SECRET_KEYS` / `WEAK_SECRET_KEYS` — implementer.
- No README change: operators see the same `[REDACTED]` marker; the
  `redaction.patterns` policy key is unchanged in shape (operators who set their
  own patterns still displace the defaults, including the fixed `token` one).

## Dependencies & order

1. `src/pmcp/auth.py` — the shape block and the three keyword rules (all new
   symbols), then the `sanitize_auth_diagnostic` body. Nothing else compiles
   against them until they exist.
2. `src/pmcp/policy/policy.py` — `_TRAILING_PARTIAL_TOKEN_RE`, the defaults,
   `truncate_output`, `redact_secrets`.
3. `tests/test_redaction.py` from `## Test bodies`.
4. `uv run pytest tests/test_redaction.py tests/test_auth.py tests/test_policy.py tests/test_project_source_consent_policy.py tests/test_trust_boundaries_e2e.py`
   — must be green with `tests/test_auth.py` **unmodified**.
5. Mutation table (below) — every row RED, every diff confirmed.
6. `SECURITY.md` claim + CHANGELOG, then the full suite and the CI gates.

## Verification

```bash
cd <worktree>
uv run pytest tests/test_redaction.py -q --cov-fail-under=0                      # 129 passed
uv run pytest tests/test_auth.py -q --cov-fail-under=0                           # 128 passed, file byte-identical to main
uv run pytest tests/test_policy.py tests/test_project_source_consent_policy.py \
              tests/test_trust_boundaries_e2e.py -q --cov-fail-under=0           # C-13 proofs + truncation pins
uv run pytest --collect-only -q tests/test_redaction.py | grep -c '::'            # 129 (a -k that matches nothing exits 0)
uv run ruff check src/ tests/                                                     # CI gate
uv run ruff format --check src/ tests/                                            # CI gate
uv run mypy src/                                                                  # CI gate -- measured: Success: no issues found in 49 source files
uv run python3 scripts/check_security_claims.py                                   # after the SECURITY.md edit
uv run python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md        # blocking inconsistencies: 0
# full suite -- detached, never in the foreground (runtime/ boots gateways; ~4 100 tests)
nohup uv run pytest tests/ -q > /tmp/pmcp-234-full.log 2>&1 & disown
tail -n 3 /tmp/pmcp-234-full.log
```

Measured this session on the revised tree: `test_redaction.py` **129 passed** (10.9 s);
`test_auth.py` (HEAD copy) **128 passed**; the earlier spike-era targeted run of
`test_redaction.py tests/test_auth.py tests/test_policy.py
tests/test_project_source_consent_policy.py tests/test_trust_boundaries_e2e.py`
**243 passed**, and in rev 2 the same four files (`test_auth.py`, `test_policy.py`,
`test_project_source_consent_policy.py`, `test_trust_boundaries_e2e.py`)
**186 passed** against the rev-2 patch; `ruff check` and `ruff format --check` on `src/ tests/` **clean**;
`check_plan_consistency.py` **blocking inconsistencies: 0** (same on
`origin/main`). Full suite on the revised tree (detached, `-m 'not live'`, 4 164
collected): **4136 passed, 3 skipped, 25 deselected in 11:20**.

## Mutation evidence

Each mutation was applied to the revised tree with a scripted single-occurrence
text replacement, **confirmed applied** by `diff -u` against the scratchpad
baseline copy of the revised file (the diff line count is in the evidence
column — a baseline `git diff --stat` would show the whole uncommitted spike, not
the mutation, so the baseline is the revised copy), the named node ids were
collected with `--collect-only` and then run, and the file was restored **from
the baseline copy** (never `git checkout --`, which would restore HEAD's
pre-fix code). The runner is reproducible from the patch above:
`scratchpad/mutations/run_mutations.py`.

Two mutants **survived the first run**, and the tests were strengthened rather
than the rows dropped: M05 (the `\bbearer` boundary) survived because the
`Bearer` value gate alone already kept `failed` in `token secret-bearer failed`,
so `test_bearer_is_a_scheme_not_a_word` gained `secret-bearer hunter2` and
`non-bearer 2024-01-01 report`, where only the boundary keeps the following
value; M09 (the split guard) survived because the engine's shape pass redacted
the real base64 probe before the operator pattern ever saw it, so the test now
probes with a uniform-hex padded value the engine leaves alone (and asserts
that it does). Rev 2 adds M18-M20, one per board defect, each the *rev-1 code*
put back (M19 restores rev 1's whole back-up block, so both the 64-char bound
and the offset-0 guard are exercised), and corrects M13's reason: only the
engine nodes go red there, the policy twin still passes via the `\bsk-`
default (grok). Rev 3 adds M21 (rev 2's `Bearer` value class: `{"note": "see
Bearer abc123def456"}` loses its `"}`), M22 (the keyed-values pass removed
from its first position: the pass is load-bearing, not only the ordering),
M23 (an unterminated quoted value is not a value: the sweep leaks again) and
M24 (rev 2's `Authorization` rule: a JSON-quoted key is not matched at all).
M20's anchor was re-targeted to the rev-3 value regex — the runner reports a
mutation whose text no longer exists as NOT APPLIED rather than as a pass,
which is how the stale anchor was caught. Rev 4 adds **M25** (mutate-in-place
composition: the URL pass rewrites the text before the other passes read it —
red on the round-3 case and on the static property test) and **M26**
(`main`'s order, cut then redact — red on the truncation property sweep and
two named cases), re-targets M19 to the window slack (`16384 → 0`: a 4 000-char
value straddling the cap leaks), M11/M13/M16/M22/M24 to their rev-4 anchors,
and retires M10 and M23 (the back-up and the end-of-line rule they mutated no
longer exist; M26 and M25 cover their classes). Rev 5 adds **M27** (`passwd`
dropped from the strong set while the policy default still names it: the
derived-key property goes red on its own) and re-targets M07 and M24 to their
rev-5 anchors. Rev 6 adds **M28** (rev 5's whole-URL rewrite span → the query
leaks; red on the named test and the composition property), **M29** (rev 5's
window: the whole redacted window returned → the edge leaks; red on the
window property), **M30** (rev 5's marker inertness → `password=hunter2
[REDACTED]` survives; red on the named test and the composition property),
**M32** (Authorization plain-word gate off) and **M33** (`==` as a
separator), re-anchors M08/M16/M24/M26 to the rev-6 code (M16 now re-adds
the spike's path redaction *inside* `redact_auth_url`), and adds the
composition property to M25's node list. A mutation making containment
unconditional again is **not** listed: with every remaining outer
replacement `[REDACTED]` or empty it is an equivalent mutant on this corpus —
the reason is stated so no one mistakes its absence for an omission. Final
run, all 30 rows:

| id | verdict | evidence | why it is red |
|---|---|---|---|
| M01-ws-gate-off | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | whitespace keyword rule redacts any value again: `token bucket` -> `token [REDACTED]` |
| M02-no-payload-bound | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | an 8 KB base64 blob is scored as a credential and replaced whole |
| M03-whole-run-with-joiners | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.08s | `pmcp-7d9f8b6c5-x2k9q` scored whole reads as random and is redacted |
| M04-hex-without-0x | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | `<Foo object at 0x7f3a2b1c4d50>` loses its address: the `x` breaks uniform hex |
| M05-bearer-word-boundary | RED | 4 diff lines; 1/1 nodes collected; 1 failed in 0.04s | `\bbearer` fires inside the hyphenated word `secret-bearer` (main's bug) and redacts the next word: `secret-bearer hunter2` -> `secret-bearer [REDACTED]` |
| M06-bearer-gate-off | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | `Missing bearer token` -> `Missing bearer [REDACTED]` |
| M07-code-qualifiers-off | RED | 3 diff lines; 1/1 nodes collected; 1 failed in 0.04s | `error_code=AADSTS50011` and `reason_code=E-1234` are treated as OAuth codes and redacted |
| M08-sep-spans-newline | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | `Missing bearer token:\nthe bearer of` -> the next line's first word is redacted |
| M09-split-guard-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | base64 padding is taken as the separator: `dXNlcjpwYXNzd29yZA= [REDACTED]` |
| M11-cut-before-redact | RED | 1 diff lines; 1/1 nodes collected; 1 failed in 0.04s | the cut at 400 leaves nine characters of the token, which no longer has a redactable shape |
| M12-main-default-token-pattern | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | main's default pattern redacts the word after `token` on the policy surface even though the engine no longer does |
| M13-prefixed-rule-off | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | `sk-abcdef123456` has a prefix but too little entropy for the transition score (engine nodes only: the policy-surface twin still passes via the `\bsk-` default pattern) |
| M14-prefixed-accepts-alpha-body | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.06s | the engine eats `ghp_abcdefghijklmnop`, and the C-13 proof that a DEFAULT pattern applies goes vacuous |
| M15-aws-vendor-shape-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | `AKIAIOSFODNN7EXAMPLE` is uniform upper-case with one digit: the transition score cannot see it |
| M16-spike-url-path-redaction | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | the inherited spike's `redact_auth_url` change: the Okta authorization-server id in an elicitation URL is redacted |
| M17-single-number-camel-clause-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | `Oauth2ClientError` (one embedded number, ratio 0.375) is scored opaque |
| M18-payload-bound-after-path-split | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | a slash-leading payload is split at every `/` and each piece scored: `/9j/...` JPEG base64 comes back as `[REDACTED]/[REDACTED]/...` |
| M19-redaction-window-without-slack | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.08s | a 4 000-char keyed value straddling the cap ends outside a slack-less window, is not matched, and its first characters survive the cut |
| M20-quoted-value-stops-at-escaped-quote | RED | 2 diff lines; 2/2 nodes collected; 1 failed, 1 passed in 2.79s | JSON {"password": "a\"hunter2"} -> {"password": [REDACTED]hunter2"} on both surfaces; the property test finds it in the first few hundred passwords |
| M21-bearer-value-eats-quotes | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | rev 2 Bearer value class eats the closing quote and brace: {"note": "see Bearer abc123def456"} -> {"note": "see Bearer [REDACTED] |
| M22-keyed-values-pass-removed | RED | 1 diff lines; 3/3 nodes collected; 3 failed in 0.06s | no keyed-value pass at all: {"password": "hunter2"} survives |
| M24-authorization-rule-misses-quoted-key-and-eats-quotes | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | rev 2 Authorization key syntax: the quote after a JSON key defeats authorization\s*[:=], so {"authorization": "Bearer abc123def456", "x": 1} is not matched at all |
| M25-mutate-in-place-composition | RED | 1 diff lines; 3/3 nodes collected; 1 failed, 2 passed in 3.25s | revs 1-3 composition for one pass: the URL pass rewrites the text before the others read it, removes the backslash escaping a quote, and {"password": "https://...\"hunter2"} -> {"password": [REDACTED]hunter2"}; the property test finds the class on its own |
| M26-truncate-then-redact | RED | 19 diff lines; 3/3 nodes collected; 3 failed in 0.05s | main order (cut, then redact): the cut leaves ghp_16C7e, "hunter2 , "hunter2\ -- shapes no rule recognises; the sweep reports leaks |
| M27-passwd-dropped-from-the-strong-set | RED | 1 diff lines; 1/1 nodes collected; 1 failed in 1.58s | `passwd` is still named by the policy default `(secret|password|passwd|pwd)` but no longer by the engine: `{"passwd": "…"}` leaks and the derived-key property reports it |
| M28-url-rewrite-span | RED | 2 diff lines; 2/2 nodes collected; 1 failed, 1 passed in 0.63s | rev 5 URL pass: one span for the whole URL with a rewritten replacement; every keyed/shape span inside the query is dropped as contained, so ?pwd=hunter2 and ?access=ghp_... leak |
| M29-window-edge-emitted | RED | 3 diff lines; 1/1 nodes collected; 1 failed in 0.06s | rev 5 window: spans applied to the whole window and the far edge returned when redaction shrank it under the cap; a value straddling max_bytes+16384 shows its head |
| M30-marker-inertness | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.61s | rev 5 marker rule: any span touching a literal [REDACTED] in the INPUT is dropped, so password=hunter2[REDACTED] survives |
| M32-authorization-gate-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | authorization: none -> authorization: [REDACTED] |
| M33-double-equals-is-a-separator | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | if token == expected: -> if token =[REDACTED] expected: |

## Acceptance criteria

- [ ] **Prose direction.** `PROSE` in `tests/test_redaction.py` survives
      `sanitize_auth_diagnostic(…, max_length=None)` **and**
      `PolicyManager().redact_secrets` byte-identically
      (`test_prose_survives_the_engine_byte_identical`,
      `test_prose_survives_the_policy_surface_byte_identical`). It contains, in
      the mangling position, every keyword the HEAD rule mangled plus the
      diagnostic shapes the spike mangled (JSON-RPC `code`, `0x` addresses, pod
      names, `bearer token`, a keyword at end of line).
- [ ] **Credential direction.** All 28 `CREDENTIALS` entries vanish on both
      surfaces with their `must_survive` context intact — including the four
      the issue measured (`sk-live-…` in a URL path, `AKIA…`, `ghp_…`, `xoxb-…`)
      and the classes they stand for (bare random alnum, base64 with `/` and
      `=`, prefixed hex/short/long/low-entropy bodies, a webhook path, PEM,
      JSON-quoted values, flags, camelCase keys, OAuth codes bare/qualified/hex,
      headers with qualifiers, cookies, bare `Bearer`).
- [ ] `redact_auth_url("https://dev-1.okta.com/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize?state=ok")`
      is byte-identical (the login URL is never shape-redacted), while the same
      opaque path segment in a diagnostic is
      (`test_a_url_path_credential_is_redacted_in_diagnostics_not_in_the_url`).
- [ ] An 8 000-char base64 blob survives both surfaces; a 200-char random
      alnum run does not (`test_payload_sized_runs_are_data_not_credentials`).
- [ ] `ghp_abcdefghijklmnop` is invisible to the engine and still redacted by
      the policy defaults; `tests/test_project_source_consent_policy.py` and
      `tests/test_trust_boundaries_e2e.py` C-13 tests pass unchanged.
- [ ] `tests/test_auth.py` is byte-identical to `main` and passes (128).
- [ ] `sanitize_auth_diagnostic(text, max_length=400)` on a token starting at
      offset 391 ends in `[REDACTED` — never `ghp_…`; `process_output(…,
      max_bytes=300)` never leaves `ghp_` in a result cut inside the token, and a
      400-byte single run still truncates at 200.
- [ ] A slash-leading payload survives both surfaces byte-identically:
      `"/" + "4eC39HqLyjWDarjtT1zdp7dc" + "/" + "A"*8190`, and
      `"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgH" + <6 000 chars of
      seeded random base64 with > 50 slashes>`; a 360-char REST route still
      loses its opaque segment (`test_a_slash_leading_payload_is_not_split_into_scored_pieces`).
- [ ] `process_output("a"*120 + " /" + "b"*68 + "/" + TOKEN + " " + "c"*100, redact=True, max_bytes=300)`
      contains no `ghp_`; the same with a 300-`b` path and `max_bytes=450`
      contains no `ghp_` and keeps the path; `"b"*400` at `max_bytes=300` yields
      only the truncation marker and `"b"*1000` at 600 keeps 500 `b`s
      (`test_process_output_never_ends_on_a_partial_token_after_a_path`,
      `test_process_output_still_truncates_a_single_giant_run`).
- [ ] **Composition.** `{"password": "https://example.test/?token=abc123def456\"hunter2"}`
      → `{"password": [REDACTED]}` on both surfaces, and
      `see https://example.test/?token=abc123def456&page=2.` keeps its host and
      route with the query value `%5BREDACTED%5D`
      (`test_no_pass_can_consume_a_boundary_another_pass_needs`); M25 red.
- [ ] **Truncation order.** `"a"*177 + " " + json.dumps({"password": "hunter2\\tail"}) + "c"*100`
      at `max_bytes=300` contains no `hunter2`; a 4 000-char value straddling
      the cap is `[REDACTED]` (`test_truncation_after_a_backslash_cannot_expose_a_password`,
      `test_the_redaction_window_reaches_past_the_cap`); M26 and M19 red.
- [ ] **URL queries.** `https://example.com/?aws_secret_access_key=wJal…`,
      `…?access=ghp_…`, `…/login?pwd=hunter2`, `…?q=<24 random alnum>&page=2`
      → the value `[REDACTED]`, the rest of the URL as written, on both
      surfaces; `redact_auth_url` byte-identical to `main` (M28 red).
- [ ] **Window edge.** With a shrinkable PEM prefix and a quoted password
      straddling `max_bytes + 16384`, no cut emits `hunter2` and the output
      never exceeds `max_bytes` (M29 red).
- [ ] **Markers.** `password=hunter2[REDACTED]` → `password=[REDACTED]`,
      `{"password": "hunter2 [REDACTED]"}` → `{"password": [REDACTED]}`;
      `password=[REDACTED]`, `password= [REDACTED]`, `{"password":
      [REDACTED]}` are fixed points on both surfaces (M30 red).
- [ ] **Composition property** ≥ 4 000 spans checked, 0 leaks; **never worse
      than main**: all 42 corpus inputs pass on both surfaces.
- [ ] `authorization: none` and `Set the Authorization:\nheader first`
      survive; `if token == expected:` survives (M32, M33 red).
- [ ] **Key coverage.** `test_property_every_declared_key_is_redacted_in_every_form`
      derives its keys from the source (31 keys) and checks > 5 000 strings
      with 0 leaks; `{"passwd": "Xk9#mQ2vL"}`, `{"pwd": …}`, `{"private_key":
      …}`, `{"credentials": …}`, `{"auth": …}` → value `[REDACTED]`;
      `{"Authorization": "Xk9 mQ2vL"}` → `{"Authorization": "[REDACTED]"}`;
      `{"Authorization": "(Xk9mQ2vL"}` and `password=(Xk9mQ2vL)` redacted;
      `credentials: include` and `auth=basic` kept; M27 red.
- [ ] **Properties.** `test_property_every_keyed_password_is_redacted_whole`
      checks > 10 000 strings with 0 leaks;
      `test_property_truncation_never_exposes_a_keyed_password` sweeps
      > 20 000 cuts with > 15 000 inside a value, asserts every value is hit
      at least once, 0 leaks; `test_property_prose_survives_byte_identical`
      2 000 lines byte-identical on both surfaces.
- [ ] `{"password": "hunter2 Bearer test-token"}` → `{"password": [REDACTED]}`,
      `{"authorization": "Bearer abc123def456", "x": 1}` → `{"authorization": "[REDACTED]", "x": 1}`,
      `{"note": "see Bearer abc123def456"}` → `{"note": "see Bearer [REDACTED]"}`
      on the engine, and neither `hunter2` nor `abc` survives the policy surface
      (`test_an_earlier_pass_never_eats_the_boundary_a_later_pass_needs`).
- [ ] The truncation sweep — prefixes 50/120/177/190 × `hunter2 tail`,
      `correct horse battery staple`, `p4ss w0rd!` × every `max_bytes` from
      `prefix+80` to `prefix+200` through `process_output(redact=True)` —
      leaks no word of any password, **and** the test asserts at least one cut
      per combination lands strictly inside the quoted value
      (`test_truncation_never_leaks_a_quoted_multi_word_password`).
- [ ] `{"password": "a\"hunter2"}` → `{"password": [REDACTED]}` and
      `{'password': 'a\'hunter2'}` → `{'password': [REDACTED]}` on both
      surfaces (`test_a_quoted_value_runs_to_its_closing_quote`).
- [ ] Custom pattern `[A-Za-z0-9+/]{16,}={1,2}` on `basic dXNlcjpwYXNzd29yZA== auth`
      → `basic [REDACTED] auth`; `mykey: abcdef` → `mykey: [REDACTED]`.
- [ ] Every row of the mutation table is RED for its named reason, with a
      non-empty confirmed diff.
- [ ] `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/`
      clean; full suite green; `check_security_claims.py` green after the
      SECURITY.md edit.

## Non-goals

- Replacing `AUTH_DIAGNOSTIC_SECRET_KEYS` / `AUTH_SECRET_QUERY_KEYS` or
  changing URL query redaction.
- Redacting inside `redact_auth_url` paths (elicitation URLs must stay
  openable — defect 1).
- Making `process_output` redact before it truncates (a full-output redaction
  pass on multi-MB tool results, and it would put whole base64 payloads through
  the engine); the bounded back-up closes the exposure at the cut.
- Detecting MIME-wrapped base64 payloads as payloads (documented residual FP).
- Entropy (Shannon) scoring; the class-transition score was kept because it
  separates camelCase and hex from random text where entropy thresholds do not.

## Unverified

- **Full suite: verified, with one caveat.** rev 6: **4193 passed, 3 skipped, 25 deselected in 580.25s (9:40)**, run detached with `-m 'not live'` on the rev-6 tree (the exact `src/` hashes embedded above) after the 30-row mutation table; `uv run mypy src/` on the same tree: Success, 49 source files.
  The targeted files that exercise every changed symbol (`test_redaction.py`,
  `test_auth.py`, `test_policy.py`, `test_project_source_consent_policy.py`,
  `test_trust_boundaries_e2e.py`) were run and are green.
- `scripts/check_security_claims.py` was not run: SECURITY.md is not edited by
  this plan.
- Throughput numbers are from one host, one run each (no repetition).
- The residual table's "expected gap 32" for base64url is arithmetic (2/64 per
  character), not measured on a corpus of real tokens.

## Execution Policy

- execute: effort=medium, reason=the patch is fully specified and measured, but
  it is security-sensitive and the prose/credential corpora must both stay
  green while SECURITY.md is updated; the mutation table must be re-run on the
  implementer's tree, not trusted from this plan.
