# Detailed plan: secret redaction — `main`'s redactor as a floor, shape-based rules on top (Consiliency/pmcp#234)

> **Revision 14 (2026-09-27): rev 13's board, round 3 (claude seat,
> DISAGREE on one blocker).**
>
> Rounds 1 and 2 stayed closed, and the floor held on the seat's 967 032
> surface cases.
>
> **The blocker, B-5, is B-3's class, not yet closed for re-encoded URL
> components.** The replay turns such a component into ONE piece that
> stands for all of itself. Every later match inside the piece copied the
> whole piece, and so did the floor spans it produced. That is k × n in
> both time and memory.
>
> - The work count did see B-5: it grew 15.6× per 4× of input.
> - The shape corpus missed it, because no shape repeated a
>   match-producing unit inside one such piece.
>
> **Rev 14 closes the class.**
>
> - **A step spells out each input character once.** A later match of the
>   same step reads those characters as the intermediate text itself. This
>   is a per-step frontier in `_Tracked.source`.
> - **The floor copies removed text only where a predicate reads it** (the
>   syntax parts), and applies a span over one input range once.
> - **Every place a span or a value can be larger than the match that
>   produced it was audited** ("Complexity argument"), and each is now
>   charged exactly what it copies.
> - **A peak-memory (tracemalloc) linearity check** runs beside the work
>   count.
> - **Shapes inside atomised pieces.** A third shape family repeats each
>   match-producing unit inside each context the replay atomises: a
>   re-encoded query key or value, a re-cased host, the unparseable-port
>   arm, a header value, a JSON string. That is 476 shapes. On rev 13, 156
>   of them grow super-linearly; here, none do.
> - **S-1:** the 256-character windowed searches are now backward walks.
>
> B-5 is red on rev 13 (`4b5cac8`) on both the work count and the memory
> check, green here, and killed by a mutant.
>
> Code: `origin/wip/234-redactor-floor-code` @ `b664588`, embedded below
> against `main` @ `876fd33`, which applies unchanged to `main` @ `8de49e4`.
>
> **Revision 13 (2026-09-27) — rev 12's board, round 2 (claude seat,
> DISAGREE).** Round 1's findings stayed closed, and the floor held again
> on the seat's 967 032-case differential. The seat found two more
> quadratic paths that the rev-12 sweep could not build, because they sit
> in Python loops that only a COMPOSITION of passes reaches:
>
> - **B-3:** a marker that the URL step synthesised stood for the whole
>   URL, so a later match over k such markers built a value k times the
>   URL long, in both time and memory.
> - **B-4:** every keyword match asked every resource name.
>
> It also showed that rev 12's timing tests flake under load (T-1, very
> likely the one failure rev 12 left unexplained), and that N3 still read
> a camelCase multi-word key as glued (N-5).
>
> Rev 13:
>
> - **B-3:** each synthesised marker stands for exactly the input it
>   replaced, or for nothing, and `source()` emits each input character
>   once.
> - **B-4:** a binary search per question. `widen_over_escapes`, which
>   the audit found in the same class, now reads backslash runs from one
>   precomputed pass.
> - **Linearity is asserted on a deterministic work count, not on the
>   clock.** Every loop and string build of the redactor adds what it
>   iterates over or copies to a counter that only the tests switch on.
>   The tests assert that this count grows linearly from 16 KB to 64 KB
>   to 256 KB on every entry point.
>   - A regex's own backtracking is the one cost a count cannot see. It is
>     argued pattern by pattern ("Complexity argument") and checked on the
>     clock, with a 64× bound over 16× the input (linear gives 16×,
>     quadratic 256×).
>   - The only clock tests left in the default tier are smoke caps or use
>     that ratio bound.
> - **Shapes from the loops, not only from the regexes.** 24 120
>   compositions join the 46 096 regex-derived shapes: two leads that arm
>   different passes, interleaved units, markers inside markers, and
>   nested JSON and header contexts. Every repro from both rounds runs in
>   the default tier.
> - **N-5:** a case change joins words, as `_` and `-` do.
>
> Each finding fails on rev 12 (`b7b071d`), passes here, and is killed by
> a mutant ("Rev 12 board findings"). Code:
> `origin/wip/234-redactor-floor-code` @ `4b5cac8` (rev 13), embedded then as a
> patch against `main` @ `876fd33`. `main` is now at `8de49e4`, which
> differs from `876fd33` only by one plan document, so the patch applies
> to it unchanged.
>
> **Revision 12 (2026-09-27) — rev 11's board, round 1 (claude seat,
> DISAGREE).** The floor held: the seat's own differential (967 032
> surface cases, generators independent of this plan's) found no input
> where rev 11 keeps a secret `main` removed without a named predicate
> firing. It found instead (a) three inputs whose cost grew quadratically
> in the floor's own helpers and one predicate, and (b) predicates that
> fired wider than their approved class. Rev 12 fixes each class, not the
> instance:
>
> - **Linearity.** The closing-wrapper search, the unindented-break test
>   (floor and additive rules) and per-span predicate evaluation are now
>   one pass each; predicates are asked once per part of a match. A
>   **generated timing sweep** derives 46 096 adversarial shapes from the
>   redactor's own 66 regular expressions (every repeatable unit, after
>   each trigger word, before each failing tail) and runs them through
>   every entry point, asserting linear growth.
> - **N10** fires only on a key that is a segment of an `arn:`/`urn:`
>   name, with its separator and value inside the name.
> - **N3** counts only the run glued to the key word, never across `_`/`-`.
> - **D2's plain number** is at most 10 decimal digits; longer digit runs
>   and `0x` hex follow `main`.
> - The URL alignment labels every input position (a trailing or doubled
>   `&` was neither kept nor labelled).
>
> Each is red on rev 11 (`3e49b95`), green here, and killed by a mutant
> ("Rev 11 board findings" below). Code (rev 12): `origin/wip/234-redactor-floor-code`
> @ `b7b071d`.
>
> **Revision 11 (2026-09-26) — never worse than `main`, by construction.**
> Revisions 7 to 10 each re-implemented `main`'s redaction rules, passed their
> own never-worse differential, and were then shown by a fresh reviewer to
> redact less than `main` on some input. A re-implementation can always have
> a gap, and every gap is a leak. Rev 11 stops re-implementing. **`main`'s
> rules run unchanged and produce candidate spans (the floor); rev 10's rules
> only add to them; a floor span is dropped only when a named suppression
> predicate says so.** Never-worse-than-`main` then holds by construction, and
> review reduces to the predicates (18, listed below).
>
> The maintainer's decisions for this revision:
>
> 1. **Main as a floor** (the design above).
> 2. **Four syntax predicates approved:** `separator_syntax`,
>    `policy_keyword_word`, `scheme_word` and `wrapper_syntax`. Each keeps
>    only fixed vocabulary, whitespace, quotes or brackets, never content.
> 3. **N11 narrowed:** a `name=value` token after a keyword and whitespace
>    is kept only when its value is not credential-shaped, and never
>    directly after `Bearer` or `Authorization`.
> 4. **Rev 10's false-positive fixes that `main` does not make stay as
>    `main` behaves** (no new classes): a flag or bullet after a keyword, a
>    joiner before `bearer`, a policy value across a JSON string boundary,
>    and a `name=value` pair right after `Bearer`.
>
> Code (rev 11): `origin/wip/234-redactor-floor-code` @ `3e49b95` (merged with
> `main` @ `876fd33`), embedded below as one patch against `876fd33`, proven
> to apply and to reproduce the branch's files byte for byte. `main` already
> carries the linear keyword matcher (`src/pmcp/keyword_matcher.py`, PR 303);
> the floor uses it unchanged.

## Task

Close Consiliency/pmcp#234. The redactor between a downstream server's error
text and the agent's context (`sanitize_auth_diagnostic`, and
`PolicyManager.redact_secrets` / `process_output`) is keyword-anchored: it
redacts the word after `token`, `secret`, `session`, `password`, `bearer`
whether or not it is a credential, and lets keyword-less credentials
(`AKIA…`, `ghp_…`, `xoxb-…`, a token in a URL path) through. Deliver
separator-anchored keyword rules and shape-based rules, plus a prose corpus
that must survive byte-identical — **without ever redacting less than `main`
except by a named, maintainer-approved suppression.**

Surfaces (unchanged): `sanitize_auth_diagnostic` (E), `redact_secrets` (P),
`process_output` on a string (POs) and on a structured result (POd, the
dict-leaf path), and each on the four `json.dumps` spellings of a text leaf.
`redact_auth_url` / elicitation URLs are not changed.

## Design: `main` as a floor

### 1. The floor: `main`'s rules, replayed (`src/pmcp/redaction_floor.py`)

`main` applies its substitutions **sequentially**, each on the previous one's
output: the URL rule (`redact_auth_url` on every `https?://` run, trailing
`).,;` handed back), `authorization\s*[:=]\s*(bearer\s+)?[^\s,;]+`,
`\bbearer\s+[^\s,;]+`, the keyword rule, the JWT rule; on the policy surface
then each effective policy pattern with `main`'s split at the first `:`/`=`.
The floor **replays exactly that sequence** on a tracked string: every
character of the intermediate text carries the input position it came from,
so what `main` removed is known exactly, as spans over the input, each
attributed to the rule and match that removed it (its key, separator and
value as `main`'s regex read them).

- `main`'s constants are **frozen in the module** (`MAIN_AUTH_SECRET_QUERY_KEYS`,
  `MAIN_DIAGNOSTIC_SECRET_KEYS`, `MAIN_DEFAULT_REDACTION_PATTERNS`), so widening
  the live key sets can never move the floor.
- The keyword step runs through `main`'s own `pmcp.keyword_matcher`
  (`keyword_matches` with `main`'s frozen key set), exactly as `main` runs it.
- The URL step calls `main`'s `redact_auth_url` logic for real and aligns its
  output with the input component by component (scheme, userinfo, host,
  path, each query key and value, fragment). A re-encoded component maps as
  one unit, so a later partial removal marks the whole input component
  removed (over-approximation only). Removals are labelled `url.userinfo`,
  `url.query`, `url.fragment`, `url.overflow` (`main`'s `ValueError` arm
  keeps 400 characters) and `url.normal`. If the alignment does not
  reproduce `main`'s output exactly, the whole URL becomes one floor span
  (`url.fallback`; 0 on every corpus).
- The policy step replays the **operator's effective compiled patterns**,
  whatever they are, with `main`'s replacement semantics.

### 2. Suppressions: the only way a floor span is dropped

Each predicate is production code in `SUPPRESSIONS`, decided from the
match's own key, separator and value (plus the input just before it, to tell
a JSON escape's tail from a glued prefix), never from any output. A predicate
that reads the key fires only if it fires on **every** reading of the key (a
key that starts on an escape tail is read with and without it). The first
predicate that fires is recorded on the span.

| predicate | keeps | stated class |
|---|---|---|
| `separator_syntax` | whitespace, `:`/`=` and the opening quote a policy replacement took between separator and value | new, approved (rev 11) |
| `policy_keyword_word` | the literal `token`/`bearer` word `main`'s `(bearer|token)\s+…` default replaced with its value | new, approved |
| `scheme_word` | the `bearer ` word `main`'s Authorization rule took with the value | new, approved |
| `wrapper_syntax` | quotes, brackets and an escaping backslash at the **edges** of a Bearer/Authorization value (a quote inside the value goes with it) | new, approved |
| N3 | a prefix or suffix of more than 24 alphanumerics **glued** to the key word (no `_`/`-` and no case change between; an identifier; cost bound) | rev 10 decision 2 (narrowed, revs 12 and 13) |
| N10 | a key that is a **segment** of an `arn:`/`urn:` name (after a `:` or `/`, no `& , ; ( ) ? =` or whitespace since the name began), with a `:` separator and a value inside the name (read on the input) | N10 (narrowed, rev 12) |
| C12 | `code` under a diagnostic qualifier (37, listed) | C12 |
| C3a | `code` after a whitespace-only separator | C3a |
| C3 | a whitespace-only separator before a value that is not credential-shaped (`token bucket`) | C3 |
| N11 | a `name=value` token after a keyword and whitespace whose value is **not credential-shaped**, and **never directly after `Bearer`/`Authorization`** | N11, narrowed (rev 11) |
| N4 | a glued mixed-case key before whitespace (`Ed25519PrivateKey X509Cert`) | N4 |
| C4 | an operator run holding `==` or `::` before a non-credential value | C4 |
| C5 | a suffixed key with a non-credential value (`token_type=`, `password_length=`) | C5 |
| C6 | a glued key with a non-credential value, or a URL/ARN value | C6 |
| C7 | after an unquoted key, a value starting on `,`/`}` (JSON structure) | C7 |
| C8 | an unindented line break before a non-credential unquoted value | C8 |
| C10 | `code` with a plain word or number; under a non-OAuth qualifier any non-credential value | C10 |
| C11 | Authorization/Bearer followed by a plain word, bare or wrapped | C11 |

"Credential-shaped" is D2 throughout: not a plain word or number, and
digit-bearing and 6+ characters or punctuated and 8+. A **plain number**
is at most 10 decimal digits, optionally signed (rev 12): a longer run or
any `0x` hex is not plain, so C3, C10 and C11 never keep one. N6b (a quoted value in
a JSON leaf) is **not** a predicate: the floor redacts those values inside
their string.

### 3. The additive rules

Rev 10's engine (separator-anchored keyword rules over a wider key set,
keyword lists, whitespace keywords with a credential-shaped value, Bearer and
Authorization, URL components with percent-decoding, JWT, vendor shapes,
prefixed tokens, opaque-run scoring, PEM blocks) and rev 10's policy
defaults (now `_ADDITIVE_DEFAULT_PATTERNS`, applied beside each effective
`main` default) run unchanged in behaviour and only **add** spans.
`DEFAULT_REDACTION_PATTERNS` is `main`'s list again. The additive policy
split finds its separator in one pass (B3).

### 4. Merge, and the JSON guarantee

All spans are merged by `apply_redaction_spans` (rev 10's rules: a covering
`[REDACTED]`/empty span absorbs what it contains, overlaps merge). When the
text is a JSON document, a floor span is applied inside the string it starts
in: characters `main` removed outside string interiors are syntax (a
delimiting quote, `{}[],:`, whitespace) and are kept; a bare scalar `main`'s
span touched becomes `"[REDACTED]"`; a span never starts or ends inside an
escape (it widens to the whole escape; a span ending on an escape's backslash
leaves the escape whole). A redacted JSON document stays JSON, and a dict
result stays a dict wherever `main`'s did. The construction test asserts that
only characters in the syntax set are ever kept this way.

### 5. Unchanged from rev 10

Truncation (`process_output` redacts before it cuts, over a window of
`max_bytes + 16384` characters, and never emits text past the cap except
inside a span); the prose and credential corpora; the grammar generator and
its two tiers.

## The floor guarantee, and how it is proven

**Guarantee.** On every surface, every character `main` removes is removed,
unless a named predicate in `SUPPRESSIONS` dropped the floor span carrying
it; the JSON adjustment keeps only syntax characters.

| proof | what it checks | result |
|---|---|---|
| **Fidelity oracle** (`tests/_main_redactor.py`: `main`'s functions vendored verbatim) | the replay's final text equals `main`'s output byte for byte, E and P | tier 1: 0 mismatches, 0 URL fallbacks; tier 2 (825 143 texts × 2 surfaces): 0; JSON fuzz and 5 000 random strings: 0 |
| vendored `main` = recorded oracle | the vendored code reproduces `tests/fixtures/redaction_main_oracle.b64` on tier 1 | equal |
| **Keyword matcher equivalence** | `pmcp.keyword_matcher.keyword_matches` yields the regex's `finditer` tuples | 0 differences: 20 000 random fragment strings, 576 adversarial joiner runs, tier-1 texts (default); tier 2 on every spelling, 200 000 more strings and 576 longer runs (slow; regex in workers under a terminating time budget) |
| **Construction, part A** | every floor span no predicate dropped is covered by the applied spans; JSON keeps only syntax | 0 problems on tier 1 (12 535 rows), the board rows (7 648), tier 2 (153 648 more) and the JSON fuzz (1 500) |
| **Construction, part B** (independent of the floor's own spans) | piece-level against vendored `main`: every piece `main` removes and this output keeps is explained by a named predicate that fired on a floor span over it — no hand classification | 0 unexplained on all four corpora; 5 tier-2 pieces are `main`'s own re-encoding (a bare query key gains `=`), checked against `main`'s output and counted apart |
| **One test per predicate** | fires on its false positive, keeps none of a credential-shaped neighbour; the four syntax predicates never keep a credential-shaped removal | 18 of 18 |
| **B1–B5** (rev 10's board) | each red on `e79d75f`, green here, each with a killing mutant | below |

The board rows are the inputs the one-pair grammar cannot produce: the B1
multi-pair grid (`k1 op break k2 sep value`, 4 320), `bearer` after a joiner
or prefix (B2, 120), punctuation inside Bearer/Authorization values (B4,
157), 3 000 separators of 3–5 characters (B5), the board's URL probes
(userinfo, fragments, an unparseable port with a 420-character path, every
query key `main` redacts, an encoded key) and the `.env`/YAML multi-pair
rows.

**Suppressions fired on rev 12** (floor spans dropped / worse pieces
explained):

| predicate | tier 1 | board | tier 2 (beyond tier 1) | fuzz |
|---|---|---|---|---|
| `separator_syntax` | 5 500 / – | 13 932 / – | 86 335 / – | 39 / – |
| `wrapper_syntax` | 7 754 / – | 408 / – | 146 950 / – | 222 / – |
| `scheme_word` | 862 / – | 600 / – | 16 376 / – | 0 |
| `policy_keyword_word` | 77 / – | 252 / – | 751 / – | 38 / – |
| C3 | 10 564 / 10 506 | 9 / 0 | 75 274 / 74 018 | 37 / – |
| C12 | 2 400 / 1 272 | 0 | 11 407 / 3 259 | 0 |
| N10 | 1 941 / 41 (rev 11: 2 538 / 283) | 0 | 27 732 / 632 (rev 11: 38 814 / 3 876) | 0 |
| C11 | 1 398 / 209 | 675 / 0 | 26 316 / 3 383 | 45 / – |
| N3 | 1 353 / 596 (rev 11: 2 839 / 1 230) | 0 | 24 664 / 10 299 (rev 11: 52 800 / 22 669) | 0 |
| C5 | 555 / 439 | 0 | 12 262 / 9 122 | 0 |
| C10 | 463 / 387 | 135 / 0 | 2 884 / 2 118 | 0 |
| C4 | 338 / 236 | 55 / 0 | 4 536 / 3 329 | 5 / – |
| C3a | 168 / 114 | 0 | 3 179 / 2 259 | 0 |
| C8 | 48 / 33 | 4 860 / 0 | 1 164 / 963 | 0 |
| C6 | 30 / 13 | 0 | 861 / 595 | 82 / – |
| N4 | 13 / 0 | 0 | 344 / 212 | 0 |
| C7 | 7 / 0 | 0 | 61 / 0 | 24 / – |
| N11 | 0 / 0 | 0 / 0 | 14 / 14 | 0 |

Narrowing N3 and N10 moves a span to the next predicate that holds for it
(C3, C4, C5, C3a, N11 rise) or leaves it unsuppressed; no span is kept that
no approved class covers. Problems: 0 on every corpus; 5 tier-2 pieces are
`main`'s own re-encoding, counted apart.

(Fuzz objects carry no planted pieces, so part B has nothing to explain
there: "–".)

### B1–B5: red on rev 10, green on rev 11, and the mutant that kills each

| finding | input (examples) | rev 10 (`e79d75f`) | rev 11 | mutant (turns it red) |
|---|---|---|---|---|
| B1 multi-pair, `\n` value start | dict leaf `[db]\npassword =\nsecret = hunter2\n` | red: `hunter2` kept on POd and the serialised surfaces | green | no floor at all |
| B2 `bearer` after `-`/`_` | `mycli --bearer s3cr3tvalue`, `X-Bearer Zm9vOmJhcg==`, `access_bearer tok_12345` | red | green | the floor's Bearer rule gated with rev 10's lookbehind |
| B3 quadratic policy split | `redact_secrets("password" + ":=" * 33000)` | red (3.5 s) | green (< 1 s; the split alone < 0.1 s) | the per-position `strip` loop restored |
| B4 value stops at a quote or bracket | `x Bearer q7Zp2Lk9Wx4R"SECRETPART`, `Authorization: Bearer abcdef'SECRETPART`, `x Bearer 'hunter2x null` | red | green | the floor's Bearer/Authorization values cut at quotes and brackets |
| B5 4–5 character mixed separators | `k password: : hunter2x e`, `token\t= : hunter2x` | red | green | the separator run capped at 3 |
| N11 after the scheme (rev 11) | `x Bearer abcdef=SECRETPART end` | red (`SECRETPART` kept) | green | rev 10's N11 restored |

## Rev 13 board findings → rev 14

| id | finding | fix (the class) | red on rev 13 (`4b5cac8`) | green | mutant that turns it red |
|---|---|---|---|---|---|
| B-5 | a re-encoded URL component is one piece; every later match inside it copied the whole piece, and its spans covered and copied the whole piece too (time and memory) | a step spells out each input character once (a per-step frontier in `_Tracked.source`); floor_spans copies removed text only for the syntax parts, and applies one span per input range | work count, 2 KB → 8 KB: `https://h/?q=+` + JWT×n grows 13×, `sk-abcdef/`×n 14×; peak memory, 8 KB → 32 KB (POd): 14.6 MB → 219 MB. The seat's 256 KB repro: 2.1 GB | work and memory linear at 8, 32 and 128 KB (default) and 16, 64 and 256 KB (slow); the seat's 256 KB repro, 0.7 s at 256 KB (the default 400 cut) and 0.34 s for the 64 KB tool result | rev 13's `source` |
| Q1-a | `_Tracked.source` and floor_spans' removed text were charged less than they copy | each charged exactly what it copies | — | — | — |
| — | shape corpus: no match-producing unit repeated inside one atomised piece | the atomised-piece family (476 shapes) | 156 of 476 super-linear | 0 of 476 | — |
| S-1 | 256-character windowed regex searches (`_context`, the glued tail, the resource segment), each O(256²) per call | backward walks; key readings once per decision | the seat's 1 MB long-key shape: 13.7 s | 2.3 s (the `token-` run: 8.8 s, the additive rules' bounded-qualifier regexes; main ~0.1 s) | — |
| N-8 | N-5 keys before a space separator are kept through N4, which does not test the value | not changed (N4 as approved) | — | — | residual |

## Rev 12 board findings → rev 13

| id | finding | fix (the class) | red on rev 12 (`b7b071d`) | green | mutant that turns it red |
|---|---|---|---|---|---|
| B-3 | a URL-step marker stood for the whole URL, and `source()` expanded it once per marker (time and memory) | each synthesised piece stands for its own input range, or for none; `source()` emits each input character once, left to right | `process_output` on `https://h/?` + `password=x&`×n: 8 KB 0.222 s → 32 KB 3.498 s (15.8×); `Authorization: https://h/?q` + `&a+b`×n: 0.269 s → 2.716 s | 0.030 s → 0.145 s; the work count grows linearly at 16, 64 and 256 KB on every entry point | both of rev 12's mechanisms restored |
| B-4 | `_in_resource_name` asked every name for every match | a binary search over the sorted, disjoint names | `arn:x `×n + `tokens: [...]`×m: 0.016 s → 0.114 s (7.1×) from 8 to 32 KB | 0.012 s → 0.050 s; the work count grows linearly | rev 12's scan, with its per-call cost counted |
| — | `widen_over_escapes` scanned back from every span start (same class, found by the audit) | one precomputed pass | — | the work count grows linearly on a backslash run before escapes | — |
| T-1 | the ratio timing asserts flaked under load (3 of 6 loaded runs) | a deterministic work count; the clock only for regex backtracking, at 64× over 16× | — | three default-tier runs, one under four CPU burners, all green | — |
| N-5 | N3 read a camelCase or SHOUTING multi-word key as glued | a case change is a joiner for N3 | `passwordForTheProductionDatabaseServer=Hunter2abcX9` kept | 7 camelCase and SHOUTING keys redacted on E and P; a single-case glued run of 25 is still N3 | rev 12's N3 |
| N-6 | a plain number of up to 10 digits includes signed and leading-zero values | not changed (D2 as approved in rev 12) | — | — | residual |
| N-7 | a plain word has no length limit | not changed (D2) | — | — | residual |

## Complexity argument

n is the length of the text a surface redacts. Every function below does
O(n) work, or O(n log n) where it sorts spans. Each one adds what it
iterates over or copies to the test-only work counter
(`pmcp.redaction_floor.WORK`), and the counter is what the default and
slow tiers assert on.

**The floor (`src/pmcp/redaction_floor.py`)**

| function | iterates over | why bounded | counted as |
|---|---|---|---|
| `replay` | a fixed sequence of 5 steps, plus one per effective pattern | the pattern count comes from the operator's configuration, not from the input | per step |
| `_url_step` / `_url_pieces` / `_label_uncovered` | each URL once: parse, rewrite, align the components, compare with `main`'s output, label the rest | URLs are disjoint `finditer` matches, and each pass over one URL is linear in its length | `len(text)`, plus 4× and 2× the URL |
| regex steps (`_authorization_step`, `_bearer_step`, `_jwt_step`, `_policy_step`) | one `finditer` over `cur`, with O(match) work per match | the matches of one pass are disjoint | `len(cur)`, plus each match |
| `_keyword_step` | `main`'s matcher (`pmcp.keyword_matcher`, equivalence-tested against the regex) | linear on `main` | `len(cur)`, plus each match |
| `_Tracked.rewrite` | `cur` once, and each edit's replaced characters once | edits are disjoint | `len(cur)`, plus the pieces |
| `_Tracked.raw_ranges` | the match's slice of `org`, then one sort | one slice per match, and the slices are disjoint | slice × log |
| `_Tracked.source` | the match's slice of `rep`; copies input a step has not spelled out yet, and reads the rest as the intermediate text | every piece stands for input at or after its predecessor's, in input order, so a per-step frontier makes each step copy each input character at most once, whatever pieces are shared between matches (B-3, B-5) | the slice, plus exactly what it copies |
| `_context` | a backward walk of at most 256 characters | a fixed window | the characters walked |
| `_header_value_spans` / `_wrap_close_start` | the value's opening run forward and its closing run backward, once each | one pass each | the closing run |
| `floor_spans` | each span once; the predicates once per part of a match; the removed text of the syntax parts only; each input range applied once | a value part may be one of many spans over one many-character piece, so its text is never copied (B-5); the syntax parts are bounded by their match; key readings are capped by `_MAX_READ_KEY` and computed once per decision | the copied removed text, plus 18 × the lengths of the strings a decision reads |
| `key_readings` / `_glued_tail` / `_key_is_resource_segment` | backward walks over contexts of at most 256 characters, and one forward search over the resource-name tail | fixed windows | the characters walked |
| `unindented_break` | one reverse search | — | its argument |
| `json_layout` / `_JsonLayout` | `json.loads` once, and the string tokens once | — | 2 × the text |
| `adjust_for_json` | each floor span's characters once, plus widening to escape boundaries (at most 6 characters) and a scalar's start | spans are disjoint within a match, and a scalar lookback is bounded by the scalar | each step |

**The additive rules (`src/pmcp/auth.py`)**

| function | iterates over | why bounded | counted as |
|---|---|---|---|
| `_keyword_sep_spans`, `_keyword_list_spans`, `_keyword_ws_spans`, `_bearer_spans`, `_authorization_spans`, `_url_spans`, `_shape_spans` | one `finditer` each, with predicates over the match's own groups | the matches of one pass are disjoint; the qualifier and glued runs in the key patterns are bounded (8 segments, 24 characters) | the text, plus each match |
| `_in_resource_name` | one `finditer` for the names, then one binary search per question | — | the text, plus 1 per question |
| `_run_spans` / `_run_is_opaque` / `_reads_as_route` | each opaque run once | runs are disjoint | the run |
| `_url_component_spans` / `_covers_anything` | each URL's pairs; a percent-encoded value is decoded and redacted again, at most 3 levels deep | each level's text is a substring of the previous level's | the URL; each recursive call counts its own text |
| `widen_over_escapes` | one pass computing the backslash runs, then each span once | — | the text, plus the spans |
| `merge_redaction_spans` / `apply_redaction_spans` | the marker scan, one sort, one merge, one join | — | the text, plus spans × log |

**Where a span or a value can be larger than the match that produced it.**
These are the places B-3 and B-5 came from. Each was audited for rev 14:

- **A URL step marker** (a redacted query value) stands for its own value
  only; an added `=` stands for nothing (rev 13).
- **A re-encoded URL component** (a key or value that changes when it is
  re-encoded, a re-cased host) is one piece, and every match inside it has
  a floor span over the whole piece. The spans are cheap (one range each);
  the removed text is not copied for a value part, and an input range is
  applied once. `source` reads the piece once per step (rev 14).
- **A marker a regex step leaves** stands for the input its edit replaced.
  Every character it could expand to is behind the per-step frontier.
- **The URL alignment fallback** makes the whole URL one piece. It never
  occurs on any corpus; if it does, the per-step frontier bounds it like
  any other piece.
- **JSON adjustment** reads a span's characters once. Spans are applied
  once per input range.

**The policy surface (`src/pmcp/policy/policy.py`).**
- `redaction_spans` adds one `finditer` per additive pattern and, per match, one split (`_value_separator`, a single pass).
- `process_output` redacts one window of `max_bytes + 16 384` characters and sorts its spans once.

**Regular expressions and backtracking.** The work count sees each regex
call only as the text it scans, not as the backtracking inside the engine.
So each pattern has to be linear by its own structure:

- **`main`'s patterns.** The URL, Authorization, Bearer, JWT and policy-default
  patterns each put a literal or a single class ahead of every quantifier,
  and none nests quantifiers. The keyword rule does not run as a regex:
  the linear matcher replaces it.
- **The floor's own patterns.** They are short literal alternations
  (`_POLICY_KEYWORD_RE`, `_SCHEME_WORD_RE`), fully anchored single-class
  runs (`_CONTEXT_RE`, bounded to 256 characters), JSON tokenisers with
  disjoint alternatives, and key-word lookaheads over keys capped at 256
  characters.
- **The additive rules.** The qualifier and glued runs are bounded (`{1,8}`
  segments, `{1,24}` characters), the value classes are single negated
  classes, and the PEM body never scans past the next `BEGIN`.
- **Replaced searches.** The earlier end-anchored searches (the
  closing-wrapper search and the unindented-break regex) are now scans
  (B-1 and B-1b).

The clock checks this: the slow tier covers all 46 096 regex-derived
shapes, and the default tier covers the two replaced searches, each with
the 64× bound.

## Rev 11 board findings → rev 12

| id | finding | fix (the class) | red on rev 11 (`3e49b95`) | green | mutant that turns it red |
|---|---|---|---|---|---|
| B-1 | closing-wrapper search anchored at the end, in the floor | `_wrap_close_start`: one right-to-left scan | `Bearer x` + `)`×n: 2 KB → 8 KB grows 15× (E 0.044 → 0.675 s) | linear, 64 KB ≈ 0.1 s | the anchored regex restored |
| B-1 | the same, escaped quotes after `Authorization:` | same | 0.030 → 0.436 s | linear | same |
| B-1b | unindented-break regex anchored at the end (floor C8 and additive rules) | `unindented_break`: one reverse search | `x Bearer` + `\n`×n + ` abc`: 0.016 → 0.204 s | linear | the regex restored in both modules |
| B-1c | predicates asked per span, rescanning a value shared by O(n) spans | decided once per part of a match (`_decision_key`) | `arn:` + `secret:`×n + `=abc123` (P): 0.021 → 0.207 s | linear | a key per span |
| B-2 | N10 fired on a key after a resource name, across `& , ; ( ) ? =` | segment of the name only, separator and value inside it | `grant_type=urn:…:token-exchange&client_secret=Hunter2abcX9` kept | redacted; 147 generated cases (4 names × 9 delimiters × 4 keys + 3 seat repros), E, P and POd | rev 11's N10 |
| N-1 | N3 summed a suffix across joiners | glued run only | `DATABASE_PASSWORD_FOR_REPLICATION_USER_ACCOUNT=Hunter2abcX9` kept | redacted | rev 11's N3 |
| N-2 | plain number unbounded, `0x` hex plain | ≤ 10 decimal digits | `Bearer ` + 24 digits, `Bearer 0x` + 64 hex, `password` + 24 digits, `auth_code=` + 24 digits kept | redacted; `code=401`, `exit code 137`, `{"code": -32601}` still kept | rev 11's number pattern |
| N-3 | a cut in `process_output` that creates `main`'s trailing `\b` | not changed | — | — | stated under Non-goals |
| N-4 | a trailing or doubled `&` neither kept nor labelled | every uncovered position labelled `url.normal` | `https://h/?a=1&` position 14 | covered | the labelling skipped |

**Generated timing sweep (rev 12; in rev 13 the work count and the
composition families replace its clock assertions).** `tests/_redaction_shapes.py` parses each of the
66 compiled patterns in the redactor's modules (`re` parser) and collects
every piece a quantifier can repeat: literal runs, representatives of each
character class, every alternative. Shapes are `lead + unit×k + tail` over
10 leads (nothing, `token`, `password=`, `Bearer x`, `x Bearer`,
`Authorization: x`, `arn:`, `https://h/?`, `code `, `x `) and 11 tails,
every two-character alternation of single-character units, and every key
word glued to a separator or joiner: 46 096 shapes. The slow tier screens
all of them at 4 KB → 16 KB on E, P, POs and POd in worker processes and
re-measures any over 8× at 16 KB → 64 KB, where growth over 1.6× the size
ratio fails. On rev 12, 40 shapes were flagged by the noisy screen and all
40 measured linear. The default tier holds 11 shapes (the seat's three,
the families the sweep found on rev 11, and the keyword-matcher shapes) at
16 KB → 64 KB with the same assertion and a 2 s cap.

## Rev-10 findings → rev-11 resolution

| rev-10 finding | resolution in rev 11 |
|---|---|
| B1 dict-leaf multi-pair leak | closed by construction: `main`'s keyword span is in the floor, and no predicate matches it |
| B2 `-bearer`, `X-Bearer`, `access_bearer` | closed by construction (`main`'s `\bbearer` spans; C11 still keeps a plain word after it) |
| B3 quadratic policy split | fixed separately (`_value_separator`, one pass) with a timing guard on `redact_secrets` directly |
| B4 Bearer/Authorization value stopped at a quote or bracket | closed by construction; the wrapping at the value's edges is kept by `wrapper_syntax`, a quote inside the value goes with it |
| B5 separators of 4–5 characters | closed by construction (`main`'s unbounded `[\s:=]+`) |
| N1 unparseable-port URL over 400 characters | closed by construction: `url.overflow` drops what `main`'s `ValueError` arm dropped |
| N2 (C6) glued key with a quote-opened value (`mysqlpwd='hunter2`) | closed by construction: C6 keeps only non-credential values, so `main`'s policy span stands |
| N3 (C10) bare `code` with a number or plain word | stays C10 (approved class); stated as a residual |
| N4 truncation window read from the diff only | unchanged from rev 10 (window invariant); the floor is computed on the window |
| Board seat's `accepted()` classifier | replaced: part B needs no hand classification |

## History (condensed)

| rev | what it did | what the next review found |
|---|---|---|
| 1 | keyword + shape rules | `[\s:=]+` still mangled prose; `\bbearer` ate the next word |
| 2–4 | payload bound, truncation order, span composition | passes ate each other's boundaries (a closing quote, an escaping backslash) → spans over the same text, applied once |
| 5–6 | composition property | a whole-URL rewrite span swallowed inner spans |
| 7 | differential against `main` as THE never-worse test | CRLF / no-break-space rows worse than `main` |
| 8 | wider continuation, bounded decoding | JSON literals, dotted/glued keys, Unicode whitespace, encoded keys: all outside the generator |
| 9 | generator widened along each finding | F1–F10: axes the generator never produced |
| 10 | generator derived from `main`'s grammar | B1–B5: the re-implementation's gaps (a wider value class, a narrowed Bearer gate, bounded separators) |
| 11 | `main`'s rules replayed as a floor; rev 10 only adds | floor held; three quadratic helpers, N10/N3/D2 wider than approved |
| 12 | each fixed as a class; generated timing sweep | floor held; two quadratic loops reached only by composed passes; flaky clock ratios |
| 13 | markers stand for what they replace; a work count, not the clock; shapes from the loops | floor held; B-3's class still open for re-encoded URL components (B-5) |
| **14** | **a step spells out input once; exact charges; memory linearity; shapes inside atomised pieces** | — |

The lesson of 7–10: a differential is only as wide as its generator, and a
re-implementation is only as good as its reviewer's imagination. Rev 11
removes the re-implementation from the never-worse argument altogether.

## Residuals

Rev 10's residual table stands for the **additive** rules (what they still do
not catch: bare uniform-hex secrets, single runs over 256 characters, a
digitless password after a whitespace keyword, JSON text inside a string
leaf — Consiliency/pmcp#290 — and the rest). None of them is worse than
`main`. The residuals **relative to `main`** are exactly the suppressions,
each approved:

- C10: bare `code` with a number or plain word (`code=123456789`): an OTP
  under a bare `code` key is kept, as every JSON-RPC and HTTP code is.
- C3a, C12: `code` after whitespace or under a diagnostic qualifier.
- D2's plain number (N-6 of rev 12's board): a value of up to 10 digits,
  signed or with leading zeros, is plain, so a PIN-shaped value after a
  bare keyword, a scheme, a weak or glued key is kept (`password
  4829163750`, `Bearer 4829163750`, `dbpassword=04829163`).
- D2's plain word (N-7): a word of letters and `_` of any length is plain,
  so `Bearer <28 random lower-case letters>` is kept (C11), as are such
  values after `password ` (C3) and a glued key (C6); the additive shape
  score does not catch an all-lower-case run.
- N4 (N-8 of rev 13's board): a camelCase multi-word key before a space
  separator (`dbPasswordForTheProductionReplicaServer Hunter2abcX9`) is
  kept through N4, which as approved does not test the value.
- N3, N4, N10: identifiers and resource names, including a credential-shaped
  value under a key with more than 24 characters glued to the key word (a
  cost bound) and a credential-shaped value that is itself a segment of an
  ARN or URN.
- The false positives rev 10 removed and rev 11 gives back to `main`
  (decision 4): `non-bearer 2024-01-01 report`, `secret --bucket`,
  `Bearer\n--flag12345`, `password:\n  * item` (P), a policy value across a
  JSON string boundary, `token_type=Bearer expires_in=3600`,
  `Bearer realm="api"`, `WWW-Authenticate: Bearer realm="x", …` and
  `scheme=Bearer scope=read`. They can come back only as separately reviewed
  classes.

## Changes

- `src/pmcp/redaction_floor.py` (new): the replay, the JSON adjustment, the
  suppression predicates.
- `src/pmcp/auth.py`: rev 10's additive engine; `collect_redaction_spans`
  = additive spans + the floor (diagnostic floor, or the policy floor when
  given `floor_patterns`); `merge_redaction_spans` split out of
  `apply_redaction_spans`; `sanitize_auth_diagnostic` applies the spans, then
  cuts. The URL rules strip trailing punctuation in one pass, as `main` does.
- `src/pmcp/keyword_matcher.py`: **unchanged from `main`**.
- `src/pmcp/policy/policy.py`: `DEFAULT_REDACTION_PATTERNS` = `main`'s;
  `_ADDITIVE_DEFAULT_PATTERNS` (rev 10's forms); `redaction_spans` = policy
  floor + additive spans; `_value_separator` (linear); rev 10's windowed
  `process_output` and `truncate_output(original_size=)`.
- `tests/_main_redactor.py` (new, the fidelity oracle),
  `tests/test_redaction_floor.py` (new), `tests/test_redaction.py`,
  `tests/_redaction_grammar.py`, `tests/fixtures/regen_redaction_main_oracle.py`,
  `tests/fixtures/redaction_main_oracle.b64` (= the sibling
  `.consiliency/plans/detailed-234-redactor-main-oracle.b64`, `cmp`-identical),
  `pyproject.toml` (the `slow` marker, excluded by `addopts`).

Documentation impact (implementer): CHANGELOG `### Security` / `### Fixed`
entries and the SECURITY.md redaction claim as in rev 10, plus one sentence
that redaction is never weaker than the previous release except by the named
suppressions.

## Verification

```bash
uv sync --all-extras -p 3.10
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
  uv run pytest -m 'not live and not slow' -q      # default tier (CI)
env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
  uv run pytest -m 'slow and not live' -q          # slow tier (not in CI)
```

**CI cost.** CI runs the default tier.

- The redaction tests there take about 9 minutes on dev0. The slowest are
  the tier-1 construction test (~45 s), the board-row construction test
  (~31 s) and the work-count test on the `key words after Bearer` shape
  (~18 s). No redaction test takes over 60 s.
- The whole default tier took 15:20, and 16:27 under load. The work-count
  and memory tests add about 5 minutes over rev 12.
- It has not been timed on a GitHub runner. With `--cov` on a 4-vCPU
  runner it may approach the `test` job's 25-minute limit; measure it
  there before merging.
- The slow tier is run by hand before a release and takes about 66
  minutes. Its longest case is the tier-2 matcher equivalence (~194 s),
  inside the 700 s per-test timeout.

**Measured on the embedded code (`b664588`):**

| check | result (as read from the logs) |
|---|---|
| `ruff check src/ tests/` | All checks passed! |
| `ruff format --check src/ tests/` | 172 files already formatted |
| `mypy src/` | Success: no issues found in 52 source files |
| default tier, run 1 | **4951 passed, 3 skipped, 122 deselected in 920.79s (0:15:20)** |
| default tier, run 2, under four CPU burners (killed afterwards) | **4951 passed, 3 skipped, 122 deselected in 987.00s (0:16:27)** |
| slow tier `-m 'slow and not live'` | **97 passed, 4979 deselected in 3989.37s (1:06:29)** |
| work-count screen of every generated shape (4 KB → 16 KB, all entry points) | 70 671 shapes, 0 over the bound |
| the same screen of the 476 atomised-piece shapes on rev 13 (`4b5cac8`) | 156 over the bound |

How these ran:

- The default tier ran as `-m 'not live and not slow'` and the slow tier
  as `-m 'slow and not live'`, both with `npm_config_cache`,
  `npm_config_store_dir` and `pnpm_config_store_dir` unset.
- The two 60 s tests in the default tier are
  `tests/test_progressive_disclosure.py`'s `test_invoke_query_docs` and
  `test_invoke_query_docs_conceptual`, which this plan does not touch.
- No `test_workflow_guards` error occurred.

**Embedding proof** (run for this plan):

- `origin/main` was re-fetched; it is `8de49e4`.
- The patch was extracted from this file with the commands under "Patch
  against `main`". It is `cmp`-equal to the generated diff.
- `git apply --check` and `git apply` succeeded on a fresh worktree of
  `8de49e4`.
- With the oracle copied from its sibling file, `pyproject.toml` is
  `cmp`-identical to the floor branch at `b664588`, and `diff -rq` of
  `src/` and `tests/` shows no differences.
- On that tree (`pmcp.__file__` printed from it):
  - `ruff check`: All checks passed!
  - `ruff format --check`: 172 files already formatted
  - `mypy src/`: no issues in 52 source files
  - the default tier of `test_redaction.py`, `test_redaction_floor.py`,
    `test_keyword_matcher.py`, `test_auth.py`, `test_policy.py`,
    `test_project_source_consent_policy.py` and
    `test_trust_boundaries_e2e.py`: **801 passed, 97 deselected in 413.81s
    (0:06:53)**.

## Acceptance criteria

1. The embedded patch applies to `main` @ `876fd33`; the result matches the
   floor branch file for file.
2. `ruff check`, `ruff format --check`, `mypy src/` clean.
3. Default tier green; slow tier green.
4. Fidelity: the replay reproduces `main`'s output on tier 1 (default) and
   tier 2 (slow), 0 URL fallbacks.
5. Construction A and B: 0 problems on tier 1, the board rows and the fuzz
   (default) and tier 2 (slow); every fired suppression is in
   `SUPPRESSIONS`, and `SUPPRESSIONS` is exactly the 18 named predicates.
6. Each predicate test fires on its false positive and keeps none of a
   credential-shaped neighbour.
7. B1–B5 and N11-after-scheme green, each mutant killed.
8. `tests/test_keyword_matcher.py` passes unmodified; `keyword_matcher.py`
   is `main`'s file.
9. The prose corpus survives both surfaces byte-identical; every surface
   keeps a JSON document JSON (the fuzz), and a dict result a dict wherever
   `main`'s was.
10. Linearity: the work count of every default-tier shape grows linearly
    (up to the n log n of sorting) from 16 KB to 64 KB to 256 KB on every
    entry point; every generated shape (slow tier) does too; no
    regex-derived shape grows more than 64x over 16x the input on the
    clock; peak traced memory of every default-tier shape grows linearly
    at 8 KB -> 32 KB -> 128 KB (default) and 16 KB -> 256 KB (slow).
11. Each rev-11 board finding is red on `3e49b95`, each rev-12 finding red
    on `b7b071d` and B-5 red on `4b5cac8`; each is green here and killed by
    its mutant.

## Non-goals

- Changing `main`'s rules, key sets or `redact_auth_url` (the floor freezes
  them; elicitation URLs stay openable).
- Any suppression not listed above; the false positives in decision 4 stay.
- Redacting a structured result as structure before it is serialised, and
  JSON text inside a string leaf (Consiliency/pmcp#290).
- Modelling `main`'s cut-then-redact order in `process_output`: the floor is
  computed on the window the redactor sees. The board's N-3 is this shape:
  a cut that happens to fall between a prefixed token and a glued non-ASCII
  letter creates a word boundary `main` then matched; rev 12 sees the
  un-cut window.
- Entropy scoring; MIME-wrapped base64 detection.

## Unverified

- Linear but slow: the additive keyword rules' bounded-qualifier regexes
  still cost about 9 s on 1 MB of a `token-` run (S-1; main about 0.1 s).
  The seat's 1 MB long-key shape now takes 2.3 s (13.7 s on rev 13).

- Timings are from one host (dev0), one run each.
- Rev 12's single unexplained default-tier failure was, on the seat's
  evidence, one of its clock-ratio tests under load (T-1); those tests are
  gone, and the three default-tier runs of rev 13 (one under load) were
  green.
- The slow tier is not run in CI; its results here are from one run.
- The floor is computed over the text the redactor sees; `main` truncated
  before redacting, so on output longer than the cap the two see different
  text (no corpus row is that long).
- SECURITY.md and CHANGELOG are the implementer's (not in the patch).

## Execution Policy

- execute: effort=medium, reason=the patch is complete and measured, but it
  is security-sensitive: re-run the fidelity oracle, the construction tests
  and the mutants on the implementer's tree rather than trusting this plan.



## Patch against `main` @ `876fd33`

This is `git diff --full-index 876fd33 b664588 -- pyproject.toml src tests`,
without `tests/fixtures/redaction_main_oracle.b64`. That fixture is
`cmp`-identical to the sibling
`.consiliency/plans/detailed-234-redactor-main-oracle.b64`, and is copied,
not patched.

`main` is now `8de49e4`. It differs from `876fd33` only by
`.consiliency/plans/detailed-296-gate-audit-20260926-2141.md`, so the
patch applies to it unchanged.

`sha256` of the result (first 16 hex digits):

| file | sha256 |
|---|---|
| `auth.py` | `4efb39a197d6b2fd` |
| `policy.py` | `ab02d11f71b6bb84` |
| `redaction_floor.py` | `41f58d21ce48c66e` |
| `keyword_matcher.py` | `4c256b3d813bed15` (= `main`'s) |

To apply, on a fresh worktree of `main`:

```bash
sed -n '/^<!-- PATCH-BEGIN -->$/,/^<!-- PATCH-END -->$/p' <this plan> \
  | sed '1,2d;$d' | sed '$d' > floor.patch
git apply --check floor.patch && git apply floor.patch
cp .consiliency/plans/detailed-234-redactor-main-oracle.b64 \
  tests/fixtures/redaction_main_oracle.b64
```

<!-- PATCH-BEGIN -->
```diff
diff --git a/pyproject.toml b/pyproject.toml
index 500a26dd9217b1dc365bfb098699316d289498f6..9b3dc88ce45a49c571b43b2165b498fdf7426fbb 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -146,7 +146,7 @@ artifacts = ["src/pmcp/manifest/_npm_resolve.js"]
 [tool.pytest.ini_options]
 asyncio_mode = "auto"
 testpaths = ["tests"]
-addopts = "-m 'not live'"
+addopts = "-m 'not live and not slow'"
 # Consiliency/pmcp#200 -- diagnostics for the intermittent runtime hang. Five
 # `test (3.x)` jobs in the week to 2026-09-01 stalled and were killed by the
 # job's `timeout-minutes: 25`; GitHub reports a timed-out job as *cancelled*,
@@ -186,6 +186,7 @@ timeout = 700
 timeout_method = "signal"
 markers = [
     "live: opt-in live integration tests (require network and package managers)",
+    "slow: opt-in long-running tests (the full grammar-derived redaction differential; run with -m slow)",
     "timeout(seconds): expected maximum runtime for slow opt-in tests",
     "real_cwd: run from the invocation directory, opting out of the autouse isolate_cwd fixture (only for tests whose subject IS the working directory)",
 ]
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index 0e58d07734b410ce37d65f463f97437f186647c9..8ec77558e57e4bdf8d482b7e73af1a5e5c97eacc 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -2,8 +2,10 @@
 
 from __future__ import annotations
 
+import bisect
 import json
 import re
+from collections.abc import Callable, Iterator, Sequence
 import time
 import asyncio
 from collections.abc import Mapping
@@ -12,14 +14,14 @@ from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
 from itertools import product
 from typing import Any, Literal
 from urllib.error import HTTPError
-from urllib.parse import parse_qsl, quote, urlparse, urlunparse
+from urllib.parse import parse_qsl, quote, urlparse, urlunparse, unquote
 from urllib.request import HTTPRedirectHandler, Request, build_opener
 
 import aiohttp
 import jwt
 from jwt import PyJWKSet
 
-from pmcp.keyword_matcher import key_start_pattern, redact_keyword_values
+from pmcp.redaction_floor import floor_spans, unindented_break, work
 from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo
 
 
@@ -71,15 +73,24 @@ AUTH_DIAGNOSTIC_SECRET_KEYS = {
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
@@ -88,8 +99,15 @@ AUTH_DIAGNOSTIC_SECRET_KEYS = {
     "token",
 }
 
-#: Where a secret key starts, for the keyword rule in `sanitize_auth_diagnostic`.
-_KEYWORD_KEY_START = key_start_pattern(AUTH_DIAGNOSTIC_SECRET_KEYS)
+#: Keys that name a credential only sometimes. A plain word or number after
+#: them is kept: `credentials: include` (a fetch mode), `auth=basic`,
+#: `auth: none`, `{"code": -32601}` (every JSON-RPC error), `{"code":
+#: "not_found"}`. Anything else -- a token, `user:pass`, a path -- is
+#: redacted. Every other key in `AUTH_DIAGNOSTIC_SECRET_KEYS` is strong: its
+#: value is redacted whatever it looks like. `tests/test_redaction.py`
+#: derives its key-coverage property from both sets, so a key named here but
+#: not redacted in every form fails that test.
+WEAK_SECRET_KEYS = frozenset({"auth", "code", "credential", "credentials"})
 
 _JWT_RE = re.compile(
     r"(?<![A-Za-z0-9_-])"
@@ -97,6 +115,1011 @@ _JWT_RE = re.compile(
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
+    # The body never scans past the next BEGIN: `.*?` alone made every
+    # unterminated BEGIN scan to the end of the text, which is quadratic in
+    # the number of BEGINs (rev 9: 5.6 s on 264 KB of them; F9 of rev 9's
+    # board).
+    re.compile(
+        r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:(?!-----BEGIN ).)*?"
+        r"-----END [A-Z ]*PRIVATE KEY-----",
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
+def _counted(matches: Iterator[re.Match[str]]) -> Iterator[re.Match[str]]:
+    """Each match of a pass, counted as the work of reading it (the
+    test-only work counter, `pmcp.redaction_floor.work`)."""
+    for match in matches:
+        work(match.end() - match.start() + 1)
+        yield match
+
+
+def _run_spans(start: int, run: str) -> list[Span]:
+    work(len(run))
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
+    work(len(text) * (3 + len(_VENDOR_SHAPE_RES)))  # one scan per pattern
+    spans: list[Span] = [
+        (m.start(), m.end(), REDACTED) for m in _counted(_JWT_RE.finditer(text))
+    ]
+    for vendor_re in _VENDOR_SHAPE_RES:
+        spans.extend((m.start(), m.end(), REDACTED) for m in vendor_re.finditer(text))
+    spans.extend(
+        (m.start(), m.end(), REDACTED) for m in _PREFIXED_TOKEN_RE.finditer(text)
+    )
+    for m in _counted(_OPAQUE_RUN_RE.finditer(text)):
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
+#: `code` names a credential in the OAuth sense -- bare (`code=`, the
+#: callback parameter) or under one of these qualifiers (`auth_code=`,
+#: `device_code=`) -- and is then a weak key: a plain word or number is kept
+#: (`{"code": -32601}` is every JSON-RPC error and `{"code": "not_found"}`
+#: every REST one), anything else is redacted.
+_CODE_QUALIFIERS = frozenset({"", "auth", "authorization", "oauth", "device", "user"})
+#: Under a status or descriptive qualifier (`status_code=401`,
+#: `error_code=invalid_grant`, `exit_code=137`, `zip_code=94105`,
+#: `sqlstate_code=42P01`) `code` names a status, and its value is left to the
+#: shape rules, which still catch an opaque one. Under any OTHER qualifier
+#: (`otp_code=`, `mfa_code=`, `verification_code=`, `recovery_code=`) it is
+#: a weak key: a credential-shaped value is redacted, as main redacted it.
+#: An unknown qualifier therefore fails closed. Matched against the
+#: qualifier's last `_`/`-` segment. Nothing here names a credential or a
+#: redeemable value: `key_code`, `promo_code`, `coupon_code` and
+#: `discount_code` are weak keys (tests/_redaction_grammar.py states this set
+#: as an accepted class and a test pins the two equal).
+_STATUS_CODE_QUALIFIERS = frozenset(
+    {
+        "status",
+        "error",
+        "exit",
+        "http",
+        "response",
+        "return",
+        "result",
+        "reason",
+        "zip",
+        "postal",
+        "country",
+        "lang",
+        "language",
+        "currency",
+        "iso",
+        "area",
+        "region",
+        "locale",
+        "event",
+        "op",
+        "opcode",
+        "char",
+        "byte",
+        "source",
+        "color",
+        "colour",
+        "product",
+        "item",
+        "sku",
+        "sqlstate",
+        "state",
+        "exception",
+        "fault",
+        "ret",
+        "rc",
+        "err",
+        "errno",
+    }
+)
+
+
+def _last_segment(qualifier: str) -> str:
+    return re.split(r"[_-]", qualifier.rstrip("_-").lower())[-1]
+
+
+#: Where a key may start in addition to after a non-identifier character:
+#: right after a JSON escape (`\\n`, `\\t`, `\\b`, `\\f`, `\\"`, `\\\\`, `\\/`,
+#: `\\u00a0`). `process_output` serialises a dict leaf with
+#: `json.dumps(ensure_ascii=True)`, so every control character, every
+#: non-ASCII character and 25 of the 29 `str.isspace()` characters reach the
+#: redactor as an escape whose last character is alphanumeric
+#: (`\\u00a0password=`), which would otherwise read as a glued prefix. Main's
+#: `\\b[A-Za-z0-9_-]*` swallowed the escape's tail and redacted (F3 of rev 9's
+#: board).
+_JSON_ESCAPE_BOUNDARY = r"(?<=\\[nrtbf/\"\\])|(?<=\\u[0-9a-fA-F]{4})"
+#: ... and what a qualifier or a glued prefix may NOT start on: the tail of
+#: such an escape (the `u00a0` of `\\u00a0password`, the `n` of
+#: `\\npassword`), which would otherwise be read as part of the key. The key
+#: NAME may start there (`\\token=x` is `token` in raw text), and anything
+#: else after a backslash may be a qualifier or glued prefix
+#: (`DOMAIN\\password=`, `C:\\secret=`, `\\dbpassword=`), as `\\b` made it on
+#: main (F8 of rev 9's board).
+_NOT_ON_AN_ESCAPE_TAIL = r"(?!(?<=\\)(?:[nrtbf]|u[0-9a-fA-F]{4}))"
+#: A whitespace character as `json.dumps` spells it: `\t \n \r \f`, or a
+#: `\uXXXX` escape of one of the other `str.isspace()` characters. Between
+#: `Bearer`/`Authorization:` and a value in a serialised leaf, main's
+#: `\s+[^\s,;]+` took such an escape as part of the value and redacted it
+#: with the token (`Bearer \u2006(tok)`); here it is part of the separator.
+_JSON_SPACE_ESCAPE = (
+    r"\\(?:[tnrf]|u(?:000[bB]|001[c-fC-F]|0085|00[aA]0|1680|200[0-9aA]"
+    r"|202[89fF]|205[fF]|3000))"
+)
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
+#: ours to guess at -- except an UNTERMINATED opening quote followed by a
+#: bare run to whitespace, a list separator or the end (`password="hunter2`,
+#: `api_key='abc`): main's policy defaults took the run after the quote, so
+#: this does too (N5). Bare whitespace is NOT a separator here (see
+#: `_KEYWORD_WS_RE`). A key starts at a non-identifier character, at a single
+#: `:` (`Database:Password=`, .NET configuration; not `::`, a path, and not
+#: inside an `arn:`/`urn:` resource name, see `_RESOURCE_NAME_RE`), after a
+#: backslash (`DOMAIN\\password=`), or right after a JSON escape
+#: (`_JSON_ESCAPE_BOUNDARY`) -- `main`'s containing match covered all of
+#: these, and the bar is never worse than `main`. The separator is `:` or
+#: `=`, Ruby's `=>`, httpie's `:=`, a run of up to four `:`/`=` (`===`,
+#: `=:`), or `==` when nothing but the value follows it (`password==hunter2`
+#: is httpie's query syntax); a separator holding `==` or `::` (`if token ==
+#: expected`, `token::Type`) is a comparison or a path unless the value is
+#: credential-shaped. The value
+#: may sit on the NEXT line when that line is indented (YAML block style,
+#: pretty-printed JSON) or starts with a quote, or -- unindented -- when the
+#: value is credential-shaped (`password:\r\nhunter2`, as main redacted it);
+#: `token:\nthe bearer of` and `token:\n  - a bullet` are prose. The next
+#: line never opens on a `--` flag or a lone `-`/`*`/`#`/`>` marker followed
+#: by whitespace; a marker glued to the value is part of it
+#: (`password:\n  -hunter22`, as main redacted it). Horizontal whitespace is
+#: every `str.isspace()` character but `\r`/`\n`; a line break is any run of
+#: `\r`/`\n` with whitespace between (blank lines, a bare CR, HTTP header
+#: folding, Windows dumps), before or after the operator -- rev 9 took
+#: exactly `\r?\n` after it and regressed against main's `[\s:=]+` (F4 of
+#: rev 9's board). A bare value ends at whitespace, a quote or a list
+#: separator (`,`, `;`, or `&` -- a query string's) and at nothing else,
+#: except that it never STARTS on `[` or `{` (`"password": [\n  "x"\n]` is a
+#: list -- `_keyword_list_spans` redacts its quoted elements instead; the
+#: one `[` allowed is the marker's own, so `token=[REDACTED]abc123def456`
+#: is still one value) and
+#: never ENDS on a closing bracket or a backslash (the `\\`
+#: before a quote in a JSON-serialised string escapes that quote; eating it
+#: breaks the document): `password=(Xk9mQ2vL)` is one value, punctuation and all,
+#: while the `}` of `{"password": [REDACTED]}` stays with the object.
+#: The opening quote of what looks like a quoted value, when the content
+#: then starts with a JSON structural character: `…token: ", "next": …` is a
+#: key at the END of a JSON string, and this quote closes that string. Only
+#: consulted after an UNQUOTED key -- after `"password": "` the quote always
+#: opens a value, whatever it holds (`", secret"` is a valid password).
+_STRADDLE_RE = re.compile(r"[^\S\r\n]*[,:\]}]")
+
+#: Every bare scalar `json.dumps` emits (`allow_nan=True` is its default):
+#: after a quoted key it is a JSON literal, and it is left alone, as main left
+#: it -- redacting it would make the document invalid (`NaN`, `Infinity`) or
+#: change the leaf's type (`{"max_tokens": 1024}`). Numeric secrets under a
+#: quoted key are Consiliency/pmcp#290's scope.
+_JSON_SCALAR_RE = re.compile(
+    r"null|true|false|NaN|-?Infinity"
+    r"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
+)
+
+#: A line break inside a separator: any run of `\r`/`\n`, with any whitespace
+#: around it, that ends before a visible character.
+_BREAK = r"[^\S\r\n]*[\r\n]\s*"
+
+_KEYWORD_KEY_SEP = (
+    r"(?P<key>(?P<qualifier>(?:(?<![A-Za-z0-9])(?<!::)|"
+    + _JSON_ESCAPE_BOUNDARY
+    + r")(?:"
+    + _NOT_ON_AN_ESCAPE_TAIL
+    + r"(?:[A-Za-z0-9]+[_-]){1,8})?"
+    r"(?:(?-i:[A-Za-z][a-z]*(?=[A-Z])))?)"
+    r"(?P<glued>(?:" + _NOT_ON_AN_ESCAPE_TAIL + r"[A-Za-z0-9]{1,24}?)?)"
+    rf"(?P<name>{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?"
+    r"(?P<extra>(?:[_-]*[A-Za-z0-9]){0,24}[_-]*))"
+    r"(?P<sep>[\"']?\s*(?:=>|:=(?![:=])|[:=]{2,4}(?![:=])|[:=](?!=))"
+    r"(?:" + _BREAK + r"(?=\S)(?!--|[-*#>](?:\s|$))|[^\S\r\n]*))"
+)
+#: The run a bare value is made of.
+_BARE_RUN = r"[^\s\"',;&]*[^\s\"',;&)\]}\\]"
+_KEYWORD_SEP_RE = re.compile(
+    _KEYWORD_KEY_SEP
+    + r"(?P<value>\"(?:[^\"\\\n]|\\.)*\"(?![A-Za-z0-9_])|'(?:[^'\\\n]|\\.)*'(?![A-Za-z0-9_])"
+    # an unterminated opening quote, then a bare run to whitespace, a list
+    # separator, the end, or a double quote (the end of the JSON string a
+    # single-quoted value sits in)
+    r"|[\"'](?=" + _BARE_RUN + r"(?:[\s,;&\"]|$))" + _BARE_RUN + r"(?!')"
+    r"|(?!\{)(?!\[(?!REDACTED\]))" + _BARE_RUN + r")",
+    re.IGNORECASE,
+)
+#: An AWS ARN or a URN: a key inside one names a resource
+#: (`arn:aws:secretsmanager:…:secret:Name`,
+#: `urn:ietf:params:oauth:token-type:access_token`), it is not one.
+_RESOURCE_NAME_RE = re.compile(r"\b[au]rn:[^\s\"'<>]*", re.IGNORECASE)
+
+#: The same keyword and separator followed by a flat list (`"password":
+#: ["hunter2"]`, pretty-printed or not): each quoted element is a value of the
+#: key. No nested brackets -- a list of objects carries its own keys.
+#: A key that is itself declared (`secret_access_key` is one key, not
+#: `secret` + a suffix): the suffixed-key gate does not apply to it.
+_DECLARED_KEY_RE = re.compile(
+    rf"(?:{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?", re.IGNORECASE
+)
+#: A literal array: quoted strings or JSON scalars separated by commas --
+#: nothing bare. `[x", "b": "]` after `token: ` inside a string leaf is not an
+#: array (the scan would run past the string's closing quote), while
+#: `["first]", "hunter2"]` is one (a `]` inside a quoted element is text).
+_LIST_BODY = r"(?P<list>\[\s*(?:(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|-?[0-9][0-9.eE+-]*|null|true|false)\s*,\s*)*(?:(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|-?[0-9][0-9.eE+-]*|null|true|false))?\s*,?\s*\])"
+_KEYWORD_LIST_RE = re.compile(
+    _KEYWORD_KEY_SEP + _LIST_BODY,
+    re.IGNORECASE,
+)
+_QUOTED_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
+
+#: A bare keyword (or `--keyword` flag) followed by whitespace and a value that
+#: could be a credential (`_value_could_be_a_credential`). This is what keeps
+#: `--token abc123def456` redacted without redacting the second word of
+#: `token bucket`, `secret ingredient` or `session expired`. A `param=value`
+#: after the keyword (`token expires_in=3600`) is not its value either. `code`
+#: never fires here: `exit code 137`, `status code 401`, `zip code 94105`.
+#: The whitespace may include a newline into an indented line (a folded
+#: header: `x-api-key\n  abc123def456`), gated by the same value test. A
+#: value is never a flag or a bullet: not `--…` (`secret --bucket` is a flag
+#: after a word) and not a lone `-`, `*`, `#` or `>` followed by whitespace or
+#: the end (`token:\n  - item` is a bullet). A single marker glued to the
+#: value is part of it: `token -abc123def`, `password\n  -hunter22` (a
+#: base64url secret can start with `-`) are redacted, as on main. The flag
+#: may be `--`, a single `-`, `_` or any run of them (`java -jar x.jar
+#: -password hunter22x`; N9 of the rev-10 audit), and the qualifier may have
+#: any number of joined segments, as main's `[A-Za-z0-9_-]*` did: the match
+#: can only start where an identifier starts (the lookbehind), so an
+#: unbounded qualifier stays linear here, unlike the keyed rule's, which may
+#: restart after every joiner and is bounded instead.
+_KEYWORD_WS_RE = re.compile(
+    r"(?P<key>(?:(?<![A-Za-z0-9_-])|"
+    + _JSON_ESCAPE_BOUNDARY
+    + r")[_-]*(?:"
+    + _NOT_ON_AN_ESCAPE_TAIL
+    + r"(?:[A-Za-z0-9]+[_-]+)+)?"
+    r"(?:(?-i:[A-Za-z][a-z]*(?=[A-Z])))?"
+    r"(?P<glued>(?:" + _NOT_ON_AN_ESCAPE_TAIL + r"[A-Za-z0-9]{1,24}?)?)"
+    rf"(?P<name>{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?"
+    r"(?:[_-]*[A-Za-z0-9]){0,24}[_-]*)"
+    r"(?P<sep>[^\S\r\n]+|" + _BREAK + r")"
+    r"(?![A-Za-z_-]+=[^=])(?!--|[-*#>](?:\s|$))(?P<value>[^\s\"',;()\[\]{}]*[^\s\"',;()\[\]{}\\])",
+    re.IGNORECASE,
+)
+
+#: `Bearer <token>` -- the HTTP scheme, so anything after it that is not a word
+#: is a token, wherever it stands: `X-Auth: Bearer x`, `session=Bearer x`
+#: (rev 9 excluded a preceding `:`/`=` and leaked those; F2 of its board).
+#: Bearer as a VALUE is already excluded by the rest: `token_type=Bearer
+#: expires_in=3600` (the lookahead: a `param=value` is not a token),
+#: `token_type: Bearer` and `{"token_type": "Bearer"}` (nothing follows but
+#: a quote, a comma or the end). Not `Bearer realm="x"` (a challenge's own
+#: parameters, the lookahead), not `Missing bearer token` or `the bearer of
+#: bad news` (plain words, the callback). `(?<![A-Za-z0-9_-])` rather than
+#: `\b`: on main `\bbearer` fired inside `secret-bearer failed` and
+#: redacted `failed`; a JSON escape before it is a boundary too
+#: (`\u00a0Bearer x` in a serialised leaf). A token may be wrapped in a quote
+#: or bracket that closes right after it (`Bearer "x"`, `Bearer (x)`, N7 of
+#: the rev-10 audit): the inside is redacted and the wrapping kept. Otherwise
+#: the value stops at a quote or bracket: `{"password": "hunter2 Bearer x"}`
+#: must keep its closing quote for the keyword pass, not lose it to this one.
+#: It never ends on a backslash either: in a serialised leaf the token reads
+#: `Bearer hunter2tok\"`, and eating the `\` un-escapes the quote.
+_BEARER_RE = re.compile(
+    r"(?:(?<![A-Za-z0-9_-])|" + _JSON_ESCAPE_BOUNDARY + r")"
+    r"(?P<key>bearer(?:(?:[^\S\r\n]|" + _JSON_SPACE_ESCAPE + r")+|" + _BREAK + r"))"
+    r"(?![A-Za-z_-]+=[^=])"
+    r"(?:[\"'(\[{<](?=[^\s,;\"'()\[\]{}<>\\]+[\"')\]}>]))?"
+    r"(?P<value>[^\s,;\"'()\[\]{}<>]*[^\s,;\"'()\[\]{}<>\\])",
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
+#: prose (`Set the Authorization:\nheader first` is a sentence). A value
+#: wrapped in a bracket or quote after the scheme (`Authorization: [x]`,
+#: `Authorization: Bearer "x"`, N6 of the rev-10 audit) has its inside
+#: redacted -- except after a quoted key, where `"Authorization": [1.5]` is
+#: JSON structure. The separator may hold line breaks on either side of the
+#: operator, as main's `\s*[:=]\s*` did.
+_AUTHORIZATION_RE = re.compile(
+    r"authorization[\"']?(?:\s|" + _JSON_SPACE_ESCAPE + r")*[:=]"
+    r"(?:" + _BREAK + r"(?=\S)(?!--|[-*#>](?:\s|$))"
+    r"|(?:[^\S\r\n]|" + _JSON_SPACE_ESCAPE + r")*)"
+    r"(?:(?P<quoted>\"(?:[^\"\\\n]|\\.)*\"(?![A-Za-z0-9_])|'(?:[^'\\\n]|\\.)*'(?![A-Za-z0-9_]))"
+    r"|(?:(?:bearer|basic|digest|negotiate|ntlm|token)"
+    r"(?:[^\S\r\n]|" + _JSON_SPACE_ESCAPE + r")+)?"
+    r"[\"'(\[{<](?P<inner>[^\s,;\"'()\[\]{}<>\\]+)[\"')\]}>]"
+    r"|(?P<bare>(?![\[{])(?:(?:bearer|basic|digest|negotiate|ntlm|token)[^\S\r\n]+)?"
+    r"[^\s,;\"']*[^\s,;\"')\]}\\]))",
+    re.IGNORECASE,
+)
+
+#: A URL in free text; trailing sentence punctuation is handed back.
+_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
+
+
+#: A separator whose (last) line break is followed by no indentation:
+#: `pmcp.redaction_floor.unindented_break`, one reverse search (a regex
+#: anchored at the end restarts at every break).
+
+
+def _in_resource_name(text: str) -> Callable[[int], bool]:
+    """Is a position inside an `arn:`/`urn:` resource name? The names are
+    disjoint and ascending (one `finditer`), so each question is one binary
+    search: asking every range for every keyword match was quadratic (B-4 of
+    rev 12's board)."""
+    ranges = [(m.start(), m.end()) for m in _RESOURCE_NAME_RE.finditer(text)]
+    starts = [a for a, _ in ranges]
+    work(len(text))
+
+    def inside(position: int) -> bool:
+        work(1)
+        i = bisect.bisect_right(starts, position) - 1
+        return i >= 0 and position < ranges[i][1]
+
+    return inside
+
+
+def _keyword_sep_spans(text: str) -> list[Span]:
+    work(len(text))  # the pass's scan
+    spans: list[Span] = []
+    in_resource_name = _in_resource_name(text)
+    for match in _counted(_KEYWORD_SEP_RE.finditer(text)):
+        if in_resource_name(match.start()):
+            continue  # `arn:…:secret:Name` names a secret, it is not one
+        name = match.group("name").lower()
+        if name in WEAK_SECRET_KEYS and _is_plain_word_or_number(match.group("value")):
+            continue
+        if unindented_break(match.group("sep")) and not (
+            match.group("value")[0] in "\"'"
+            or _value_could_be_a_credential(match.group("value"))
+        ):
+            continue  # `token:\nthe bearer of`: a sentence, not a value
+        if name == "code":
+            qualifier = (
+                (match.group("qualifier") + match.group("glued")).rstrip("_-").lower()
+            )
+            if qualifier not in _CODE_QUALIFIERS and (
+                _last_segment(qualifier) in _STATUS_CODE_QUALIFIERS
+                or not _value_could_be_a_credential(match.group("value"))
+            ):
+                continue  # `status_code=401`; `otp_code=abc123def456` is redacted
+        start, end = match.start("value"), match.end("value")
+        value = match.group("value")
+        sep = match.group("sep")
+        if ("==" in sep or "::" in sep) and not _value_could_be_a_credential(
+            value.strip("\"'")
+        ):
+            # `if token == expected:` compares, `token::Type` is a path;
+            # `password == hunter2` assigns
+            continue
+        if match.group("glued") or (
+            match.group("extra")
+            and not _DECLARED_KEY_RE.fullmatch(
+                text, match.start("name"), match.end("extra")
+            )
+        ):
+            # `CLIENTSECRET=`, `dbpassword=` (a prefix glued on with no case
+            # or separator boundary) and suffixed keys: a secret only when
+            # the value looks like one
+            # `password_confirmation=`, `passwordHash=`, `secret_value=`: a
+            # suffixed key is only a secret when its value looks like one
+            # (`token_type=bearer`, `password_length=12` are not)
+            inner = value[1:-1] if value[0] in "\"'" else value
+            if (
+                "://" in inner
+                or inner.lower().startswith("arn:")
+                or not _value_could_be_a_credential(inner)
+            ):
+                continue  # `token_endpoint=https://…`, `secret_arn=arn:…` name things
+        if (
+            match.group("sep")[:1] in "\"'"
+            and value[0] not in "\"'"
+            and _JSON_SCALAR_RE.fullmatch(value)
+        ):
+            # A quoted key -- JSON (or a Python/JS literal): a bare scalar is
+            # a JSON literal, left as main left it (`_JSON_SCALAR_RE`)
+            continue
+        if (
+            value[0] in "\"'"
+            and match.group("sep")[:1] not in "\"'"
+            and (
+                _STRADDLE_RE.match(value, 1)
+                # the OTHER quote, unescaped, inside: the value runs across a
+                # JSON string boundary (`…password='x"], "k": "y'`)
+                or ('"' if value[0] == "'" else "'") in value[1:-1].replace("\\\\", "")
+            )
+        ):
+            continue  # the quote closes the string this key sits in
+        if value[0] in "\"'" and (len(value) == 1 or value[-1] != value[0]):
+            # An unterminated opening quote: the run after it. Ended by a
+            # double quote, it may be the end of the JSON string a
+            # single-quoted value sits in (`{"a": "password='x", …}`): only
+            # a credential-shaped run is a value there.
+            if text[end : end + 1] == '"' and not _value_could_be_a_credential(
+                value[1:]
+            ):
+                continue
+            spans.append((start + 1, end, REDACTED))
+            continue
+        if value[0] in "\"'":
+            # Redact INSIDE the quotes: `{"password": "[REDACTED]"}` is still
+            # JSON, so a structured result round-trips as a dict (main's did).
+            start, end = start + 1, end - 1
+        spans.append((start, end, REDACTED))
+    return spans
+
+
+def _keyword_list_spans(text: str) -> list[Span]:
+    work(len(text))  # the pass's scan
+    spans: list[Span] = []
+    in_resource_name = _in_resource_name(text)
+    for match in _counted(_KEYWORD_LIST_RE.finditer(text)):
+        if in_resource_name(match.start()):
+            continue
+        name = match.group("name").lower()
+        if (
+            name == "code"
+            and _last_segment(match.group("qualifier")) in _STATUS_CODE_QUALIFIERS
+        ):
+            continue  # `error_codes: [...]` are diagnostics, as in the scalar pass
+        base = match.start("list")
+        for element in _QUOTED_RE.finditer(match.group("list")):
+            inner = element.group()[1:-1]
+            # An empty element needs no case: a zero-length span is a no-op.
+            if name in WEAK_SECRET_KEYS and _is_plain_word_or_number(inner):
+                continue
+            spans.append(
+                (base + element.start() + 1, base + element.end() - 1, REDACTED)
+            )
+    return spans
+
+
+def _is_single_case(word: str) -> bool:
+    """`CLIENTSECRET`, `clientsecret`, `Clientsecret` -- one word, case-folded.
+    Not `Ed25519PrivateKey` (`str.istitle` would accept that)."""
+    return (
+        word.isupper() or word.islower() or (word[:1].isupper() and word[1:].islower())
+    )
+
+
+#: An acronym glued to a Titlecase word: `PGPassword`, `DBPassword`,
+#: `APIToken` (N4b of the rev-10 audit). Not `Ed25519PrivateKey`.
+_ACRONYM_TITLE_RE = re.compile(r"[A-Z0-9]{1,8}[A-Z][a-z]+")
+
+
+def _keyword_ws_spans(text: str) -> list[Span]:
+    work(len(text))  # the pass's scan
+    return [
+        (match.start("value"), match.end("value"), REDACTED)
+        for match in _counted(_KEYWORD_WS_RE.finditer(text))
+        if match.group("name").lower() != "code"
+        and _value_could_be_a_credential(match.group("value"))
+        # a glued prefix (`CLIENTSECRET abc…`) only on a single-case key or
+        # an acronym + Titlecase one (`PGPassword abc…`): any other
+        # mixed-case identifier (`Ed25519PrivateKey X509Cert`) is prose
+        and (
+            not match.group("glued")
+            or _is_single_case(match.group("key").lstrip("-_"))
+            or _ACRONYM_TITLE_RE.fullmatch(match.group("key").lstrip("-_")) is not None
+        )
+        and "://" not in match.group("value")
+        and not match.group("value").lower().startswith("arn:")
+    ]
+
+
+def _bearer_spans(text: str) -> list[Span]:
+    work(len(text))  # the pass's scan
+    return [
+        (match.start("value"), match.end("value"), REDACTED)
+        for match in _counted(_BEARER_RE.finditer(text))
+        if not _is_plain_word_or_number(match.group("value"))
+        and not (
+            unindented_break(match.group("key"))
+            and (
+                # a flag or a lone bullet on the next line (the value holds no
+                # whitespace, so a one-character marker is the bullet case)
+                match.group("value").startswith("--")
+                or (
+                    match.group("value")[0] in "-*#>" and len(match.group("value")) == 1
+                )
+                or not _value_could_be_a_credential(match.group("value"))
+            )
+        )
+    ]
+
+
+def _authorization_spans(text: str) -> list[Span]:
+    work(len(text))  # the pass's scan
+    spans: list[Span] = []
+    for match in _counted(_AUTHORIZATION_RE.finditer(text)):
+        key_end = match.start() + len("authorization")
+        quoted_key = text[key_end : key_end + 1] in ('"', "'")
+        quoted = match.group("quoted")
+        if quoted is not None:
+            if not quoted_key and _STRADDLE_RE.match(quoted, 1):
+                continue  # the quote closes the string this key sits in
+            spans.append((match.start("quoted") + 1, match.end("quoted") - 1, REDACTED))
+            continue
+        if match.group("inner") is not None:
+            if not quoted_key:  # `"Authorization": [1.5]` is JSON structure
+                spans.append((match.start("inner"), match.end("inner"), REDACTED))
+            continue
+        bare = match.group("bare")
+        if quoted_key and _JSON_SCALAR_RE.fullmatch(bare):
+            continue  # a JSON literal after a quoted key: same rule as above
+        if not _is_plain_word_or_number(bare):
+            spans.append((match.start("bare"), match.end("bare"), REDACTED))
+    return spans
+
+
+#: Percent-decoding a query value and asking the engine about it can meet
+#: another encoded URL inside; three levels is more than any real URL nests
+#: (a redirect inside a redirect), and past that the value is treated as
+#: covered -- redacted -- rather than decoded again. Downstream-controlled
+#: text must not be able to recurse the diagnostic path to death.
+_MAX_DECODE_DEPTH = 3
+
+Covers = Callable[[str], bool]
+
+
+def _url_component_spans(
+    base: int, raw_url: str, depth: int, covers: Covers | None
+) -> list[Span]:
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
+    work(len(raw_url))
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
+            value_start = position + len(key) + 1
+            if equals and value and unquote(key).lower() in AUTH_SECRET_QUERY_KEYS:
+                spans.append(
+                    (base + value_start, base + value_start + len(value), REDACTED)
+                )
+            elif (
+                equals
+                and value
+                and "%" in key
+                and _covers_anything(f"{unquote(key)}={unquote(value)}", depth, covers)
+            ):
+                # `?api%2Dkey=hunter2`: the encoded key hides it from the
+                # keyword passes, which read the raw text
+                spans.append(
+                    (base + value_start, base + value_start + len(value), REDACTED)
+                )
+            elif (
+                equals
+                and "%" in value
+                and _covers_anything(unquote(value), depth, covers)
+            ):
+                # A percent-encoded value hides its shape from every other
+                # pass (`%67%68%70%5F…` is `ghp_…`): decode it, ask the whole
+                # engine, and redact the encoded value whole if anything in
+                # the decoded text would be.
+                spans.append(
+                    (base + value_start, base + value_start + len(value), REDACTED)
+                )
+            position += len(pair) + 1
+    return spans
+
+
+def _covers_anything(text: str, depth: int, covers: Covers | None) -> bool:
+    """Would the engine (or the policy surface's patterns, via ``covers``)
+    redact anything in the decoded ``text``? Past the decode depth: yes."""
+    if depth >= _MAX_DECODE_DEPTH:
+        return True
+    if any(
+        replacement in (REDACTED, "")
+        for _, _, replacement in collect_redaction_spans(
+            text, _depth=depth + 1, covers=covers
+        )
+    ):
+        return True
+    return covers is not None and covers(text)
+
+
+def _url_spans(text: str, depth: int, covers: Covers | None) -> list[Span]:
+    spans: list[Span] = []
+    for match in _counted(_URL_RE.finditer(text)):
+        raw_url = match.group(0)
+        # Trailing sentence punctuation is handed back, and so is a trailing
+        # backslash: in a serialised leaf it escapes the closing quote
+        # (`…?sid=x\\"`), and a query-value span that ate it broke the JSON.
+        raw_url = raw_url.rstrip(").,;\\")  # in one pass, as main does
+        spans.extend(_url_component_spans(match.start(), raw_url, depth, covers))
+    return spans
+
+
+def collect_redaction_spans(
+    text: str,
+    *,
+    _depth: int = 0,
+    covers: Covers | None = None,
+    floor_patterns: Sequence[re.Pattern[str]] | None = None,
+) -> list[Span]:
+    """Every redaction the engine would make to ``text``, as spans over it:
+    main's floor (`pmcp.redaction_floor`: what main's own rules removed,
+    replayed, less the named suppressions) and the additive rules below.
+
+    ``floor_patterns`` None is the diagnostic surface's floor (main's
+    `sanitize_auth_diagnostic`); the policy surface passes its effective
+    patterns (main's `redact_secrets`). A decoded query value asked about by
+    `_covers_anything` (``_depth`` > 0) gets the additive rules only: the
+    floor already replayed main's URL rule on the text it came from.
+
+    Each pass reads the same, unmodified ``text``. The order of the list does
+    not matter: `apply_redaction_spans` merges overlaps. ``covers`` lets the
+    policy surface add its own patterns to the question asked of a decoded
+    query value (a raw encoded value evades a pattern written for the
+    decoded shape; main decoded before matching, and so does this).
+    """
+    spans = _additive_spans(text, _depth, covers)
+    if _depth == 0:
+        spans.extend(floor_spans(text, floor_patterns)[1])
+    return spans
+
+
+def _additive_spans(text: str, depth: int, covers: Covers | None) -> list[Span]:
+    """The rules added on top of main's floor (revs 1-10 of #234)."""
+    spans = [
+        *_url_spans(text, depth, covers),
+        *_keyword_sep_spans(text),
+        *_keyword_list_spans(text),
+        *_authorization_spans(text),
+        *_bearer_spans(text),
+        *_keyword_ws_spans(text),
+        *_shape_spans(text),
+    ]
+    return widen_over_escapes(text, spans)
+
+
+def widen_over_escapes(text: str, spans: list[Span]) -> list[Span]:
+    """Start no span in the middle of a backslash escape.
+
+    A serialised result reaches the redactor as JSON text, where
+    `json.dumps` spells non-ASCII whitespace as `\\u2003`: a span that starts
+    at the `u` would leave a lone `\\` -- invalid JSON, so a dict result
+    silently became a string. A span preceded by an escaping backslash (an
+    odd run of them) takes the backslash too, so the whole escape goes.
+    """
+    # the length of the backslash run ending just before each position, in
+    # one pass: scanning back from every span start was quadratic in a long
+    # run of backslashes with spans inside it
+    work(len(text) + len(spans))
+    runs = [0] * (len(text) + 1)
+    for i, char in enumerate(text):
+        runs[i + 1] = runs[i] + 1 if char == "\\" else 0
+    widened: list[Span] = []
+    for start, end, replacement in spans:
+        if runs[start] % 2 == 1 and start < end:
+            start -= 1
+        widened.append((start, end, replacement))
+    return widened
+
+
+def merge_redaction_spans(text: str, spans: list[Span]) -> list[Span]:
+    """The disjoint spans `apply_redaction_spans` applies, ascending (see
+    there for the overlap rules)."""
+    work(len(text) + len(spans) * max(1, len(spans).bit_length()))  # scan, sort
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
+    return merged
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
+    (`password=[REDACTED]` is a fixed point on both surfaces; on the policy
+    surface `password= [REDACTED]` becomes `password=[REDACTED]` once, then
+    stays).
+    """
+    merged = merge_redaction_spans(text, spans)
+    work(len(text) + len(merged))
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
@@ -580,21 +1603,10 @@ def sanitize_url_elicitation_url(
 def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
     """Return a display-safe diagnostic string for auth failures."""
     text = str(value)
-
-    def redact_url_match(match: re.Match[str]) -> str:
-        whole = match.group(0)
-        raw_url = whole.rstrip(").,;")  # trailing sentence punctuation, in one pass
-        return redact_auth_url(raw_url) + whole[len(raw_url) :]
-
-    text = re.sub(r"https?://[^\s\"'<>]+", redact_url_match, text)
-    text = re.sub(
-        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
-        r"\1[REDACTED]",
-        text,
-    )
-    text = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
-    text = redact_keyword_values(text, _KEYWORD_KEY_START)
-    text = _JWT_RE.sub("[REDACTED]", text)
+    # Every pass reads the same text; the replacements land in one step
+    # (Consiliency/pmcp#234). The cut is taken afterwards, so truncation can
+    # only ever shorten `[REDACTED]`, never expose a prefix.
+    text = apply_redaction_spans(text, collect_redaction_spans(text))
     return text if max_length is None else text[:max_length]
 
 
diff --git a/src/pmcp/policy/policy.py b/src/pmcp/policy/policy.py
index cac27021c46dcd6c2a066fa779ccf58046bc0c94..669483aed797c075ccd0cb7ff3803bc289effcdd 100644
--- a/src/pmcp/policy/policy.py
+++ b/src/pmcp/policy/policy.py
@@ -22,7 +22,14 @@ from pmcp.types import (
     ServerPolicy,
     ToolPolicy,
 )
-from pmcp.auth import sanitize_auth_diagnostic
+from pmcp.redaction_floor import work
+from pmcp.auth import (
+    REDACTED,
+    Span,
+    apply_redaction_spans,
+    collect_redaction_spans,
+    widen_over_escapes,
+)
 
 if TYPE_CHECKING:
     # Annotation only. `pmcp.manifest`'s package `__init__` imports the loader and
@@ -44,8 +51,21 @@ _ListPolicy = ServerPolicy | ToolPolicy | ResourcePolicy | PromptPolicy
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
+    # Common secret patterns (case-insensitive). main's list, unchanged: it is
+    # the floor (`pmcp.redaction_floor` replays it with main's semantics), and
+    # `_ADDITIVE_DEFAULT_PATTERNS` adds to it (Consiliency/pmcp#234).
     r"(api[_-]?key|apikey)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
     r"(secret|password|passwd|pwd)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
     r"(bearer|token)[\s]+[a-zA-Z0-9._-]+",
@@ -55,6 +75,63 @@ DEFAULT_REDACTION_PATTERNS = [
     r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
 ]
 
+#: Rev 10's forms of the defaults, entry for entry: applied IN ADDITION to
+#: the default they stand beside, whenever that default is effective. They
+#: only add redactions; main's own default, replayed as the floor, still
+#: removes everything it removed.
+_ADDITIVE_DEFAULT_PATTERNS = [
+    # Common secret patterns (case-insensitive). The separator mirrors the
+    # engine's: `:`, `=`, `=>`, `:=`, or `==` followed directly by the value,
+    # on the same line (`[ \t]*`, not `[\s]*`, which let `token:\nthe` -- a
+    # sentence ending in the keyword -- redact the first word of the next
+    # line; `token == expected` is a comparison on this surface too).
+    r"(api[_-]?key|apikey)[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
+    # Not after `:` or `.`: `arn:…:secret:Name` names a secret, it is not one.
+    # Right after a JSON escape it is a key (`\u00a0password=` in a
+    # serialised leaf), as `\b` made it on main.
+    r"(?:(?<![A-Za-z0-9:])|(?<=\\[nrtbf/\"\\])|(?<=\\u[0-9a-fA-F]{4}))(secret|password|passwd|pwd)[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
+    # `token` needs a real separator: the pre-#234 `(bearer|token)\s+…` form
+    # redacted the word after "token" in prose ("token bucket"). Bearer values
+    # are handled unconditionally by `sanitize_auth_diagnostic`.
+    r"(?:\b|(?<=\\[nrtbf/\"\\])|(?<=\\u[0-9a-fA-F]{4}))token[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
+    r"(aws_secret|aws_access)[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
+    r"\bsk-[A-Za-z0-9_-]{6,}\b",
+    r"\bghp_[A-Za-z0-9_]{10,}\b",
+    r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
+]
+
+_ADDITIVE_FOR_DEFAULT = {
+    default: re.compile(additive, re.IGNORECASE)
+    for default, additive in zip(
+        DEFAULT_REDACTION_PATTERNS, _ADDITIVE_DEFAULT_PATTERNS, strict=True
+    )
+}
+
+
+def _additive_form(regex: re.Pattern[str]) -> re.Pattern[str]:
+    return _ADDITIVE_FOR_DEFAULT.get(regex.pattern, regex)
+
+
+def _value_separator(full_match: str) -> int:
+    """Where an additive pattern's match splits into key and value: the first
+    separator (`:` or `=`) that has a value after it, or -1. A separator
+    with nothing but separators after it is base64 padding
+    (`dXNlcjpwYXNzd29yZA==`); splitting there kept the whole secret and
+    replaced the `=`.
+
+    Linear: "a value after it" is one comparison with where the trailing run
+    of separators starts. Rev 10 re-stripped the rest of the match at every
+    position, which is quadratic in a run of `:=` (B3 of its board: 58 s on
+    264 KB, reachable from callers with no window)."""
+    content_end = len(full_match.rstrip(" \t:="))
+    for i, char in enumerate(full_match):
+        if i + 1 >= content_end:
+            return -1
+        if char in ":=":
+            return i
+    return -1
+
+
 # Search order for an auto-discovered policy. The project-local entries are kept
 # RELATIVE on purpose: they are resolved against `Path.cwd()` when a
 # `PolicyManager` is constructed, not when this module is imported. Storing them
@@ -666,18 +743,31 @@ class PolicyManager:
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
@@ -687,29 +777,65 @@ class PolicyManager:
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
+
+    def _additive_regexes(self) -> list[re.Pattern[str]]:
+        """The additive form of each effective pattern: rev 10's form of a
+        default (`_ADDITIVE_DEFAULT_PATTERNS`), an operator's pattern as it
+        is -- applied with the split below, which also keeps base64 padding
+        whole where main's first-separator split did not."""
+        return [_additive_form(regex) for regex in self._redaction_regexes]
+
+    def _pattern_matches(self, text: str) -> bool:
+        """Does any operator (or default) pattern match ``text``? Asked of a
+        percent-decoded query value so an encoded token cannot evade a
+        pattern written for its decoded shape (main decoded before matching)."""
+        return any(
+            regex.search(text)
+            for regex in (*self._redaction_regexes, *self._additive_regexes())
+        )
 
-            def replace_match(match: re.Match[str]) -> str:
+    def redaction_spans(self, output: str) -> list[Span]:
+        """Every redaction either surface would make to ``output``, as spans:
+        main's floor for this surface (the engine's rules, then the effective
+        patterns, replayed in main's order with main's semantics) and the
+        additive rules on top."""
+        spans: list[Span] = collect_redaction_spans(
+            output,
+            covers=self._pattern_matches,
+            floor_patterns=self._redaction_regexes,
+        )
+        for regex in self._additive_regexes():
+            work(len(output))  # the pattern's scan
+            for match in regex.finditer(output):
                 full_match = match.group(0)
-                # Find the separator (: or =)
-                for i, char in enumerate(full_match):
-                    if char in ":=":
-                        return full_match[: i + 1] + " [REDACTED]"
-                return "[REDACTED]"
-
-            result = regex.sub(replace_match, result)
-
-        return result
+                work(2 * len(full_match) + 1)  # the match, the split
+                split = _value_separator(full_match)
+                if split >= 0:
+                    spans.append(
+                        (match.start() + split + 1, match.end(), " " + REDACTED)
+                    )
+                else:
+                    spans.append((match.start(), match.end(), REDACTED))
+        return widen_over_escapes(output, spans)
 
     def process_output(
         self,
@@ -731,11 +857,43 @@ class PolicyManager:
 
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
+            # A structured result is serialised first and the window of the
+            # serialised text redacted: JSON text INSIDE a leaf arrives with
+            # escaped quotes the keyed rules do not read -- scoped out to
+            # Consiliency/pmcp#290; the bar here is never worse than main.
+            spans = self.redaction_spans(window)
+            work(2 * len(window) + len(spans) * max(1, len(spans).bit_length()))
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
diff --git a/src/pmcp/redaction_floor.py b/src/pmcp/redaction_floor.py
new file mode 100644
index 0000000000000000000000000000000000000000..b26a699e9171798e5cd0dbbe26e8461e10d00398
--- /dev/null
+++ b/src/pmcp/redaction_floor.py
@@ -0,0 +1,1639 @@
+"""Main's redaction rules as a floor (Consiliency/pmcp#234).
+
+Four review rounds of the redactor rewrite each found inputs it redacted less
+than `main` did, because the rewrite re-implemented main's rules and every
+gap in a re-implementation is a leak. This module does not re-implement them:
+it REPLAYS them. Main's substitutions run in main's order, each on the
+previous one's output, exactly as `main`'s `sanitize_auth_diagnostic` and
+`PolicyManager.redact_secrets` ran them, and every character of the
+intermediate text carries the position of the input character it came from.
+What main removed is then known exactly, as spans over the input: the floor.
+
+The redactor (`pmcp.auth`) applies every floor span, plus its own additive
+rules on top. A floor span is dropped only when a named suppression predicate
+below says so (`SUPPRESSIONS`); each is decided from the match's own key,
+separator and value, never from any output. So "never worse than main except
+by a named suppression" holds by construction, and review reduces to the
+predicates.
+
+Two things are not a verbatim call of main's code, and both are held to it by
+tests (`tests/test_redaction_floor.py` against `tests/_main_redactor.py`,
+main's functions vendored verbatim): the intermediate text must equal what
+main's functions return, byte for byte, at the end of the replay.
+
+* Main's keyword rule is replayed by `main_keyword_matches`: main's own
+  linear matcher (`pmcp.keyword_matcher`), which yields exactly the matches
+  of the rule's regular expression, with main's frozen key set.
+* Main's URL rewrite (`redact_auth_url`) re-encodes what it keeps; it is
+  called for real, and the output is aligned with the input component by
+  component (`_url_pieces`). If the alignment does not reproduce main's
+  output exactly, the whole URL becomes one floor span.
+
+This module is standalone: it imports nothing from the rest of `pmcp`, and
+main's constants are frozen here, so a change to the live key sets can never
+move the floor.
+"""
+
+from __future__ import annotations
+
+import json
+import re
+from typing import Any
+from collections.abc import Callable, Iterator, Sequence
+from dataclasses import dataclass, field
+from urllib.parse import parse_qsl, quote, unquote, urlparse, urlunparse
+
+from pmcp.keyword_matcher import key_start_pattern, keys_alternation, keyword_matches
+
+REDACTED = "[REDACTED]"
+
+#: Test-only work counter (Consiliency/pmcp#234). None in production; the
+#: complexity tests set it to `[0]` and every loop and string build of the
+#: redactor adds what it iterates over or copies, so linear time is asserted
+#: on a deterministic count, not on the clock.
+WORK: list[int] | None = None
+
+
+def work(amount: int) -> None:
+    if WORK is not None:
+        WORK[0] += amount
+
+
+# --------------------------------------------------------------------------
+# main's constants, frozen (origin/main 1fb36f2, src/pmcp/auth.py and
+# src/pmcp/policy/policy.py).
+
+MAIN_AUTH_SECRET_QUERY_KEYS = frozenset(
+    {
+        "access_token",
+        "api_key",
+        "apikey",
+        "auth",
+        "auth_code",
+        "authorization",
+        "bearer",
+        "client_secret",
+        "code",
+        "id_token",
+        "assertion",
+        "key",
+        "password",
+        "refresh_token",
+        "saml",
+        "secret",
+        "session",
+        "sid",
+        "ticket",
+        "token",
+        "jwt",
+    }
+)
+
+MAIN_DIAGNOSTIC_SECRET_KEYS = frozenset(
+    {
+        "access_token",
+        "api_key",
+        "apikey",
+        "assertion",
+        "client_secret",
+        "code",
+        "cookie",
+        "id_token",
+        "jwt",
+        "password",
+        "refresh_token",
+        "saml",
+        "secret",
+        "session",
+        "set-cookie",
+        "sid",
+        "tenant-id",
+        "tenant_id",
+        "token",
+    }
+)
+
+#: main's `DEFAULT_REDACTION_PATTERNS`, verbatim.
+MAIN_DEFAULT_REDACTION_PATTERNS = (
+    r"(api[_-]?key|apikey)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
+    r"(secret|password|passwd|pwd)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
+    r"(bearer|token)[\s]+[a-zA-Z0-9._-]+",
+    r"(aws_secret|aws_access)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
+    r"\bsk-[A-Za-z0-9_-]{6,}\b",
+    r"\bghp_[A-Za-z0-9_]{10,}\b",
+    r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
+)
+
+_MAIN_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
+_MAIN_AUTHORIZATION_RE = re.compile(
+    r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+"
+)
+_MAIN_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
+_MAIN_JWT_RE = re.compile(
+    r"(?<![A-Za-z0-9_-])"
+    r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
+    r"(?![A-Za-z0-9_-])"
+)
+
+
+# --------------------------------------------------------------------------
+# main's keyword rule: `pmcp.keyword_matcher`, as main runs it.
+
+
+def _main_keys_alternation(keys: frozenset[str]) -> str:
+    return keys_alternation(keys)
+
+
+_KEY_START_RE = key_start_pattern(MAIN_DIAGNOSTIC_SECRET_KEYS)
+
+
+def main_keyword_matches(text: str) -> Iterator[tuple[int, int, int, int]]:
+    """Main's keyword rule's matches over ``text`` (see
+    `pmcp.keyword_matcher.keyword_matches`), with main's frozen key set."""
+    return keyword_matches(text, _KEY_START_RE)
+
+
+# --------------------------------------------------------------------------
+# the tracked replay
+
+
+@dataclass
+class FloorSpan:
+    """One stretch of the input main removed, from one match of one rule.
+
+    ``key``/``sep``/``value`` are the match's own groups as main's regex read
+    them (on the intermediate text); ``before`` is the intermediate text just
+    before the match, which a predicate may read to tell a JSON escape's tail
+    from a glued prefix. ``part`` names which piece of main's removal this is
+    (a policy default's replacement also takes the separator's whitespace and
+    quote, and the `token`/`bearer` word itself: those are split off so a
+    predicate can keep them without keeping the value)."""
+
+    start: int
+    end: int
+    rule: str
+    part: str = "value"
+    key: str = ""
+    sep: str = ""
+    value: str = ""
+    before: str = ""
+    #: the intermediate text just after the match (at most 256 characters)
+    after: str = ""
+    #: the input text this part of the match removed (all its spans)
+    removed: str = ""
+    #: the input just before the match's key (where the key came from the
+    #: input; None where main's own text produced it)
+    raw_key_before: str | None = None
+    #: the spans of one part of one match share a group: the predicates are
+    #: asked once per group, on its first span (every span of it carries the
+    #: same key, separator and value)
+    group: int = 0
+    raw_key_start: int = -1
+    #: `key_readings`, computed once per decision
+    readings_cache: list[KeyReading] | None = None
+    replacement: str = REDACTED
+    suppressed_by: str | None = None
+    #: characters the JSON adjustment kept (syntax only; see `adjust_for_json`)
+    json_kept: list[tuple[int, int]] = field(default_factory=list)
+
+
+# A piece of one step's output: ("keep", a, b) copies cur[a:b] with its
+# origins; ("new", text) is synthesised (("new", text, raw_a, raw_b): standing
+# for that input range only); ("atom", text, raw_a, raw_b) is text
+# derived from the whole raw range (a re-encoded URL component).
+_Piece = tuple
+
+
+class _Tracked:
+    def __init__(self, text: str) -> None:
+        self.raw = text
+        self.cur = text
+        #: per character of `cur`: an atom id (>= 0) or -1 (synthesised)
+        self.org: list[int] = list(range(len(text)))
+        #: per character of `cur`: what it stands for in the input -- its
+        #: origin, or for a synthesised `[REDACTED]` the input it replaced.
+        #: Read only to spell a value as the input wrote it (`source`); what
+        #: was removed is decided by `org` alone.
+        self.rep: list[int] = list(range(len(text)))
+        self.atoms: list[tuple[int, int]] = []
+        self.groups = 0
+        self.step_covered_to = -1
+
+    def atom_range(self, atom: int) -> tuple[int, int]:
+        n = len(self.raw)
+        return (atom, atom + 1) if atom < n else self.atoms[atom - n]
+
+    def new_atom(self, a: int, b: int) -> int:
+        self.atoms.append((a, b))
+        return len(self.raw) + len(self.atoms) - 1
+
+    def raw_ranges(self, a: int, b: int) -> list[tuple[int, int]]:
+        """The input ranges the characters cur[a:b] came from, merged."""
+        work((b - a) * max(1, (b - a).bit_length()))  # the slice, the sort
+        ranges = sorted(self.atom_range(atom) for atom in self.org[a:b] if atom >= 0)
+        merged: list[tuple[int, int]] = []
+        for start, end in ranges:
+            if merged and start <= merged[-1][1]:
+                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
+            else:
+                merged.append((start, end))
+        return merged
+
+    def source(self, a: int, b: int) -> str:
+        """cur[a:b] as the input spelled it: a synthesised marker reads as
+        the input text it replaced."""
+        pieces: list[str] = []
+        # Every character stands for input at or after its predecessor's
+        # (`rewrite` keeps that order), and a step asks in match order, so the
+        # input already spelled out for an earlier match of THIS step is not
+        # copied again: those characters read as the intermediate text itself.
+        # Without that, a many-character piece (a re-encoded URL component, a
+        # marker standing for it) was copied whole once per match inside it:
+        # k x n (B-3 and B-5 of revs 12 and 13). A step copies at most the
+        # input once, plus its own matches' text.
+        copied = 0
+        earlier = self.step_covered_to  # spelled out by an earlier match
+        for position in range(a, b):
+            atom = self.rep[position]
+            if atom < 0:
+                continue
+            start, end = self.atom_range(atom)
+            if end <= earlier:
+                pieces.append(self.cur[position])
+                copied += 1
+                continue
+            if end <= self.step_covered_to:
+                continue  # spelled out already by this match
+            piece = self.raw[max(start, self.step_covered_to) : end]
+            pieces.append(piece)
+            copied += len(piece)
+            self.step_covered_to = end
+        work(b - a + copied)
+        return "".join(pieces)
+
+    def begin_step(self) -> None:
+        """A new pass reads the text afresh: its matches may spell out any
+        input again, once."""
+        self.step_covered_to = -1
+
+    def rewrite(self, edits: list[tuple[int, int, list[_Piece]]]) -> None:
+        """Replace each cur[start:end] by its pieces (edits ascending,
+        disjoint)."""
+        work(len(self.cur) + sum(len(pieces) for _, _, pieces in edits))
+        out: list[str] = []
+        org: list[int] = []
+        rep: list[int] = []
+        position = 0
+        for start, end, pieces in edits:
+            out.append(self.cur[position:start])
+            org.extend(self.org[position:start])
+            rep.extend(self.rep[position:start])
+            kept = bytearray(end - start)
+            for piece in pieces:
+                if piece[0] == "keep":
+                    kept[piece[1] - start : piece[2] - start] = b"\x01" * (
+                        piece[2] - piece[1]
+                    )
+            replaced = [
+                self.atom_range(self.rep[i])
+                for i in range(start, end)
+                if not kept[i - start] and self.rep[i] >= 0
+            ]
+            stands_for = (
+                self.new_atom(min(r[0] for r in replaced), max(r[1] for r in replaced))
+                if replaced
+                else -1
+            )
+            for piece in pieces:
+                if piece[0] == "keep":
+                    out.append(self.cur[piece[1] : piece[2]])
+                    org.extend(self.org[piece[1] : piece[2]])
+                    rep.extend(self.rep[piece[1] : piece[2]])
+                elif piece[0] == "new":
+                    # a marker stands for what it replaced: the input range
+                    # the piece names (a URL's redacted value), nothing
+                    # (`-1`, a URL's added `=`), or the edit's replaced text
+                    marker = stands_for
+                    if len(piece) == 4:
+                        marker = (
+                            self.new_atom(piece[2], piece[3])
+                            if piece[2] < piece[3]
+                            else -1
+                        )
+                    out.append(piece[1])
+                    org.extend([-1] * len(piece[1]))
+                    rep.extend([marker] * len(piece[1]))
+                else:
+                    atom = self.new_atom(piece[2], piece[3])
+                    out.append(piece[1])
+                    org.extend([atom] * len(piece[1]))
+                    rep.extend([atom] * len(piece[1]))
+            position = end
+        out.append(self.cur[position:])
+        org.extend(self.org[position:])
+        rep.extend(self.rep[position:])
+        self.cur = "".join(out)
+        self.org = org
+        self.rep = rep
+
+
+#: What a predicate may read before a match: back to the last whitespace,
+#: quote or angle bracket (a resource name or a glued prefix never crosses
+#: one), at most 256 characters.
+_CONTEXT_STOPS = frozenset(" \t\n\r\f\v\"'<>")
+
+
+def _context(text: str, start: int) -> str:
+    """The text before ``start`` back to the last whitespace, quote or angle
+    bracket, at most 256 characters: one backward walk (a regex anchored at
+    the end restarted at every position of the window)."""
+    k = start
+    limit = max(0, start - 256)
+    while k > limit and text[k - 1] not in _CONTEXT_STOPS and not text[k - 1].isspace():
+        k -= 1
+    work(start - k + 1)
+    return text[k:start]
+
+
+def _counted(matches: Iterator[re.Match[str]]) -> Iterator[re.Match[str]]:
+    """Each match of a pass, counted as the work of reading it."""
+    for match in matches:
+        work(match.end() - match.start() + 1)
+        yield match
+
+
+def _spans_for(
+    tracked: _Tracked, a: int, b: int, rule: str, *, key_at: int = -1, **info: Any
+) -> list[FloorSpan]:
+    """The floor spans of cur[a:b], one part of one match whose key starts at
+    cur[key_at] (-1: no key)."""
+    tracked.groups += 1
+    raw_key_start = -1
+    if 0 <= key_at < len(tracked.org) and tracked.org[key_at] >= 0:
+        raw_key_start = tracked.atom_range(tracked.org[key_at])[0]
+    return [
+        FloorSpan(
+            start,
+            end,
+            rule,
+            group=tracked.groups,
+            raw_key_start=raw_key_start,
+            **info,  # type: ignore[arg-type]
+        )
+        for start, end in tracked.raw_ranges(a, b)
+    ]
+
+
+# ---- the URL step
+
+
+def _requoted(component: str) -> str:
+    return quote(unquote(component.replace("+", " ")))
+
+
+def _url_pieces(
+    raw: str, base: int
+) -> tuple[list[_Piece], list[tuple[int, int, str]]] | None:
+    """Main's `redact_auth_url(raw)` as pieces over the text (``base`` is the
+    URL's offset), plus the labelled input ranges it drops. None when the
+    alignment does not reproduce main's output exactly."""
+    expected = main_redact_auth_url(raw)
+    work(4 * len(raw))  # parse, rewrite, align, compare
+    pieces: list[_Piece] = []
+    dropped: list[tuple[int, int, str]] = []
+    try:
+        parsed = urlparse(raw)
+        port = parsed.port
+    except ValueError:
+        head_end = min(len(raw.split("#", 1)[0]), 400)
+        pieces.append(("keep", base, base + head_end))
+        fragment = raw.find("#")
+        if fragment < 0 or fragment > head_end:
+            if head_end < len(raw):
+                stop = fragment if fragment >= 0 else len(raw)
+                dropped.append((base + head_end, base + stop, "url.overflow"))
+                if fragment >= 0:
+                    dropped.append((base + fragment, base + len(raw), "url.fragment"))
+        else:
+            dropped.append((base + fragment, base + len(raw), "url.fragment"))
+        return (pieces, dropped) if raw[:head_end] == expected else None
+
+    netloc_start = raw.index("://") + 3
+    netloc_end = len(raw)
+    for delimiter in "/?#":
+        index = raw.find(delimiter, netloc_start)
+        if index >= 0:
+            netloc_end = min(netloc_end, index)
+    pieces.append(("keep", base, base + netloc_start))
+    at = raw.rfind("@", netloc_start, netloc_end)
+    host_start = at + 1 if at >= 0 else netloc_start
+    if at >= 0:
+        dropped.append((base + netloc_start, base + host_start, "url.userinfo"))
+    netloc = parsed.hostname or ""
+    if ":" in netloc and not netloc.startswith("["):
+        netloc = f"[{netloc}]"
+    if port:
+        netloc = f"{netloc}:{port}"
+    raw_host = raw[host_start:netloc_end]
+    if netloc == raw_host or (
+        len(netloc) == len(raw_host)
+        and all(o == r or o == r.lower() for o, r in zip(netloc, raw_host))
+    ):
+        pieces.append(("keep", base + host_start, base + netloc_end))
+    elif netloc:
+        pieces.append(("atom", netloc, base + host_start, base + netloc_end))
+    elif raw_host:
+        dropped.append((base + host_start, base + netloc_end, "url.normal"))
+
+    fragment = raw.find("#", netloc_end)
+    rest_end = fragment if fragment >= 0 else len(raw)
+    query_mark = raw.find("?", netloc_end, rest_end)
+    path_end = query_mark if query_mark >= 0 else rest_end
+    path = parsed.path + (f";{parsed.params}" if parsed.params else "")
+    raw_path = raw[netloc_end:path_end]
+    if raw_path.startswith(path):
+        pieces.append(("keep", base + netloc_end, base + netloc_end + len(path)))
+        if len(path) < len(raw_path):
+            dropped.append(
+                (base + netloc_end + len(path), base + path_end, "url.normal")
+            )
+    else:
+        return None
+
+    if query_mark >= 0:
+        pairs: list[list[_Piece]] = []
+        position = query_mark + 1
+        for pair in raw[query_mark + 1 : rest_end].split("&"):
+            pair_start = position
+            position += len(pair) + 1
+            if not pair:
+                continue  # an empty pair: its `&` is labelled below
+            key, equals, value = pair.partition("=")
+            key_start = pair_start
+            value_start = key_start + len(key) + len(equals)
+            pair_pieces: list[_Piece] = []
+            if _requoted(key) == key:
+                pair_pieces.append(
+                    ("keep", base + key_start, base + key_start + len(key))
+                )
+            else:
+                pair_pieces.append(
+                    (
+                        "atom",
+                        _requoted(key),
+                        base + key_start,
+                        base + key_start + len(key),
+                    )
+                )
+            if equals:
+                pair_pieces.append(
+                    ("keep", base + key_start + len(key), base + value_start)
+                )
+            else:
+                pair_pieces.append(("new", "=", -1, -1))
+            decoded_key = unquote(key.replace("+", " "))
+            if decoded_key.lower() in MAIN_AUTH_SECRET_QUERY_KEYS:
+                pair_pieces.append(
+                    (
+                        "new",
+                        quote(REDACTED),
+                        base + value_start,
+                        base + value_start + len(value),
+                    )
+                )
+                if value:
+                    dropped.append(
+                        (
+                            base + value_start,
+                            base + value_start + len(value),
+                            "url.query",
+                        )
+                    )
+            elif _requoted(value) == value:
+                pair_pieces.append(
+                    ("keep", base + value_start, base + value_start + len(value))
+                )
+            else:
+                pair_pieces.append(
+                    (
+                        "atom",
+                        _requoted(value),
+                        base + value_start,
+                        base + value_start + len(value),
+                    )
+                )
+            if pairs:
+                # the `&` before this pair, as written
+                pair_pieces.insert(
+                    0, ("keep", base + pair_start - 1, base + pair_start)
+                )
+            pairs.append(pair_pieces)
+        if pairs:
+            pieces.append(("keep", base + query_mark, base + query_mark + 1))
+            for pair_pieces in pairs:
+                pieces.extend(pair_pieces)
+        else:
+            dropped.append((base + query_mark, base + query_mark + 1, "url.normal"))
+    if fragment >= 0:
+        dropped.append((base + fragment, base + len(raw), "url.fragment"))
+
+    rebuilt = "".join(
+        piece[1]
+        if piece[0] in ("new", "atom")
+        else raw[piece[1] - base : piece[2] - base]
+        for piece in pieces
+    )
+    if rebuilt != expected:
+        return None
+    _label_uncovered(raw, base, pieces, dropped)
+    return pieces, dropped
+
+
+def _label_uncovered(
+    raw: str, base: int, pieces: list[_Piece], dropped: list[tuple[int, int, str]]
+) -> None:
+    """Every input position is either kept by a piece or dropped by a label:
+    whatever no piece keeps and no label names (the `&` of an empty, doubled
+    or trailing pair) is labelled `url.normal` here."""
+    work(2 * len(raw) + len(pieces) + len(dropped))
+    covered = bytearray(len(raw))
+    for piece in pieces:
+        if piece[0] == "keep":
+            covered[piece[1] - base : piece[2] - base] = b"\x01" * (piece[2] - piece[1])
+        elif piece[0] == "atom":
+            covered[piece[2] - base : piece[3] - base] = b"\x01" * (piece[3] - piece[2])
+    for start, stop, _ in dropped:
+        covered[start - base : stop - base] = b"\x01" * (stop - start)
+    position = 0
+    while position < len(raw):
+        if covered[position]:
+            position += 1
+            continue
+        stop = position
+        while stop < len(raw) and not covered[stop]:
+            stop += 1
+        dropped.append((base + position, base + stop, "url.normal"))
+        position = stop
+
+
+def main_redact_auth_url(url: str) -> str:
+    """main's `redact_auth_url`, verbatim but for main's frozen key set."""
+    try:
+        parsed = urlparse(url)
+        port = parsed.port
+    except ValueError:
+        return str(url).split("#", 1)[0][:400]
+    netloc = parsed.hostname or ""
+    if ":" in netloc and not netloc.startswith("["):
+        netloc = f"[{netloc}]"
+    if port:
+        netloc = f"{netloc}:{port}"
+    query_parts = []
+    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
+        if key.lower() in MAIN_AUTH_SECRET_QUERY_KEYS:
+            query_parts.append((key, "[REDACTED]"))
+        else:
+            query_parts.append((key, value))
+    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in query_parts)
+    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, ""))
+
+
+def _url_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
+    # The URL step is the first: cur is the input and every origin is itself.
+    text = tracked.cur
+    work(len(text))
+    edits: list[tuple[int, int, list[_Piece]]] = []
+    for match in _counted(_MAIN_URL_RE.finditer(text)):
+        # main's `redact_url_match`: trailing sentence punctuation is handed
+        # back, stripped in one pass
+        raw = match.group(0).rstrip(").,;")
+        start = match.start()
+        end = start + len(raw)
+        result = _url_pieces(raw, start)
+        if result is None:
+            edits.append((start, end, [("new", main_redact_auth_url(raw))]))
+            spans.append(FloorSpan(start, end, "url.fallback", value=raw))
+            continue
+        pieces, dropped = result
+        edits.append((start, end, pieces))
+        for a, b, label in dropped:
+            if a < b:
+                spans.append(
+                    FloorSpan(
+                        a,
+                        b,
+                        label,
+                        value=text[a:b],
+                        before=_context(text, a),
+                        replacement=REDACTED if label == "url.query" else "",
+                    )
+                )
+    tracked.rewrite(edits)
+
+
+# ---- the regex steps
+
+
+#: A value's wrapping as main's Bearer and Authorization values took it with
+#: the value: opening quotes and brackets before it, closing ones (an escaped
+#: quote included) after it. Split off so `wrapper_syntax` can keep them.
+_WRAP_OPEN_RE = re.compile(r"[\"'(\[{<]+")
+_WRAP_CLOSERS = frozenset("\"')]}>")
+
+
+def _wrap_close_start(text: str, start: int, end: int) -> int:
+    """Where the closing wrapping of text[start:end] begins -- one or more
+    closing quotes or brackets, each optionally escaped, then sentence
+    punctuation, running to ``end`` -- or -1. One right-to-left scan (a
+    search anchored at the end restarts at every closer)."""
+    k = end
+    while k > start and text[k - 1] in ".:!?":
+        k -= 1
+    found = -1
+    while k > start and text[k - 1] in _WRAP_CLOSERS:
+        k -= 1
+        if k > start and text[k - 1] == "\\":
+            k -= 1
+        found = k
+    work(end - k + 1)
+    return found
+
+
+def _header_value_spans(
+    tracked: _Tracked, a: int, end: int, rule: str, scheme_end: int, **info: Any
+) -> list[FloorSpan]:
+    """Floor spans of one Bearer/Authorization value cur[a:end]: the scheme
+    word main took with it (`Authorization: Bearer x` lost `Bearer `), the
+    wrapping, and the value itself."""
+    cur = tracked.cur
+    opening = _WRAP_OPEN_RE.match(cur, scheme_end, end)
+    value_start = opening.end() if opening is not None else scheme_end
+    closing = _wrap_close_start(cur, value_start, end)
+    value_end = closing if closing >= 0 else end
+    if value_end <= value_start:
+        value_start, value_end = scheme_end, end  # nothing but wrapping: one value
+    value = tracked.source(value_start, value_end)
+    spans: list[FloorSpan] = []
+    for start, stop, part in (
+        (a, scheme_end, "scheme"),
+        (scheme_end, value_start, "wrap_open"),
+        (value_start, value_end, "value"),
+        (value_end, end, "wrap_close"),
+    ):
+        if start < stop:
+            spans.extend(
+                _spans_for(tracked, start, stop, rule, part=part, value=value, **info)
+            )
+    return spans
+
+
+def _authorization_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
+    cur = tracked.cur
+    work(len(cur))  # the pass's scan
+    edits = []
+    for m in _counted(_MAIN_AUTHORIZATION_RE.finditer(cur)):
+        a = m.end(1)
+        edits.append((m.start(), m.end(), [("keep", m.start(), a), ("new", REDACTED)]))
+        scheme = m.group(2) or ""
+        spans.extend(
+            _header_value_spans(
+                tracked,
+                a,
+                m.end(),
+                "authorization",
+                a + len(scheme),
+                key=m.group(1),
+                sep=scheme,
+                before=_context(cur, m.start()),
+                after=cur[m.end() : m.end() + 256],
+            )
+        )
+    tracked.rewrite(edits)
+
+
+def _bearer_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
+    cur = tracked.cur
+    work(len(cur))  # the pass's scan
+    edits = []
+    for m in _counted(_MAIN_BEARER_RE.finditer(cur)):
+        a = m.end(1)
+        edits.append((m.start(), m.end(), [("keep", m.start(), a), ("new", REDACTED)]))
+        spans.extend(
+            _header_value_spans(
+                tracked,
+                a,
+                m.end(),
+                "bearer",
+                a,
+                key=m.group(1).rstrip(),
+                sep=m.group(1)[len(m.group(1).rstrip()) :],
+                before=_context(cur, m.start()),
+                after=cur[m.end() : m.end() + 256],
+            )
+        )
+    tracked.rewrite(edits)
+
+
+def _keyword_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
+    cur = tracked.cur
+    edits = []
+    work(len(cur))  # the matcher's scan
+    for start, key_end, value_start, end in main_keyword_matches(cur):
+        work(end - start + 1)
+        edits.append((start, end, [("keep", start, value_start), ("new", REDACTED)]))
+        spans.extend(
+            _spans_for(
+                tracked,
+                value_start,
+                end,
+                "keyword",
+                key_at=start,
+                key=cur[start:key_end],
+                sep=cur[key_end:value_start],
+                value=tracked.source(value_start, end),
+                before=_context(cur, start),
+                after=cur[end : end + 256],
+            )
+        )
+    tracked.rewrite(edits)
+
+
+def _jwt_step(tracked: _Tracked, spans: list[FloorSpan]) -> None:
+    cur = tracked.cur
+    work(len(cur))  # the pass's scan
+    edits = []
+    for m in _counted(_MAIN_JWT_RE.finditer(cur)):
+        edits.append((m.start(), m.end(), [("new", REDACTED)]))
+        spans.extend(
+            _spans_for(
+                tracked,
+                m.start(),
+                m.end(),
+                "jwt",
+                value=m.group(0),
+                before=_context(cur, m.start()),
+            )
+        )
+    tracked.rewrite(edits)
+
+
+#: A separator's syntax as main's policy replacement takes it with the value:
+#: the whitespace, operator characters and opening quote after the first
+#: `:`/`=`.
+_POLICY_SYNTAX_RE = re.compile(r"[\s:=\"']*")
+_POLICY_KEYWORD_RE = re.compile(r"(?i)(?:bearer|token)\s+")
+
+
+def _policy_step(
+    tracked: _Tracked, spans: list[FloorSpan], regex: re.Pattern[str], index: int
+) -> None:
+    cur = tracked.cur
+    work(len(cur))  # the pass's scan
+    edits = []
+    default = regex.pattern in MAIN_DEFAULT_REDACTION_PATTERNS
+    rule = (
+        f"policy:{MAIN_DEFAULT_REDACTION_PATTERNS.index(regex.pattern)}"
+        if default
+        else f"policy:custom{index}"
+    )
+    for m in _counted(regex.finditer(cur)):
+        full = m.group(0)
+        separator = next((i for i, char in enumerate(full) if char in ":="), -1)
+        info: dict[str, Any] = {
+            "before": _context(cur, m.start()),
+            "after": cur[m.end() : m.end() + 256],
+        }
+        if separator >= 0:
+            a = m.start() + separator + 1
+            edits.append(
+                (m.start(), m.end(), [("keep", m.start(), a), ("new", " " + REDACTED)])
+            )
+            syntax_end = _POLICY_SYNTAX_RE.match(cur, a, m.end()).end()  # type: ignore[union-attr]
+            key, sep = (
+                cur[m.start() : m.start() + separator],
+                cur[m.start() + separator : syntax_end],
+            )
+            value = tracked.source(syntax_end, m.end())
+            spans.extend(
+                _spans_for(
+                    tracked,
+                    a,
+                    syntax_end,
+                    rule,
+                    key_at=m.start(),
+                    part="syntax",
+                    key=key,
+                    sep=sep,
+                    value=value,
+                    **info,
+                )
+            )
+            spans.extend(
+                _spans_for(
+                    tracked,
+                    syntax_end,
+                    m.end(),
+                    rule,
+                    key_at=m.start(),
+                    key=key,
+                    sep=sep,
+                    value=value,
+                    **info,
+                )
+            )
+        else:
+            edits.append((m.start(), m.end(), [("new", REDACTED)]))
+            word = (
+                _POLICY_KEYWORD_RE.match(cur, m.start(), m.end())
+                if rule == "policy:2"
+                else None
+            )
+            split = word.end() if word is not None else m.start()
+            key, sep = (
+                (
+                    cur[m.start() : split].rstrip(),
+                    cur[m.start() : split][len(cur[m.start() : split].rstrip()) :],
+                )
+                if word
+                else ("", "")
+            )
+            value = tracked.source(split, m.end())
+            spans.extend(
+                _spans_for(
+                    tracked,
+                    m.start(),
+                    split,
+                    rule,
+                    part="keyword",
+                    key=key,
+                    sep=sep,
+                    value=value,
+                    **info,
+                )
+            )
+            spans.extend(
+                _spans_for(
+                    tracked, split, m.end(), rule, key=key, sep=sep, value=value, **info
+                )
+            )
+    tracked.rewrite(edits)
+
+
+def replay(
+    text: str, patterns: Sequence[re.Pattern[str]] | None = None
+) -> tuple[str, list[FloorSpan]]:
+    """Main's redaction of ``text``, replayed: main's output text, and what it
+    removed as spans over ``text``. ``patterns`` None is main's
+    `sanitize_auth_diagnostic(text, max_length=None)`; a list is main's
+    `PolicyManager.redact_secrets` with those effective compiled patterns."""
+    tracked = _Tracked(text)
+    spans: list[FloorSpan] = []
+    for step in (
+        _url_step,
+        _authorization_step,
+        _bearer_step,
+        _keyword_step,
+        _jwt_step,
+    ):
+        tracked.begin_step()
+        step(tracked, spans)
+    for index, regex in enumerate(patterns or ()):
+        tracked.begin_step()
+        _policy_step(tracked, spans, regex, index)
+    return tracked.cur, spans
+
+
+# --------------------------------------------------------------------------
+# the JSON adjustment
+#
+# Main's Bearer and Authorization values run to whitespace, `,` or `;`, so in
+# a serialised document they ate the closing quote and bracket
+# (`"Bearer x"}` -> `"Bearer [REDACTED]`), and a dict result came back as a
+# string. When the text is a JSON document, a floor span is applied inside the
+# string it starts in instead: the characters main removed outside string
+# interiors are JSON syntax (a delimiting quote, `{}[],:`, whitespace), and
+# are kept; a bare scalar main's span touched is replaced whole by the string
+# `"[REDACTED]"`; and a span never starts or ends inside an escape: it is
+# widened to the whole escape, which only ever removes more -- except that a
+# span ending on an escape's backslash (main took `\\` and left the `"` it
+# escaped) leaves the escape whole: the backslash was syntax, and main kept
+# the character.
+
+_JSON_STRING_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"")
+_JSON_ESCAPE_TOKEN_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\.|[^\\]", re.DOTALL)
+_JSON_SCALAR_TOKEN_RE = re.compile(
+    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|NaN|-?Infinity"
+)
+#: What the adjustment may keep of a floor span: syntax, never content.
+JSON_SYNTAX = frozenset(' \t\r\n"{}[],:')
+
+
+class _JsonLayout:
+    def __init__(self, text: str) -> None:
+        self.text = text
+        work(2 * len(text))
+        #: (start, end) of every string token, quotes included
+        self.strings = [m.span() for m in _JSON_STRING_RE.finditer(text)]
+        self._starts = [start for start, _ in self.strings]
+        #: interior positions where an escape token starts (or a plain char)
+        self.token_start = bytearray(len(text) + 1)
+        for start, end in self.strings:
+            for token in _JSON_ESCAPE_TOKEN_RE.finditer(text, start + 1, end - 1):
+                self.token_start[token.start()] = 1
+            self.token_start[end - 1] = 1
+
+    def string_at(self, index: int) -> tuple[int, int] | None:
+        work(max(1, len(self._starts).bit_length()))
+        import bisect
+
+        i = bisect.bisect_right(self._starts, index) - 1
+        if i >= 0 and self.strings[i][0] <= index < self.strings[i][1]:
+            return self.strings[i]
+        return None
+
+
+def json_layout(text: str) -> _JsonLayout | None:
+    """The layout of ``text`` when it is a JSON document, else None."""
+    work(len(text))
+    head = text.lstrip()[:1]
+    if head not in ("{", "[", '"'):
+        return None
+    try:
+        json.loads(text)
+    except (ValueError, RecursionError):
+        return None
+    return _JsonLayout(text)
+
+
+def adjust_for_json(layout: _JsonLayout, span: FloorSpan) -> list[tuple[int, int, str]]:
+    """The applied form of one floor span over a JSON document (see above).
+    Records what it kept in ``span.json_kept``."""
+    text = layout.text
+    applied: list[tuple[int, int, str]] = []
+    position = span.start
+    while position < span.end:
+        work(1)
+        string = layout.string_at(position)
+        if string is not None and string[0] < position < string[1] - 1:
+            stop = min(span.end, string[1] - 1)
+            start, end = position, stop
+            while not layout.token_start[start]:
+                work(1)
+                start -= 1
+            if (
+                end - 1 >= start
+                and text[end - 1] == "\\"
+                and layout.token_start[end - 1]
+            ):
+                # main took an escape's backslash and left what it escapes:
+                # the backslash is syntax, and the escaped character main kept
+                span.json_kept.append((end - 1, end))
+                end -= 1
+            while not layout.token_start[end]:
+                work(1)
+                end += 1
+            applied.append((start, end, span.replacement))
+            position = stop
+            continue
+        if string is not None:
+            # a delimiting quote
+            span.json_kept.append((position, position + 1))
+            position += 1
+            continue
+        if text[position] not in JSON_SYNTAX:
+            # a bare scalar (outside strings nothing else is not syntax)
+            back = position
+            while back > 0 and text[back - 1] not in JSON_SYNTAX:
+                work(1)
+                back -= 1
+            scalar = _JSON_SCALAR_TOKEN_RE.match(text, back)
+        else:
+            scalar = None
+        if scalar is not None:
+            applied.append((scalar.start(), scalar.end(), f'"{REDACTED}"'))
+            position = scalar.end()
+            continue
+        span.json_kept.append((position, position + 1))
+        position += 1
+    return applied
+
+
+# --------------------------------------------------------------------------
+# the suppression predicates
+
+Predicate = Callable[[FloorSpan], bool]
+
+#: name -> predicate. Filled below; the order is the order they are asked in,
+#: and the first that fires is recorded on the span.
+SUPPRESSIONS: dict[str, Predicate] = {}
+
+#: Qualifiers under which `code` names a status or a description, not a
+#: credential (`status_code=401`, `sqlstate_code=42P01`), matched against
+#: the qualifier's last `_`/`-` segment.
+DIAGNOSTIC_CODE_QUALIFIERS = frozenset(
+    {
+        "area", "byte", "char", "color", "colour", "country", "currency",
+        "err", "errno", "error", "event", "exception", "exit", "fault", "http",
+        "iso", "item", "lang", "language", "locale", "op", "opcode", "postal",
+        "product", "rc", "reason", "region", "response", "result", "ret",
+        "return", "sku", "source", "sqlstate", "state", "status", "zip",
+    }
+)  # fmt: skip
+#: Qualifiers under which a bare `code` is OAuth's (`auth_code=`,
+#: `device_code=`), where only a plain word or number is kept.
+OAUTH_CODE_QUALIFIERS = frozenset(
+    {"", "auth", "authorization", "oauth", "device", "user"}
+)
+#: Keys that are one declared name, not a key word plus a descriptive suffix.
+DECLARED_KEYS = frozenset(
+    {
+        "access_token", "api_key", "apikey", "api-key", "assertion", "auth",
+        "aws_access", "aws_secret", "client_secret", "code", "cookie",
+        "credential", "credentials", "id_token", "jwt", "passwd", "password",
+        "private_key", "privatekey", "private-key", "pwd", "refresh_token",
+        "saml", "secret", "secret_access_key", "session", "set-cookie", "sid",
+        "tenant-id", "tenant_id", "token",
+    }
+)  # fmt: skip
+
+_PLAIN_WORD_RE = re.compile(r"[A-Za-z_]+")
+#: A plain number: a short decimal (a status, an exit code, a JSON-RPC
+#: error: at most 10 digits). A longer digit run or any `0x` hex is not
+#: plain: it may be a numeric token or a hex key, and main's redaction of it
+#: stands.
+_PLAIN_NUMBER_RE = re.compile(r"[+-]?[0-9]{1,10}")
+
+
+def is_plain(value: str) -> bool:
+    """A word (`bucket`, `invalid_grant`) or a number (`401`, `-32601`,
+    `0x80070005`), quotes and sentence punctuation after it allowed."""
+    value = value.strip("\"'").rstrip(".:!?")
+    return bool(_PLAIN_WORD_RE.fullmatch(value) or _PLAIN_NUMBER_RE.fullmatch(value))
+
+
+def looks_like_credential(value: str) -> bool:
+    """D2 of #234: not a plain word or number, and digit-bearing and 6+
+    characters or punctuated and 8+ (`hunter2`, `abc123def456`,
+    `correct-horse-battery`; not `v2`, `re-use`, `expired`)."""
+    value = value.strip("\"'")
+    if is_plain(value):
+        return False
+    return len(value) >= (6 if any(c.isdigit() for c in value) else 8)
+
+
+@dataclass(frozen=True)
+class KeyReading:
+    """One way to read a match's key: `qualifier` (everything before the key
+    word, glued prefix included), `glued` (its trailing alphanumeric run: no
+    joiner before the key word), `name` (the key word), `suffix`."""
+
+    qualifier: str
+    glued: str
+    name: str
+    suffix: str
+
+
+_KEY_WORD_RE = re.compile(
+    rf"(?i)(?=({_main_keys_alternation(MAIN_DIAGNOSTIC_SECRET_KEYS | {'passwd', 'pwd', 'aws_secret', 'aws_access', 'bearer', 'authorization'})}))"
+)
+_ESCAPE_TAIL_RE = re.compile(r"(?:[nrtbf]|u[0-9a-fA-F]{4})")
+
+
+#: A key longer than this is read no way at all, so every predicate that
+#: reads the key declines and main's redaction stands: a run of 11 000
+#: `token-` has 11 000 readings, and reading each is quadratic.
+_MAX_READ_KEY = 256
+
+
+_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
+
+
+def _glued_tail(text: str) -> str:
+    """The run of ASCII letters and digits ``text`` ends with: one backward
+    walk (a regex anchored at the end restarted at every position)."""
+    k = len(text)
+    while k > 0 and text[k - 1] in _ALNUM:
+        k -= 1
+    work(len(text) - k + 1)
+    return text[k:]
+
+
+def key_readings(span: FloorSpan) -> list[KeyReading]:
+    """Every reading of the span's key: each key word in it, and -- when the
+    key starts on the tail of a JSON escape (`\\npassword`) -- with and
+    without that tail. A predicate that reads the key fires only if it fires
+    on EVERY reading, so an ambiguous key is never read the lenient way."""
+    if span.readings_cache is not None:
+        return span.readings_cache
+    key = span.key.rstrip()
+    before = span.before
+    if len(key) > _MAX_READ_KEY:
+        return []  # no reading: no key-reading predicate fires (a cost bound)
+    variants = [(_glued_tail(before), key)]
+    if (len(before) - len(before.rstrip("\\"))) % 2 == 1:
+        tail = _ESCAPE_TAIL_RE.match(key)
+        if tail is not None:
+            variants.append(("", key[tail.end() :]))
+    readings = []
+    for context_glue, text in variants:
+        for word in _KEY_WORD_RE.finditer(text):
+            name = word.group(1)
+            qualifier = text[: word.start()]
+            glued = _glued_tail(qualifier) if qualifier else context_glue
+            readings.append(
+                KeyReading(qualifier, glued, name, text[word.start() + len(name) :])
+            )
+    span.readings_cache = readings
+    return readings
+
+
+def _declared(name: str, suffix: str) -> bool:
+    if (name + suffix).lower() in DECLARED_KEYS:
+        return True
+    return (
+        re.fullmatch(r"[_-]?(?:id|key)|s", suffix.lower()) is not None
+        and name.lower() in DECLARED_KEYS
+    )
+
+
+def _sep_and_value(span: FloorSpan) -> tuple[str, str]:
+    """The separator and value as a reader sees them: an operator run main's
+    value began with (`token==x` backtracks `=` into the value) is part of
+    the separator."""
+    value = _unescaped(span.value)
+    lead = re.match(r"[:=]*", value).group(0)  # type: ignore[union-attr]
+    if lead and value[len(lead) :]:
+        return span.sep + lead, value[len(lead) :]
+    return span.sep, value
+
+
+def _unescaped(value: str) -> str:
+    """A value read in a serialised leaf, with its escaped quotes read as the
+    quotes they are (`\\"type` is `"type`)."""
+    return value.replace('\\"', '"').replace("\\'", "'")
+
+
+_KEYED_RULES = frozenset({"keyword", "policy:0", "policy:1", "policy:3"})
+
+
+def _for_every_reading(span: FloorSpan, test: Callable[[KeyReading], bool]) -> bool:
+    readings = key_readings(span)
+    return bool(readings) and all(test(reading) for reading in readings)
+
+
+def suppression(name: str) -> Callable[[Predicate], Predicate]:
+    def register(predicate: Predicate) -> Predicate:
+        SUPPRESSIONS[name] = predicate
+        return predicate
+
+    return register
+
+
+def _decision_key(span: FloorSpan) -> int:
+    """The predicates are asked once per part of a match (its group): one
+    match can split into many spans around main's own markers, and asking
+    each would rescan the shared value once per span (B-1c of rev 11's
+    board)."""
+    return span.group
+
+
+#: The parts of a match whose removed text a predicate reads.
+_SYNTAX_PARTS = frozenset({"syntax", "keyword", "scheme", "wrap_open", "wrap_close"})
+
+
+def floor_spans(
+    text: str, patterns: Sequence[re.Pattern[str]] | None = None
+) -> tuple[list[FloorSpan], list[tuple[int, int, str]]]:
+    """Every span main removed from ``text`` (``suppressed_by`` set on those
+    a named predicate drops), and the spans the redactor must apply for the
+    rest (adjusted for JSON when ``text`` is a JSON document)."""
+    _, spans = replay(text, patterns)
+    layout = json_layout(text) if spans else None
+    applied: list[tuple[int, int, str]] = []
+    work(len(text) + len(spans))
+    # What a part of a match removed is read only by the syntax predicates,
+    # and only for the syntax parts (a separator's whitespace and quote, a
+    # scheme word, a wrapper): those are bounded by the match. A value part
+    # can be one of many spans over the same many-character input (every
+    # match inside one re-encoded URL component spans all of it), so its
+    # text is never copied here (B-5 of rev 13's board).
+    removed: dict[int, list[str]] = {}
+    for span in spans:
+        if span.part in _SYNTAX_PARTS:
+            piece = text[span.start : span.end]
+            work(len(piece))
+            removed.setdefault(span.group, []).append(piece)
+    joined = {group: "".join(parts) for group, parts in removed.items()}
+    decided: dict[int, str | None] = {}
+    applied_once: set[tuple[int, int, str]] = set()
+    for span in spans:
+        # what the whole part of the match removed, shared by its spans
+        span.removed = joined.get(span.group, "")
+        key = _decision_key(span)
+        if key not in decided:
+            # each predicate reads the match's own strings a bounded number of
+            # times (key readings are bounded by `_MAX_READ_KEY`)
+            work(
+                len(SUPPRESSIONS)
+                * (
+                    len(span.key)
+                    + len(span.sep)
+                    + len(span.value)
+                    + len(span.removed)
+                    + len(span.before)
+                    + len(span.after)
+                    + 256
+                )
+            )
+            if span.raw_key_start >= 0:
+                span.raw_key_before = _context(text, span.raw_key_start)
+            decided[key] = next(
+                (name for name, predicate in SUPPRESSIONS.items() if predicate(span)),
+                None,
+            )
+        span.suppressed_by = decided[key]
+        if span.suppressed_by is not None:
+            continue
+        # one application per input range: many matches inside one
+        # re-encoded component each span all of it
+        if (span.start, span.end, span.replacement) in applied_once:
+            continue
+        applied_once.add((span.start, span.end, span.replacement))
+        if layout is not None:
+            applied.extend(adjust_for_json(layout, span))
+        else:
+            applied.append((span.start, span.end, span.replacement))
+    return spans, applied
+
+
+# The predicates. Each names the stated class it implements (the classes of
+# `tests/_redaction_grammar.py`, approved by the maintainer) and the false
+# positive it exists for; `tests/test_redaction_floor.py` holds, for each,
+# the case it fires on and a credential-shaped case it does not fire on.
+
+
+@suppression("separator_syntax")
+def _separator_syntax(span: FloorSpan) -> bool:
+    """A policy replacement (`key: [REDACTED]`) also took the whitespace, the
+    operator characters and the opening quote between the separator and the
+    value (`password: "x"` lost ` "`). Those are syntax, never content: the
+    value itself is a separate floor span. Keeping them is what keeps
+    `password: "[REDACTED]"` quoted."""
+    return span.part == "syntax" and all(
+        c.isspace() or c in ":=\"'" for c in span.removed
+    )
+
+
+@suppression("policy_keyword_word")
+def _policy_keyword_word(span: FloorSpan) -> bool:
+    """Main's `(bearer|token)\\s+value` default replaced the keyword too
+    (`token abc` became `[REDACTED]`). The word itself is the pattern's own
+    literal vocabulary, never content; the value is a separate floor span."""
+    return span.part == "keyword" and span.removed.strip().lower() in (
+        "token",
+        "bearer",
+    )
+
+
+_SCHEME_WORD_RE = re.compile(r"(?i)bearer\s+")
+
+
+@suppression("scheme_word")
+def _scheme_word(span: FloorSpan) -> bool:
+    """`Authorization: Bearer x` lost the scheme word with the value on main.
+    The word is main's own pattern's literal (`bearer` and whitespace), never
+    content; the value is a separate floor span."""
+    return span.part == "scheme" and _SCHEME_WORD_RE.fullmatch(span.removed) is not None
+
+
+_WRAPPER_CHARS = frozenset("\"'()[]{}<>\\")
+
+
+@suppression("wrapper_syntax")
+def _wrapper_syntax(span: FloorSpan) -> bool:
+    """Main's Bearer and Authorization values ran to whitespace, `,` or `;`,
+    so they took the quote or bracket wrapped around the token (`Bearer
+    "x"`, `Authorization: [x]`) and the one closing the string or object the
+    header sat in (`{'h': 'Bearer x'}`). Quotes, brackets and an escaping
+    backslash at the value's edges are syntax, never content: the value
+    between them is a separate floor span; so is sentence punctuation after a
+    closing one (`"Bearer api"}.`). A quote INSIDE the value
+    (`Bearer abc"SECRETPART`) is not at an edge, and goes with it."""
+    return span.part in ("wrap_open", "wrap_close") and all(
+        c in _WRAPPER_CHARS or (span.part == "wrap_close" and c in ".:!?")
+        for c in span.removed
+    )
+
+
+_GLUED_SUFFIX_RE = re.compile(r"[A-Za-z0-9]*")
+
+
+def _case(char: str) -> int:
+    return 1 if char.isupper() else 2 if char.islower() else 0
+
+
+def _same_case_run(text: str, *, from_end: bool, neighbour: str) -> int:
+    """How long the run of ``text`` next to the key word is before a case
+    change (`production` + `Password`, `PRODUCTION` + `password`, `Server` in
+    `passwordForServer`): a case change joins words as `_` and `-` do (N-5
+    of rev 12's board: camelCase and SHOUTING multi-word keys). Digits
+    continue a run. ``neighbour`` is the key word's letter on that side."""
+    chars = reversed(text) if from_end else iter(text)
+    previous = _case(neighbour)
+    length = 0
+    for char in chars:
+        case = _case(char)
+        if case and previous and case != previous:
+            break
+        if case:
+            previous = case
+        length += 1
+    return length
+
+
+@suppression("N3")
+def _n3(span: FloorSpan) -> bool:
+    """A prefix or suffix of more than 24 alphanumerics GLUED to the key word
+    -- no `_`/`-` and no case change between them and the key word
+    (`<25 letters>password=`, `password<25 letters>=`) -- is an identifier,
+    not a key (the additive rules' cost bound). Joined words are not glued:
+    `DATABASE_PASSWORD_FOR_REPLICATION_USER_ACCOUNT=`,
+    `passwordForTheProductionDatabaseServer=` and
+    `PRODUCTIONDATABASEREPLICATIONpassword=` are keys."""
+
+    def glued_too_long(r: KeyReading) -> bool:
+        prefix = _same_case_run(r.glued, from_end=True, neighbour=r.name[:1])
+        suffix_run = _GLUED_SUFFIX_RE.match(r.suffix).group(0)  # type: ignore[union-attr]
+        suffix = _same_case_run(suffix_run, from_end=False, neighbour=r.name[-1:])
+        return prefix > 24 or suffix > 24
+
+    return span.rule in _KEYED_RULES and _for_every_reading(span, glued_too_long)
+
+
+#: The text before a key that is itself a segment of an `arn:`/`urn:`
+#: resource name: the name's start, then only name characters -- no
+#: whitespace, quote, bracket or pair/list delimiter (`& , ; ( ) ? =`) -- and
+#: the key right after a `:` or `/` segment boundary.
+_NAME_DELIMITER_RE = re.compile(r"[\s\"'<>&,;()?=]")
+_NAME_DELIMITERS = frozenset("\"'<>&,;()?=")
+_RESOURCE_START_RE = re.compile(r"(?i)\b[au]rn:")
+
+
+def _key_is_resource_segment(before: str) -> bool:
+    """Does the text before a key end inside an `arn:`/`urn:` name, right
+    after a `:` or `/` segment boundary? The name's tail is the run back to
+    the last whitespace or name delimiter (`& , ; ( ) ? =`, a quote or angle
+    bracket); it must hold the name's start. One backward walk and one
+    forward search over the tail (a regex anchored at the end restarted at
+    every position)."""
+    if not before or before[-1] not in ":/":
+        return False
+    k = len(before)
+    while (
+        k > 0 and before[k - 1] not in _NAME_DELIMITERS and not before[k - 1].isspace()
+    ):
+        k -= 1
+    work(len(before) - k + 1)
+    return _RESOURCE_START_RE.search(before, k) is not None
+
+
+@suppression("N10")
+def _n10(span: FloorSpan) -> bool:
+    """A key that is a segment of an `arn:`/`urn:` resource name, with its
+    separator and value inside the name too, names a resource
+    (`arn:aws:secretsmanager:...:secret:Name`), and only such a key: a key
+    after the name in the same run (`...:token-exchange&client_secret=x`,
+    `urn:db;password=x`) is a key, and so is a pair that leaves the name
+    (`...:token-exchange=client_secret=x`). Read on the INPUT
+    before the key: an earlier step of main's may already have rewritten the
+    `arn` itself."""
+    return (
+        span.rule in _KEYED_RULES
+        and span.part == "value"
+        and span.raw_key_before is not None
+        and _key_is_resource_segment(span.raw_key_before)
+        # the separator and the value are in the name too: `:` only, and a
+        # value with no name delimiter (`...:token-exchange=client_secret=x`
+        # leaves the name at the `=`)
+        and span.sep.strip(":") == ""
+        and _NAME_DELIMITER_RE.search(span.value) is None
+    )
+
+
+def _last_segment(qualifier: str) -> str:
+    return re.split(r"[_-]", qualifier.strip("_-").lower())[-1]
+
+
+@suppression("C12")
+def _c12(span: FloorSpan) -> bool:
+    """`code` under a diagnostic or descriptive qualifier keeps its value
+    (`status_code=401`, `error_code=E_TIMEOUT_42`, `sqlstate_code=42P01`)."""
+    return span.rule in _KEYED_RULES and _for_every_reading(
+        span,
+        lambda r: r.name.lower() == "code"
+        and _last_segment(r.qualifier) in DIAGNOSTIC_CODE_QUALIFIERS,
+    )
+
+
+def _whitespace_sep(span: FloorSpan) -> bool:
+    sep, _ = _sep_and_value(span)
+    return sep != "" and sep.isspace()
+
+
+@suppression("C3a")
+def _c3a(span: FloorSpan) -> bool:
+    """`code` never counts on a whitespace-only separator (`exit code 137`,
+    `status code 401`, `zip code 94105`)."""
+    return (
+        span.rule == "keyword"
+        and _whitespace_sep(span)
+        and _for_every_reading(span, lambda r: r.name.lower() == "code")
+    )
+
+
+@suppression("C3")
+def _c3(span: FloorSpan) -> bool:
+    """A whitespace-only separator with a value that is not credential-shaped
+    (D2) is prose: `token bucket`, `secret ingredient`, `session expired`,
+    `token v2`."""
+    return (
+        span.rule in ("keyword", "policy:2")
+        and span.part == "value"
+        and _whitespace_sep(span)
+        and not looks_like_credential(_sep_and_value(span)[1])
+    )
+
+
+_PAIR_RE = re.compile(r"[A-Za-z_-]+=[^=]")
+
+
+_PAIR_VALUE_RE = re.compile(r"[A-Za-z_-]+=([^\s\"',;]*)")
+
+
+@suppression("N11")
+def _n11(span: FloorSpan) -> bool:
+    """After a keyword and whitespace, a `name=value` token whose value is
+    not credential-shaped is its own pair, not the keyword's value (`token
+    expires_in=3600`, `token code=404`). Never after `Bearer` or
+    `Authorization`: whatever follows the scheme is the credential
+    (`Bearer abcdef=SECRETPART`), as main read it."""
+    if span.rule not in ("keyword", "policy:2") or span.part != "value":
+        return False
+    if span.key.strip().lower() in ("bearer", "authorization"):
+        return False
+    if not _whitespace_sep(span):
+        return False
+    pair = _PAIR_VALUE_RE.match(_sep_and_value(span)[1] + span.after)
+    return pair is not None and not looks_like_credential(pair.group(1))
+
+
+def _single_case(word: str) -> bool:
+    word = word.lstrip("-_")
+    return (
+        word.isupper() or word.islower() or (word[:1].isupper() and word[1:].islower())
+    )
+
+
+_ACRONYM_TITLE_RE = re.compile(r"[A-Z0-9]{1,8}[A-Z][a-z]+")
+
+
+@suppression("N4")
+def _n4(span: FloorSpan) -> bool:
+    """A glued mixed-case key before whitespace is an identifier, not a key
+    (`Ed25519PrivateKey X509Cert`, `Base64UrlEncoder Sha256HashAlgorithm`)."""
+
+    def mixed_identifier(r: KeyReading) -> bool:
+        key = r.glued + r.name + r.suffix
+        return (
+            bool(r.glued)
+            and not _single_case(key)
+            and _ACRONYM_TITLE_RE.fullmatch(key.lstrip("-_")) is None
+        )
+
+    return (
+        span.rule == "keyword"
+        and _whitespace_sep(span)
+        and _for_every_reading(span, mixed_identifier)
+    )
+
+
+@suppression("C4")
+def _c4(span: FloorSpan) -> bool:
+    """An operator run holding `==` or `::` before a value that is not
+    credential-shaped is a comparison or a path (`if token == expected`,
+    `token::Type`)."""
+    sep, value = _sep_and_value(span)
+    return (
+        span.rule in _KEYED_RULES
+        and span.part == "value"
+        and ("==" in sep or "::" in sep)
+        and not looks_like_credential(value)
+    )
+
+
+@suppression("C5")
+def _c5(span: FloorSpan) -> bool:
+    """A suffixed key with a value that is not credential-shaped names
+    metadata (`token_type=Bearer`, `password_length=12`,
+    `token_endpoint=https://...`)."""
+    value = _sep_and_value(span)[1]
+    return (
+        span.rule == "keyword"
+        and not looks_like_credential(value)
+        and _for_every_reading(
+            span, lambda r: bool(r.suffix) and not _declared(r.name, r.suffix)
+        )
+    )
+
+
+@suppression("C6")
+def _c6(span: FloorSpan) -> bool:
+    """A glued key (no joiner before the key word) counts only with a
+    credential-shaped value that is not a URL or ARN (`unicode input`,
+    `encoded payload`, `mytoken=https://...`)."""
+    value = _sep_and_value(span)[1]
+    return (
+        span.rule in _KEYED_RULES
+        and span.part == "value"
+        and (
+            not looks_like_credential(value)
+            or value.lower() in ("https", "http")
+            and span.after.startswith("://")
+            or value.lower().startswith("arn:")
+        )
+        and _for_every_reading(span, lambda r: bool(r.glued))
+    )
+
+
+@suppression("C7")
+def _c7(span: FloorSpan) -> bool:
+    """After an unquoted key, a value starting on `,` or `}` is JSON
+    structure (`...token: ", "next": ...`)."""
+    sep, value = _sep_and_value(span)
+    return (
+        span.rule in _KEYED_RULES
+        and not sep.startswith('"')
+        and value.startswith((",", "}"))
+    )
+
+
+def unindented_break(text: str) -> bool:
+    """Does ``text`` hold a line break with no space, tab or no-break space
+    after it (the last break decides: an indent after it is after every
+    earlier one too)? One reverse search, not a regex anchored at the end."""
+    work(len(text))
+    last = max(text.rfind("\r"), text.rfind("\n"))
+    return last >= 0 and not any(c in " \t\xa0" for c in text[last + 1 :])
+
+
+@suppression("C8")
+def _c8(span: FloorSpan) -> bool:
+    """A separator whose last line break is unindented, before an unquoted
+    value that is not credential-shaped, ends a sentence (`token:\\nthe
+    bearer of`)."""
+    sep, value = _sep_and_value(span)
+    return (
+        span.rule in (*_KEYED_RULES, "policy:2", "bearer")
+        and span.part == "value"
+        and unindented_break(span.key + sep)
+        and not value.startswith(('"', "'"))
+        and not looks_like_credential(value)
+    )
+
+
+@suppression("C10")
+def _c10(span: FloorSpan) -> bool:
+    """`code` (main's one weak key) keeps a plain word or number
+    (`{"code": -32601}` is every JSON-RPC error), and under a non-OAuth
+    qualifier any value that is not credential-shaped."""
+    value = _sep_and_value(span)[1]
+
+    def weak(r: KeyReading) -> bool:
+        if r.name.lower() != "code" or (r.suffix and not _declared(r.name, r.suffix)):
+            return False
+        if is_plain(value):
+            return True
+        return r.qualifier.strip(
+            "_-"
+        ).lower() not in OAUTH_CODE_QUALIFIERS and not looks_like_credential(value)
+
+    return span.rule in _KEYED_RULES and _for_every_reading(span, weak)
+
+
+def _unwrap(value: str) -> str:
+    if len(value) >= 2 and value[0] + value[-1] in ('""', "''", "()", "[]", "{}", "<>"):
+        return value[1:-1]
+    return value
+
+
+@suppression("C11")
+def _c11(span: FloorSpan) -> bool:
+    """`Authorization`/`Bearer` followed by a plain word, bare or wrapped in
+    quotes or brackets, is prose (`Missing bearer token`, `the bearer of`,
+    `Authorization: required`)."""
+    return (
+        span.rule in ("bearer", "authorization", "policy:2")
+        and span.part == "value"
+        and (span.rule != "policy:2" or span.key.lower() == "bearer")
+        and is_plain(_unwrap(_unescaped(span.value)))
+    )
diff --git a/tests/_main_redactor.py b/tests/_main_redactor.py
new file mode 100644
index 0000000000000000000000000000000000000000..707072bd29024609331dc22392f393d393de9725
--- /dev/null
+++ b/tests/_main_redactor.py
@@ -0,0 +1,164 @@
+"""`main`'s redactor, vendored VERBATIM from `origin/main` (1fb36f2) for the
+floor's fidelity tests (Consiliency/pmcp#234): `pmcp.redaction_floor` replays
+main's rules with origin tracking, and its intermediate text must equal what
+these functions return, byte for byte. Test-only. The keyword rule here is
+the regular expression; main now runs the same rule through
+`pmcp.keyword_matcher`, which yields exactly its matches.
+
+Only `redact_secrets` is adapted: it is a method on main, here a function of
+the text and the effective compiled patterns.
+"""
+
+# ruff: noqa: E501
+from __future__ import annotations
+
+import re
+from urllib.parse import parse_qsl, quote, urlparse, urlunparse
+
+AUTH_SECRET_QUERY_KEYS = {
+    "access_token",
+    "api_key",
+    "apikey",
+    "auth",
+    "auth_code",
+    "authorization",
+    "bearer",
+    "client_secret",
+    "code",
+    "id_token",
+    "assertion",
+    "key",
+    "password",
+    "refresh_token",
+    "saml",
+    "secret",
+    "session",
+    "sid",
+    "ticket",
+    "token",
+    "jwt",
+}
+
+AUTH_DIAGNOSTIC_SECRET_KEYS = {
+    "access_token",
+    "api_key",
+    "apikey",
+    "assertion",
+    "client_secret",
+    "code",
+    "cookie",
+    "id_token",
+    "jwt",
+    "password",
+    "refresh_token",
+    "saml",
+    "secret",
+    "session",
+    "set-cookie",
+    "sid",
+    "tenant-id",
+    "tenant_id",
+    "token",
+}
+
+_JWT_RE = re.compile(
+    r"(?<![A-Za-z0-9_-])"
+    r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
+    r"(?![A-Za-z0-9_-])"
+)
+
+
+def redact_auth_url(url: str) -> str:
+    """Strip URL userinfo and redact auth-bearing query values."""
+    try:
+        parsed = urlparse(url)
+        port = parsed.port
+    except ValueError:
+        return str(url).split("#", 1)[0][:400]
+    netloc = parsed.hostname or ""
+    if ":" in netloc and not netloc.startswith("["):
+        netloc = f"[{netloc}]"
+    if port:
+        netloc = f"{netloc}:{port}"
+    query_parts = []
+    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
+        if key.lower() in AUTH_SECRET_QUERY_KEYS:
+            query_parts.append((key, "[REDACTED]"))
+        else:
+            query_parts.append((key, value))
+    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in query_parts)
+    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, ""))
+
+
+def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
+    """Return a display-safe diagnostic string for auth failures."""
+    text = str(value)
+
+    def redact_url_match(match: re.Match[str]) -> str:
+        raw_url = match.group(0)
+        suffix = ""
+        while raw_url and raw_url[-1] in ").,;":
+            suffix = raw_url[-1] + suffix
+            raw_url = raw_url[:-1]
+        return redact_auth_url(raw_url) + suffix
+
+    text = re.sub(r"https?://[^\s\"'<>]+", redact_url_match, text)
+    text = re.sub(
+        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
+        r"\1[REDACTED]",
+        text,
+    )
+    text = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
+    secret_keys = "|".join(
+        [
+            *[re.escape(key) for key in AUTH_DIAGNOSTIC_SECRET_KEYS],
+            r"api[_-]?key",
+        ]
+    )
+    text = re.sub(
+        rf"(?i)\b([A-Za-z0-9_-]*(?:{secret_keys})[A-Za-z0-9_-]*)"
+        r"([\s:=]+)([A-Za-z0-9._~+/=-]{3,})",
+        r"\1\2[REDACTED]",
+        text,
+    )
+    text = _JWT_RE.sub("[REDACTED]", text)
+    return text if max_length is None else text[:max_length]
+
+
+DEFAULT_REDACTION_PATTERNS = [
+    # Common secret patterns (case-insensitive)
+    r"(api[_-]?key|apikey)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
+    r"(secret|password|passwd|pwd)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
+    r"(bearer|token)[\s]+[a-zA-Z0-9._-]+",
+    r"(aws_secret|aws_access)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
+    r"\bsk-[A-Za-z0-9_-]{6,}\b",
+    r"\bghp_[A-Za-z0-9_]{10,}\b",
+    r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
+]
+
+
+def compiled_default_patterns() -> list[re.Pattern[str]]:
+    return [re.compile(p, re.IGNORECASE) for p in DEFAULT_REDACTION_PATTERNS]
+
+
+def redact_secrets(
+    output: str, redaction_regexes: list[re.Pattern[str]] | None = None
+) -> str:
+    """Redact secrets from output."""
+    result = sanitize_auth_diagnostic(output, max_length=None)
+
+    if redaction_regexes is None:
+        redaction_regexes = compiled_default_patterns()
+    for regex in redaction_regexes:
+
+        def replace_match(match: re.Match[str]) -> str:
+            full_match = match.group(0)
+            # Find the separator (: or =)
+            for i, char in enumerate(full_match):
+                if char in ":=":
+                    return full_match[: i + 1] + " [REDACTED]"
+            return "[REDACTED]"
+
+        result = regex.sub(replace_match, result)
+
+    return result
diff --git a/tests/_redaction_grammar.py b/tests/_redaction_grammar.py
new file mode 100644
index 0000000000000000000000000000000000000000..997d19fc87ac309681275ed9b8244f16f5933fb1
--- /dev/null
+++ b/tests/_redaction_grammar.py
@@ -0,0 +1,1138 @@
+"""The grammar-derived never-worse-than-main differential (Consiliency/pmcp#234, rev 10).
+
+Revs 8 and 9 each passed a differential whose axes were listed by hand from
+past findings, with zero unaccepted rows, and each was then blocked by inputs
+on an axis nobody had listed. This corpus is derived from the ORACLE's
+grammar instead -- main's rules plus what `json.dumps` emits -- so an axis
+cannot be missing because nobody thought of it.
+
+Main's rules (`main:src/pmcp/auth.py`, `main:src/pmcp/policy/policy.py`):
+
+* the keyword rule
+  `(?i)\\b[A-Za-z0-9_-]*(?:KEYS|api[_-]?key)[A-Za-z0-9_-]*[\\s:=]+[A-Za-z0-9._~+/=-]{3,}`;
+* `(?i)authorization\\s*[:=]\\s*(bearer\\s+)?[^\\s,;]+` and `(?i)\\bbearer\\s+[^\\s,;]+`;
+* the policy defaults' key words (`passwd`, `pwd`, `aws_secret`, `aws_access`);
+* `process_output(dict)`: `json.dumps(obj, indent=2)` (`ensure_ascii=True`),
+  whose escapes are `\\" \\\\ \\b \\f \\n \\r \\t` and `\\uXXXX`, and whose bare
+  scalars are `null true false`, integers, floats, `NaN` and `+-Infinity`.
+
+A text row is `pre + qual + name + suffix + sep + value`, each part drawn from
+the construct it stands for:
+
+=========  ================================================================
+axis       alphabet / grammar
+=========  ================================================================
+pre        the character before the key: none; every ASCII punctuation
+           character but the joiners (`-`, `_`); `:`; `\\`; all 29
+           `str.isspace()` characters; control characters (serialised as
+           `\\u0000 \\u0001 \\b \\u001b \\u007f`); non-ASCII letters,
+           punctuation and an astral character (serialised as `\\uXXXX` or a
+           surrogate pair); an `arn:` / `urn:` resource-name context. Each
+           optionally after the word `abc`.
+qual       main's `[A-Za-z0-9_-]*` prefix: joined (`x_`, `x-`), glued in
+           random case (1-8), glued past the 24 bound (25-32), a leading
+           joiner run (`_ - -- --- _-`), 2-10 joined segments.
+name       main's 20 keys plus the policy keys, lower / UPPER / Title /
+           rAnDoM case; 30% of name-focus rows are `code` under a
+           qualifier: a random word, a diagnostic one
+           (`DIAGNOSTIC_CODE_QUALIFIERS`, class C12) or a credential one
+           (`key otp mfa sms verification recovery invite promo coupon
+           discount api access secret`), in any case, `_`- or `-`-joined.
+suffix     main's `[A-Za-z0-9_-]*` suffix: declared (`s _id _key Key -id id
+           key`), a trailing joiner or joiner run, descriptive (`_new 2 Hash
+           _type _length ized ...`), random, and past the 24 bound.
+sep        main's `[\\s:=]+`: EVERY 1- and 2-character string over the 31
+           characters `str.isspace()` + `:` + `=`, plus sampled 3-character
+           ones; focus rows draw 1-3 characters, half operator, half space.
+value      main's value class `[A-Za-z0-9._~+/=-]{3,14}`, plain words,
+           integers, quoted, unterminated-quote, bracketed, JSON literals and
+           punctuated values.
+bearer     `bearer\\s+[^\\s,;]+` after 13 contexts (`X-Auth: `, `x=`,
+           `token: `, ...) or a random punctuation/non-ASCII/space character,
+           a 1-3 character `\\s` run, a credential optionally wrapped in
+           `"" '' () [] {} <>`.
+authz      `authorization\\s*[:=]\\s*(bearer\\s+)?[^\\s,;]+`: a prefix, 0-2
+           `\\s` characters each side of the operator, a scheme (or none, or
+           one followed by a blank line), a credential or plain value,
+           optionally wrapped.
+wrap       where the pair sits: bare; in prose (a word and one ASCII
+           punctuation or `str.isspace()` character on each side -- what
+           ends main's `\\b` and value class); or in each position of a URL
+           main's URL rule reads: path, query pair, query value, fragment.
+url        main's URL grammar itself (`https?://[^\\s"'<>]+`, trailing
+           `).,;` handed back, then `redact_auth_url`): userinfo (user,
+           user:password, :password), hosts (one whose port does not parse,
+           which makes main keep the URL as written), 0-3 query pairs whose
+           key is or is not in `AUTH_SECRET_QUERY_KEYS` (any case, optionally
+           percent-encoded) with a credential, plain, percent-encoded or
+           empty value, and a fragment (none, a word, a secret,
+           `access_token=` + a secret), in prose. The secrets are what main
+           removes.
+scalars    every scalar `json.dumps` emits under 37 keys (the 24 above plus
+           `auth credentials authorization Authorization bearer tokens
+           input_tokens max_tokens token_type Password API_KEY privateKey
+           aws_secret_access_key`), top-level, nested and in a list of
+           objects.
+=========  ================================================================
+
+Every text row is observed on 12 surfaces: `E`/`P` (`sanitize_auth_diagnostic`,
+`PolicyManager.redact_secrets`) on the raw text; `E`/`P` on each of the four
+spellings `json.dumps({"t": text}, ensure_ascii in (True, False), indent in
+(None, 2))` (`EjAC EjAI EjUC EjUI PjAC PjAI PjUC PjUI`); `process_output(text)`
+(`POs`); and `process_output({"t": text})` (`POd`, the dict-leaf path), whose
+result type is recorded too. A scalar row is observed on `POo`, with its type.
+A piece counts as present in an output as written or percent-decoded (a query
+value's `+` read as a space): main's URL rule re-encodes what it keeps, and
+a re-encoded secret is still there.
+
+Sampling density (`corpus(tier)`; tier 2 is tier 1 followed by the rest):
+
+===============  =====================================  ======================
+block            tier 1 (the default suite)             tier 2 adds (`slow`)
+===============  =====================================  ======================
+sep              the 992 1- and 2-character separators  the 992 x `secret`,
+                 x `password`, `token` x {credential,   `api_key`, `session`
+                 plain}: 3 968                          x 2, and 3 000 sampled
+                                                        3-character ones x 5
+                                                        keys x 2: 35 952
+pre              each of the 77 pre characters x        lead `abc` x 5 keys,
+                 `password`, `token` x (`=`, `: `,      no lead x 3 more
+                 ` `) x {credential, plain}: 924        keys: 3 696
+code             `code` under each of the 37 diagnostic  --
+                 and 13 credential qualifiers x (`=`,
+                 `: `) x {credential, plain}: 200
+focus:<axis>     500 per axis (pre qual name suffix     9 500 per axis:
+                 sep value wrap bearer authz url):      95 000
+                 5 000
+mix              1 000 (every axis at once, the wrap    19 000
+                 included)
+scalar           13 scalars x 37 keys x 3 shapes:       --
+                 1 443
+===============  =====================================  ======================
+
+Tier 1 is 12 535 rows; tier 2 is 166 183. A focus row varies one axis and
+keeps the others benign (no pre, qualifier, suffix or wrap; `=` or `: `; a
+credential value), so a failure is attributed to one axis; mix rows vary
+them all at once (`-password hunter22x` was found only there).
+
+How it runs: tier 1 is `test_grammar_differential_never_worse_than_main_
+except_by_stated_class` in the default suite. Tier 2 is
+`test_grammar_differential_full_set`, marked `slow`: `pytest
+tests/test_redaction.py -m slow` (about 2-3 minutes). `addopts` excludes
+`slow` by default, so CI -- which runs `pytest tests/` with `addopts` --
+does NOT run it.
+
+This module is stdlib-only and imports nothing from `pmcp`: the fixture is
+recorded by running it against `main`'s tree.
+"""
+
+from __future__ import annotations
+
+import hashlib
+import json
+import random
+import re
+from collections.abc import Callable
+from typing import Any
+from urllib.parse import unquote
+
+# ---------------------------------------------------------------- alphabets
+
+#: Every character `str.isspace()` accepts -- Python's `\s` (pinned against a
+#: full enumeration by the axis test, not computed here: the enumeration
+#: depends on the interpreter's Unicode version).
+SPACES = (
+    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003"
+    "\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
+)
+#: main's `[\s:=]`.
+SEP_ALPHABET = SPACES + ":="
+#: Every ASCII punctuation character but the identifier joiners `-`/`_`
+#: (the `qual` axis) and `:`/`\` (classes of their own).
+ASCII_PUNCT = "!\"#$%&'()*+,./;<=>?@[]^`{|}~"
+#: -> `\u0000 \u0001 \b \u001b \u007f` in `json.dumps` output.
+CONTROL = "\x00\x01\x08\x1b\x7f"
+#: A letter, CJK, bullet, curly quote, dash, middle dot, astral emoji (a
+#: surrogate pair when escaped), sharp s, Greek and Cyrillic.
+NON_ASCII = "\xe9\u5bc6\u2022\u201c\u2014\xb7\U0001f642\xdf\u03a9\u0418"
+#: A resource-name context: a key inside it names a resource.
+RESOURCE = ("arn:aws:secretsmanager:us-east-1:1:secret:", "urn:ietf:params:oauth:")
+PRE_CHARS: dict[str, tuple[str, ...]] = {
+    "start": ("",),
+    "ascii-punct": tuple(ASCII_PUNCT),
+    "colon": (":",),
+    "backslash": ("\\",),
+    "isspace": tuple(SPACES),
+    "control": tuple(CONTROL),
+    "non-ascii": tuple(NON_ASCII),
+    "resource": RESOURCE,
+}
+MAIN_KEYS = (
+    "access_token",
+    "api_key",
+    "api-key",
+    "apikey",
+    "assertion",
+    "client_secret",
+    "code",
+    "cookie",
+    "id_token",
+    "jwt",
+    "password",
+    "refresh_token",
+    "saml",
+    "secret",
+    "session",
+    "set-cookie",
+    "sid",
+    "tenant-id",
+    "tenant_id",
+    "token",
+)
+POLICY_KEYS = ("passwd", "pwd", "aws_secret", "aws_access")
+KEYS = MAIN_KEYS + POLICY_KEYS
+IDENT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
+MAIN_VALUE = IDENT + "._~+/=-"
+PLAIN_WORDS = (
+    "letmeinnow",
+    "expired",
+    "required",
+    "bucket",
+    "none",
+    "hunter",
+    "changeme",
+    "Basic",
+)
+DIGITS = "0123456789"
+SCALARS: dict[str, object] = {
+    "null": None,
+    "true": True,
+    "false": False,
+    "0": 0,
+    "-1": -1,
+    "2**100": 2**100,
+    "1.5": 1.5,
+    "-0.0": -0.0,
+    "1e+20": 1e20,
+    "1e-07": 1e-7,
+    "NaN": float("nan"),
+    "Infinity": float("inf"),
+    "-Infinity": float("-inf"),
+}
+SCALAR_KEYS = KEYS + (
+    "auth",
+    "credentials",
+    "authorization",
+    "Authorization",
+    "bearer",
+    "tokens",
+    "input_tokens",
+    "max_tokens",
+    "token_type",
+    "Password",
+    "API_KEY",
+    "privateKey",
+    "aws_secret_access_key",
+)
+BEARER_PRE = (
+    "",
+    "X-Auth: ",
+    "X-Auth:",
+    "x=",
+    "token: ",
+    "session=",
+    "Cookie: session=",
+    "(",
+    '"',
+    "use a ",
+    "headers: {X-Auth: ",
+    "X-Api-Token: ",
+    "auth: ",
+)
+FOCI = ("pre", "qual", "name", "suffix", "sep", "value", "wrap")
+#: main's `AUTH_SECRET_QUERY_KEYS`: the query keys `redact_auth_url` redacts
+#: (after `parse_qsl` decodes them, case-insensitively).
+MAIN_QUERY_KEYS = (
+    "access_token",
+    "api_key",
+    "apikey",
+    "auth",
+    "auth_code",
+    "authorization",
+    "bearer",
+    "client_secret",
+    "code",
+    "id_token",
+    "assertion",
+    "key",
+    "password",
+    "refresh_token",
+    "saml",
+    "secret",
+    "session",
+    "sid",
+    "ticket",
+    "token",
+    "jwt",
+)
+#: The stated diagnostic class (C12): `code` under one of these qualifiers
+#: (its last `_`/`-` segment) names a status or a descriptive code and keeps
+#: its value. Pinned equal to `pmcp.auth._STATUS_CODE_QUALIFIERS` by a test.
+DIAGNOSTIC_CODE_QUALIFIERS = frozenset(
+    {
+        "area",
+        "byte",
+        "char",
+        "color",
+        "colour",
+        "country",
+        "currency",
+        "err",
+        "errno",
+        "error",
+        "event",
+        "exception",
+        "exit",
+        "fault",
+        "http",
+        "iso",
+        "item",
+        "lang",
+        "language",
+        "locale",
+        "op",
+        "opcode",
+        "postal",
+        "product",
+        "rc",
+        "reason",
+        "region",
+        "response",
+        "result",
+        "ret",
+        "return",
+        "sku",
+        "source",
+        "sqlstate",
+        "state",
+        "status",
+        "zip",
+    }
+)
+#: Qualifiers under which `code` names a credential or a redeemable value.
+CREDENTIAL_CODE_QUALIFIERS = (
+    "key",
+    "otp",
+    "mfa",
+    "sms",
+    "verification",
+    "recovery",
+    "invite",
+    "promo",
+    "coupon",
+    "discount",
+    "api",
+    "access",
+    "secret",
+)
+EXHAUSTIVE_KEYS = ("password", "token", "secret", "api_key", "session")
+
+Row = dict[str, Any]
+
+
+def recase(rng: random.Random, word: str, how: str | None = None) -> str:
+    how = how or rng.choice(["lower", "upper", "title", "random"])
+    if how == "lower":
+        return word.lower()
+    if how == "upper":
+        return word.upper()
+    if how == "title":
+        return word[:1].upper() + word[1:]
+    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in word)
+
+
+def cred(rng: random.Random) -> str:
+    """A credential-shaped value from main's value class: alphanumerics with a
+    digit after the first character, which is a lower-case letter."""
+    n = rng.randint(8, 16)
+    s = [rng.choice(IDENT) for _ in range(n)]
+    s[0] = rng.choice("abcdefghijkmnpqrstuvwxyz")
+    s[rng.randrange(1, n)] = rng.choice(DIGITS)
+    return "".join(s)
+
+
+def ident_run(rng: random.Random, lo: int, hi: int, joiners: bool = True) -> str:
+    alphabet = IDENT + ("_-" if joiners else "")
+    return "".join(rng.choice(alphabet) for _ in range(rng.randint(lo, hi)))
+
+
+def sep_kind(sep: str) -> str:
+    if any(c in "\r\n" for c in sep):
+        return "line-break"
+    if sep.count("=") >= 2 or sep.count(":") >= 2:
+        return "operator-run"
+    if sep.isspace():
+        return "whitespace-only"
+    return "mixed"
+
+
+def _sample_pre(rng: random.Random, focus: bool) -> tuple[str, str]:
+    if not focus:
+        return "start", ""
+    cls = rng.choice(list(PRE_CHARS))
+    lead = "" if cls == "resource" else rng.choice(["", "abc"])
+    return cls, lead + rng.choice(PRE_CHARS[cls])
+
+
+def _sample_qual(rng: random.Random, focus: bool) -> tuple[str, str]:
+    if not focus:
+        return "none", ""
+    kind = rng.choice(
+        ["joined", "glued", "glued-long", "leading-joiner", "multi-segment"]
+    )
+    if kind == "joined":
+        return kind, ident_run(rng, 1, 6, False) + rng.choice("_-")
+    if kind == "glued":
+        return kind, recase(rng, ident_run(rng, 1, 8, False))
+    if kind == "glued-long":
+        return kind, ident_run(rng, 25, 32, False)
+    if kind == "leading-joiner":
+        return kind, rng.choice(["_", "-", "--", "---", "_-"])
+    segments = rng.randint(2, 10)
+    return kind, "".join(
+        ident_run(rng, 1, 3, False) + rng.choice("_-") for _ in range(segments)
+    )
+
+
+def _sample_suffix(rng: random.Random, focus: bool) -> tuple[str, str]:
+    if not focus:
+        return "none", ""
+    kind = rng.choice(["declared", "trailing-joiner", "descriptive", "random", "long"])
+    if kind == "declared":
+        return kind, rng.choice(["s", "_id", "_key", "Key", "-id", "id", "key"])
+    if kind == "trailing-joiner":
+        return kind, rng.choice(["_", "-", "__", "_-", "--"])
+    if kind == "descriptive":
+        return kind, rng.choice(
+            [
+                "_new",
+                "_old",
+                "2",
+                "Hash",
+                "_hash",
+                "_PROD",
+                "_type",
+                "_length",
+                "ized",
+                "_value",
+            ]
+        )
+    if kind == "long":
+        return kind, "_" + ident_run(rng, 25, 30, False)
+    return kind, ident_run(rng, 1, 8)
+
+
+def _sample_sep(rng: random.Random, focus: bool) -> tuple[str, str]:
+    if not focus:
+        return "default", rng.choice(["=", ": "])
+    n = rng.choice([1, 2, 2, 3, 3])
+    sep = "".join(
+        rng.choice(":=") if rng.random() < 0.5 else rng.choice(SPACES) for _ in range(n)
+    )
+    return sep_kind(sep), sep
+
+
+def _sample_value(rng: random.Random, focus: bool) -> tuple[str, str]:
+    if not focus:
+        return "default", cred(rng)
+    kind = rng.choice(
+        [
+            "main-class",
+            "plain",
+            "number",
+            "quoted",
+            "unterminated-quote",
+            "bracketed",
+            "json-literal",
+            "punctuated",
+        ]
+    )
+    if kind == "main-class":
+        v = "".join(rng.choice(MAIN_VALUE) for _ in range(rng.randint(3, 14)))
+    elif kind == "plain":
+        v = rng.choice(PLAIN_WORDS)
+    elif kind == "number":
+        v = str(rng.randint(0, 10 ** rng.randint(1, 12)))
+    elif kind == "quoted":
+        q = rng.choice("\"'")
+        v = q + cred(rng) + q
+    elif kind == "unterminated-quote":
+        v = rng.choice("\"'") + cred(rng)
+    elif kind == "bracketed":
+        a, b = rng.choice(["()", "[]", "{}", "<>"])
+        v = a + cred(rng) + b
+    elif kind == "json-literal":
+        v = rng.choice(
+            ["null", "true", "false", "NaN", "Infinity", "-Infinity", "-0.0", "1e+20"]
+        )
+    else:
+        v = cred(rng) + rng.choice(["!", "@x", "#1", "$", "%2F", "&x", "*"])
+    return kind, v
+
+
+def _text(f: dict[str, str]) -> str:
+    return (
+        f"{f.get('left', '')}{f['pre']}{f['qual']}{f['name']}{f['suffix']}"
+        f"{f['sep']}{f['value']}{f.get('right', '')}"
+    )
+
+
+def _pct(rng: random.Random, text: str) -> str:
+    """Percent-encode one to three characters of ``text`` (as `parse_qsl`
+    decodes it)."""
+    chars = list(text)
+    for i in rng.sample(range(len(chars)), min(len(chars), rng.randint(1, 3))):
+        chars[i] = "%{:02X}".format(ord(chars[i]))
+    return "".join(chars)
+
+
+_URL_BASE = ("https://h.example", "http://h.example:8443", "https://127.0.0.1")
+
+
+def _sample_wrap(rng: random.Random, focus: bool) -> tuple[str, str, str]:
+    """Where the pair sits: bare, in prose (a word and a delimiter each side:
+    what ends main's `\\b` and value class), or in each position of a URL
+    main's URL rule reads (path, query pair, query value, fragment)."""
+    if not focus:
+        return "none", "", ""
+    kind = rng.choice(
+        [
+            "none",
+            "none",
+            "prose",
+            "prose",
+            "url-path",
+            "url-query-pair",
+            "url-query-value",
+        ]
+        + ["url-fragment"]
+    )
+    if kind == "none":  # so the mix block keeps unwrapped pairs too
+        return kind, "", ""
+    base = rng.choice(_URL_BASE)
+    if kind == "prose":
+        left = rng.choice(["", "abc", "error:", "log"]) + rng.choice(
+            ASCII_PUNCT + SPACES
+        )
+        right = rng.choice(ASCII_PUNCT + SPACES) + rng.choice(["", "tail", "x=1"])
+    elif kind == "url-path":
+        left, right = base + "/p/", rng.choice(["", "/v1", "?q=1"])
+    elif kind == "url-query-pair":
+        left = base + "/?" + rng.choice(["", "x=1&"])
+        right = rng.choice(["", "&y=2", "#f"])
+    elif kind == "url-query-value":
+        left, right = base + "/?q=", rng.choice(["", "&y=2"])
+    else:
+        left, right = base + "/#", ""
+    return kind, left, right
+
+
+def _url_row(rng: random.Random) -> Row:
+    """A URL from main's URL grammar (`https?://[^\\s"'<>]+`, trailing `).,;`
+    handed back, then `redact_auth_url`): userinfo (dropped), a host (one
+    whose port does not parse makes main keep the URL as written), query
+    pairs whose key is or is not in `AUTH_SECRET_QUERY_KEYS` (any case,
+    optionally percent-encoded) with a credential, plain, percent-encoded or
+    empty value, and a fragment (dropped). The pieces are what main removes:
+    userinfo passwords, values under a secret key, fragment secrets -- one
+    per secret, so at most five."""
+    secrets: list[str] = []
+
+    def secret(value: str) -> str:
+        secrets.append(value)
+        return value
+
+    kinds = []
+    ui = rng.choice(["none", "user", "user-pass", "pass-only"])
+    userinfo = {"none": "", "user": "user@"}.get(ui) or (
+        ("user:" if ui == "user-pass" else ":") + secret(cred(rng)) + "@"
+    )
+    if ui != "none":
+        kinds.append("userinfo")
+    host = rng.choice(
+        ["h.example", "h.example:8443", "127.0.0.1", "[::1]", "h.example:99999"]
+    )
+    path = rng.choice(["", "/", "/v1/items", "/cb"])
+    pairs = []
+    for _ in range(rng.randint(0, 3)):
+        key_kind = rng.choice(["secret", "secret", "secret-encoded", "other"])
+        if key_kind == "other":
+            key = rng.choice(["q", "page", "access", "next", "redirect", "u"])
+        else:
+            key = recase(rng, rng.choice(MAIN_QUERY_KEYS))
+            if key_kind == "secret-encoded":
+                key = _pct(rng, key)
+        value_kind = rng.choice(["cred", "plain", "encoded", "empty"])
+        value = {
+            "cred": cred(rng),
+            # a non-secret key's plain value never repeats a secret's word,
+            # or a kept `redirect=Basic` would read as a kept secret `Basic`
+            "plain": rng.choice(
+                PLAIN_WORDS if key_kind != "other" else ("home", "en", "2")
+            ),
+            "encoded": _pct(rng, cred(rng)),
+            "empty": "",
+        }[value_kind]
+        if key_kind != "other" and value:
+            secret(value)
+        kinds.append(f"query-{key_kind}-{value_kind}")
+        pairs.append(f"{key}={value}")
+    query = ("?" + "&".join(pairs)) if pairs else rng.choice(["", "?"])
+    fragment = rng.choice(["", "#top", "#access_token=", "#"])
+    if fragment in ("#access_token=", "#"):
+        fragment += secret(cred(rng))
+        kinds.append("fragment")
+    left = rng.choice(["", "GET ", "see ", "(", '"', "url="])
+    right = rng.choice(["", ".", ")", ",", ";", " failed", '"'])
+    text = f"{left}{rng.choice(['http', 'https'])}://{userinfo}{host}{path}{query}{fragment}{right}"
+    return {
+        "block": "focus:url",
+        "bucket": "url",
+        "kinds": kinds,
+        "f": None,
+        "t": text,
+        "value": " ".join(secrets),
+        # one piece per secret: the longest run of an encoded one
+        "pieces": [max(PIECE_RE.findall(v) or [v], key=len) for v in secrets],
+    }
+
+
+def _text_row(rng: random.Random, focus: str | None, block: str) -> Row:
+    pre_k, pre = _sample_pre(rng, focus == "pre")
+    qual_k, qual = _sample_qual(rng, focus == "qual")
+    name_k = "case" if focus == "name" else "lower"
+    name = rng.choice(KEYS)
+    if focus == "name" and rng.random() < 0.3:
+        name = "code"
+        name_k = rng.choice(["code-qualified", "code-diagnostic", "code-credential"])
+        if name_k == "code-diagnostic":
+            word = rng.choice(sorted(DIAGNOSTIC_CODE_QUALIFIERS))
+        elif name_k == "code-credential":
+            word = rng.choice(CREDENTIAL_CODE_QUALIFIERS)
+        else:
+            word = ident_run(rng, 2, 10, False)
+        qual = recase(rng, word, rng.choice(["lower", "upper", "title"])) + rng.choice(
+            "_-"
+        )
+    if focus == "name" and name_k == "case":
+        name = recase(rng, name)
+    suf_k, suffix = _sample_suffix(rng, focus == "suffix")
+    sep_k, sep = _sample_sep(rng, focus == "sep")
+    val_k, value = _sample_value(rng, focus == "value")
+    wrap_k, left, right = _sample_wrap(rng, focus == "wrap")
+    kinds = {
+        "wrap": wrap_k,
+        "pre": pre_k,
+        "qual": qual_k,
+        "name": name_k,
+        "suffix": suf_k,
+        "sep": sep_k,
+        "value": val_k,
+    }
+    f = {
+        "pre": pre,
+        "qual": qual,
+        "name": name,
+        "suffix": suffix,
+        "sep": sep,
+        "value": value,
+        "left": left,
+        "right": right,
+    }
+    bucket = f"{focus}:{kinds[focus]}" if focus in kinds else block
+    return {"block": block, "bucket": bucket, "f": f, "t": _text(f), "value": value}
+
+
+def _mix_row(rng: random.Random) -> Row:
+    row = _text_row(rng, None, "mix")
+    for focus in FOCI:
+        other = _text_row(rng, focus, "mix")["f"]
+        for part in ("left", "right") if focus == "wrap" else (focus,):
+            row["f"][part] = other[part]
+    row["t"] = _text(row["f"])
+    row["value"] = row["f"]["value"]
+    row["bucket"] = "mix"
+    return row
+
+
+def _bearer_row(rng: random.Random) -> Row:
+    pre = rng.choice(list(BEARER_PRE) + [rng.choice(ASCII_PUNCT + NON_ASCII + SPACES)])
+    ws = "".join(rng.choice(SPACES) for _ in range(rng.randint(1, 3)))
+    v = cred(rng)
+    wrap = rng.choice(["", "", "", '""', "''", "()", "[]", "{}", "<>"])
+    val = (wrap[0] + v + wrap[1]) if wrap else v
+    if pre.rstrip().endswith((":", "=")):
+        kind = "after-:/="
+    elif any(c in "\r\n" for c in ws):
+        kind = "line-break"
+    else:
+        kind = "wrapped-value" if wrap else "plain-context"
+    f = {
+        "pre": pre,
+        "qual": "",
+        "name": recase(rng, "bearer"),
+        "suffix": "",
+        "sep": ws,
+        "value": val,
+    }
+    return {
+        "block": "focus:bearer",
+        "bucket": f"bearer:{kind}",
+        "f": f,
+        "t": _text(f),
+        "value": v,
+    }
+
+
+def _authz_row(rng: random.Random) -> Row:
+    pre = rng.choice(["", "", "Proxy-", "x", "(", '"', "X-"])
+    ws1 = "".join(rng.choice(SPACES) for _ in range(rng.choice([0, 0, 1, 2])))
+    ws2 = "".join(rng.choice(SPACES) for _ in range(rng.choice([0, 1, 1, 2])))
+    scheme = rng.choice(
+        ["", "", "Bearer ", "bearer\t", "Basic ", "Token ", "Bearer\n\n"]
+    )
+    v = cred(rng) if rng.random() < 0.7 else rng.choice(PLAIN_WORDS)
+    wrap = rng.choice(["", "", "", '""', "''", "()", "[]", "{}"])
+    val = (wrap[0] + v + wrap[1]) if wrap else v
+    sep = ws1 + rng.choice(":=") + ws2
+    if any(c in "\r\n" for c in sep + scheme):
+        kind = "line-break"
+    else:
+        kind = "wrapped-value" if wrap else "scheme" if scheme else "plain"
+    f = {
+        "pre": pre,
+        "qual": "",
+        "name": recase(rng, "authorization"),
+        "suffix": "",
+        "sep": sep + scheme,
+        "value": val,
+    }
+    return {
+        "block": "focus:authz",
+        "bucket": f"authorization:{kind}",
+        "f": f,
+        "t": _text(f),
+        "value": v,
+    }
+
+
+def _fixed_row(block: str, bucket: str, value: str, **parts: str) -> Row:
+    f = {
+        "pre": "",
+        "qual": "",
+        "name": "password",
+        "suffix": "",
+        "sep": "=",
+        "value": value,
+    }
+    f.update(parts)
+    return {"block": block, "bucket": bucket, "f": f, "t": _text(f), "value": value}
+
+
+def _sep_rows(rng: random.Random, seps: list[str], keys: tuple[str, ...]) -> list[Row]:
+    return [
+        _fixed_row("sep", f"sep:{sep_kind(sep)}", v, name=k, sep=sep)
+        for sep in seps
+        for k in keys
+        for v in (cred(rng), rng.choice(PLAIN_WORDS))
+    ]
+
+
+def _pre_rows(
+    rng: random.Random, leads: tuple[str, ...], keys: tuple[str, ...]
+) -> list[Row]:
+    return [
+        _fixed_row("pre", f"pre:{cls}", v, pre=lead + ch, name=k, sep=sep)
+        for cls, chars in PRE_CHARS.items()
+        for ch in chars
+        for lead in (("",) if cls == "resource" else leads)
+        for k in keys
+        for sep in ("=", ": ", " ")
+        for v in (cred(rng), rng.choice(PLAIN_WORDS))
+    ]
+
+
+def _code_rows(rng: random.Random) -> list[Row]:
+    """`code` under every diagnostic and every credential qualifier, with a
+    credential and a plain value, after `=` and `: `."""
+    return [
+        _fixed_row("code", f"code:{kind}", v, qual=word + "_", name="code", sep=sep)
+        for kind, words in (
+            ("diagnostic", sorted(DIAGNOSTIC_CODE_QUALIFIERS)),
+            ("credential", CREDENTIAL_CODE_QUALIFIERS),
+        )
+        for word in words
+        for sep in ("=", ": ")
+        for v in (cred(rng), rng.choice(PLAIN_WORDS))
+    ]
+
+
+def _scalar_rows() -> list[Row]:
+    rows = []
+    for sname, s in SCALARS.items():
+        for k in SCALAR_KEYS:
+            for shape in ("top", "nested", "list"):
+                if shape == "top":
+                    obj: object = {k: s}
+                elif shape == "nested":
+                    obj = {"a": 1, "x": {k: s, "n": 2}}
+                else:
+                    obj = {"items": [{"id": 1, k: s}, {"id": 2}]}
+                rows.append(
+                    {
+                        "block": "scalar",
+                        "bucket": f"scalar:{sname}",
+                        "f": None,
+                        "o": obj,
+                        "value": None,
+                    }
+                )
+    return rows
+
+
+def _focus_rows(rng: random.Random, per_axis: int) -> list[Row]:
+    rows = []
+    for focus in FOCI:
+        rows += [_text_row(rng, focus, f"focus:{focus}") for _ in range(per_axis)]
+    rows += [_bearer_row(rng) for _ in range(per_axis)]
+    rows += [_authz_row(rng) for _ in range(per_axis)]
+    rows += [_url_row(rng) for _ in range(per_axis)]
+    return rows
+
+
+PAIRS = list(SEP_ALPHABET) + [a + b for a in SEP_ALPHABET for b in SEP_ALPHABET]
+BLOCKS = (
+    ("sep", "pre", "code")
+    + tuple(f"focus:{f}" for f in (*FOCI, "bearer", "authz", "url"))
+    + ("mix", "scalar")
+)
+
+
+def corpus(tier: int) -> list[Row]:
+    """Tier 1 (the default suite), or tier 2: tier 1 followed by the rest."""
+    rng = random.Random(2026_09_26)
+    rows = _sep_rows(rng, PAIRS, ("password", "token"))
+    rows += _pre_rows(rng, ("",), ("password", "token"))
+    rows += _code_rows(rng)
+    rows += _focus_rows(rng, 500)
+    rows += [_mix_row(rng) for _ in range(1000)]
+    rows += _scalar_rows()
+    if tier == 1:
+        return rows
+    rng = random.Random(2026_09_27)
+    triples = ["".join(rng.choice(SEP_ALPHABET) for _ in range(3)) for _ in range(3000)]
+    rows += _sep_rows(rng, PAIRS, ("secret", "api_key", "session"))
+    rows += _sep_rows(rng, triples, EXHAUSTIVE_KEYS)
+    rows += _pre_rows(rng, ("",), ("secret", "api_key", "session"))
+    rows += _pre_rows(rng, ("abc",), EXHAUSTIVE_KEYS)
+    rows += _focus_rows(rng, 9500)
+    rows += [_mix_row(rng) for _ in range(19000)]
+    return rows
+
+
+def fingerprint(rows: list[Row]) -> str:
+    """A digest of the rows as written: the fixture records the digest of the
+    corpus it was recorded over, so a corpus change without a new fixture
+    fails instead of comparing rows against another row's oracle."""
+    digest = hashlib.sha256()
+    for row in rows:
+        digest.update(repr(row.get("t", row.get("o"))).encode("utf-8", "surrogatepass"))
+        digest.update(b"\0")
+    return digest.hexdigest()
+
+
+# ------------------------------------------------------------ observation
+
+#: The pieces of a value that count as the secret: main's value class, 3+.
+PIECE_RE = re.compile(r"[A-Za-z0-9_.+/~=-]{3,}")
+TEXT_SURFACES = (
+    "E",
+    "P",
+    "EjAC",
+    "EjAI",
+    "EjUC",
+    "EjUI",
+    "PjAC",
+    "PjAI",
+    "PjUC",
+    "PjUI",
+    "POs",
+    "POd",
+)
+SERIALISED = frozenset(TEXT_SURFACES[2:10] + ("POd", "POo"))
+_DIGITS32 = "0123456789abcdefghijklmnopqrstuv"
+_SPELLINGS = (
+    ("AC", True, None),
+    ("AI", True, 2),
+    ("UC", False, None),
+    ("UI", False, 2),
+)
+BIG = 10**7
+
+
+def pieces(row: Row) -> list[str]:
+    if "pieces" in row:
+        return list(row["pieces"])
+    return PIECE_RE.findall(row["value"]) if row["value"] else []
+
+
+def observe(
+    row: Row,
+    engine: Callable[[str], str],
+    redact_secrets: Callable[[str], str],
+    process_output: Callable[[object], object],
+) -> str:
+    """What survives on each surface, as one base-32 digit per surface (bit i:
+    piece i is still in the output, as written or percent-decoded -- main's
+    URL rule re-encodes what it keeps, which is not a removal) and a final type letter for the dict
+    path (`d` dict, `s` str). A piece's spelling is the same on every surface:
+    nothing in `PIECE_RE`'s alphabet is escaped by `json.dumps`."""
+    ps = pieces(row)
+    assert len(ps) <= 5, ps
+
+    def mask(out: str) -> str:
+        decoded = unquote(out)
+        return _DIGITS32[
+            sum(
+                1 << i
+                for i, p in enumerate(ps)
+                # a query value's `+` is a space once decoded
+                if p in out or p in decoded or p.replace("+", " ") in decoded
+            )
+        ]
+
+    def po(obj: object) -> tuple[str, str]:
+        r = process_output(obj)
+        if isinstance(r, str):
+            return "s", r
+        return ("d" if isinstance(r, dict) else "o"), json.dumps(r, indent=2)
+
+    if "o" in row:
+        kind, out = po(row["o"])
+        return mask(out) + kind
+    t = row["t"]
+    outs = [engine(t), redact_secrets(t)]
+    dumped = [json.dumps({"t": t}, ensure_ascii=a, indent=i) for _, a, i in _SPELLINGS]
+    outs += [engine(d) for d in dumped] + [redact_secrets(d) for d in dumped]
+    outs.append(po(t)[1])
+    kind, out = po({"t": t})
+    outs.append(out)
+    return "".join(mask(o) for o in outs) + kind
+
+
+def worse_surfaces(row: Row, main: str, here: str) -> list[str]:
+    """The surfaces where `here` keeps a piece `main` removed, and `POd.type`
+    / `POo.type` where main's result was a dict and this one's is not."""
+    names = ("POo",) if "o" in row else TEXT_SURFACES
+    worse = [s for s, m, h in zip(names, main, here) if int(h, 32) & ~int(m, 32)]
+    if main[-1] == "d" and here[-1] != "d":
+        worse.append(names[-1] + ".type")
+    return worse
+
+
+def removed_by_main(row: Row, main: str) -> bool:
+    """The positive control: main removed some piece on some surface."""
+    full = (1 << len(pieces(row))) - 1
+    return any(int(m, 32) != full for m in main[:-1])
+
+
+# ---------------------------------------------------- the accepted classes
+
+
+def _plain(value: str) -> bool:
+    v = value.strip("\"'").rstrip(".:!?")
+    return bool(
+        re.fullmatch(r"[A-Za-z_]+", v)
+        or re.fullmatch(r"[+-]?[0-9]+|0[xX][0-9a-fA-F]+", v)
+    )
+
+
+def credential(value: str) -> bool:
+    """D2, restated: not a plain word or number, and digit-bearing and 6+
+    characters or punctuated and 8+."""
+    value = value.strip("\"'")
+    if _plain(value):
+        return False
+    return len(value) >= (6 if any(c.isdigit() for c in value) else 8)
+
+
+_DECLARED = frozenset(
+    {
+        "access_token",
+        "api_key",
+        "apikey",
+        "api-key",
+        "assertion",
+        "auth",
+        "aws_access",
+        "aws_secret",
+        "client_secret",
+        "code",
+        "cookie",
+        "credential",
+        "credentials",
+        "id_token",
+        "jwt",
+        "passwd",
+        "password",
+        "private_key",
+        "privatekey",
+        "private-key",
+        "pwd",
+        "refresh_token",
+        "saml",
+        "secret",
+        "secret_access_key",
+        "session",
+        "set-cookie",
+        "sid",
+        "tenant-id",
+        "tenant_id",
+        "token",
+    }
+)
+_WEAK = frozenset({"code", "auth", "credential", "credentials"})
+
+
+def _declared(name: str, suffix: str) -> bool:
+    if (name + suffix).lower() in _DECLARED:
+        return True
+    return (
+        bool(re.fullmatch(r"[_-]?(?:id|key)|s", suffix.lower()))
+        and name.lower() in _DECLARED
+    )
+
+
+def _single_case(word: str) -> bool:
+    word = word.lstrip("-_")
+    return (
+        word.isupper() or word.islower() or (word[:1].isupper() and word[1:].islower())
+    )
+
+
+def _acronym_title(word: str) -> bool:
+    return re.fullmatch(r"[A-Z0-9]{1,8}[A-Z][a-z]+", word.lstrip("-_")) is not None
+
+
+def _inner_key(name: str, suffix: str) -> bool:
+    """The key as written holds another secret-key word that starts inside the
+    written name and ends inside the suffix (`aws_access` + `id` holds
+    `sid`): main's containing match read that inner key."""
+    word = (name + suffix).lower()
+    for key in KEYS:
+        start = word.find(key, 1)
+        while start != -1:
+            if start < len(name) < start + len(key):
+                return True
+            start = word.find(key, start + 1)
+    return False
+
+
+#: The stated classes (Consiliency/pmcp#234 rev 10: the audit's accept list
+#: and the maintainer's four decisions). Each is decided from the row as
+#: written -- `pre`, `qual`, `name`, `suffix`, `sep`, `value` -- and the
+#: surface it is read on (a serialisation is a wrap), never from any output.
+CLASSES = {
+    "N3": "a glued prefix or a suffix of more than 24 alphanumerics: an identifier, not a key (a cost bound)",
+    "N10": "a key inside an `arn:`/`urn:` resource name names a resource",
+    "C3a": "`code` never fires on a whitespace-only separator",
+    "C3": "a whitespace-only separator with a non-credential-shaped value (D2)",
+    "N11": "a `name=value` token after a keyword and whitespace is its own pair",
+    "N4": "a glued mixed-case key before whitespace is an identifier, not a key (`Ed25519PrivateKey X509Cert`, `gby3zPassword x`)",
+    "N4c": "the key as written holds another key word starting inside the written name (`aws_accessid` holds `sid`)",
+    "C4": "an operator run holding `==` or `::` with a non-credential-shaped value is a comparison or a path",
+    "C5": "a suffixed key with a non-credential-shaped value names metadata",
+    "C6": "a glued key (no joiner before the name, any case) counts only with a credential-shaped value that is not a URL or ARN",
+    "C7": "after an unquoted key a value starting on `,`/`}` is JSON structure",
+    "C8": "a separator whose last line break is unindented, with a non-credential-shaped unquoted value, ends a sentence",
+    "N6b": "a quoted value on a serialised surface is JSON inside a leaf (Consiliency/pmcp#290)",
+    "C10": "a weak key keeps a plain word or number; `code` under a non-OAuth qualifier keeps any non-credential-shaped value (N8's gate)",
+    "C11": "`Authorization`/`Bearer` followed by a plain word, bare or wrapped in quotes or brackets, is prose",
+    "C12": "a `code` key qualified by a diagnostic or descriptive word (`DIAGNOSTIC_CODE_QUALIFIERS`, the qualifier's last segment) keeps its value (`error_code=E_TIMEOUT_42`, `sqlstate_code=42P01`)",
+}
+
+
+def accepted(f: dict[str, str], surface: str) -> str | None:
+    """The accepted class of a (row, surface) this redactor is worse on than
+    main, or None: a defect."""
+    pre, qual, name, suffix = f["pre"], f["qual"], f["name"], f["suffix"]
+    pre = f.get("left", "") + pre  # the wrap's text before the pair
+    glue_m = re.search(r"[A-Za-z0-9]*$", pre)
+    full_qual = (glue_m.group(0) if glue_m else "") + qual
+    sep, value = f["sep"], f["value"]
+    lead_m = re.match(r"[:=]*", value)
+    lead = lead_m.group(0) if lead_m else ""
+    sep, value = sep + lead, value[len(lead) :] or value
+    lname = name.lower()
+    cred = credential(value)
+    glued_run = re.search(r"[A-Za-z0-9]*$", full_qual)
+    glued = glued_run.group(0) if glued_run else ""
+    key = glued + name + suffix
+    if len(glued) > 24 or sum(c.isalnum() for c in suffix) > 24:
+        return "N3"
+    if re.search(r"\b[au]rn:[^\s\"'<>]*$", pre, re.IGNORECASE):
+        return "N10"
+    if lname == "code" and re.split(r"[_-]", full_qual.strip("_-").lower())[-1] in (
+        DIAGNOSTIC_CODE_QUALIFIERS
+    ):
+        return "C12"
+    if sep.isspace():
+        if lname == "code":
+            return "C3a"
+        if not cred:
+            return "C3"
+        # the value as written runs on into the wrap (`x=1`, `=tail`)
+        if re.match(r"[A-Za-z_-]+=[^=]", value + f.get("right", "")):
+            return "N11"
+        if glued and not _single_case(key) and not _acronym_title(key):
+            return "N4"
+    if ("==" in sep or "::" in sep) and not cred:
+        return "C4"
+    if suffix and not _declared(name, suffix) and not cred:
+        return "C5"
+    if glued and (not cred or "://" in value or value.lower().startswith("arn:")):
+        return "C6"
+    if not sep.startswith('"') and value.startswith((",", "}")):
+        return "C7"
+    if (
+        re.search(r"[\r\n][^ \t\xa0]*$", sep)
+        and not value.startswith(('"', "'"))
+        and not cred
+    ):
+        return "C8"
+    if surface in SERIALISED and re.match(
+        r"(?:(?:bearer|basic|digest|negotiate|ntlm|token)\s+)?\"", value, re.IGNORECASE
+    ):
+        return "N6b"
+    if surface in SERIALISED and '\\"' in sep:
+        return "N6b"
+    if lname in _WEAK and (not suffix or _declared(name, suffix)):
+        if _plain(value):
+            return "C10"
+        qualifier = full_qual.strip("_-").lower()
+        if lname == "code" and qualifier not in _OAUTH_QUALIFIERS and not cred:
+            return "C10"
+    if lname in ("authorization", "bearer") and _plain(_unwrap(value)):
+        return "C11"
+    if _inner_key(name, suffix):
+        return "N4c"
+    return None
+
+
+_OAUTH_QUALIFIERS = frozenset({"", "auth", "authorization", "oauth", "device", "user"})
+
+
+def _unwrap(value: str) -> str:
+    if len(value) >= 2 and value[0] + value[-1] in ('""', "''", "()", "[]", "{}", "<>"):
+        return value[1:-1]
+    return value
diff --git a/tests/_redaction_shapes.py b/tests/_redaction_shapes.py
new file mode 100644
index 0000000000000000000000000000000000000000..7723f649e327fc754f90c00f949e2fdf078d8620
--- /dev/null
+++ b/tests/_redaction_shapes.py
@@ -0,0 +1,377 @@
+"""Adversarial input shapes derived from the redactor's own regular
+expressions (Consiliency/pmcp#234).
+
+A super-linear path in a regex or a scan needs an input the path can consume
+over and over and then fail on. So each pattern is parsed (`re._parser`) and
+every piece a quantifier can repeat -- a literal run, a representative of
+each character class, each key word of an alternation -- becomes a unit;
+each unit is repeated to the target length, with each of a few failing tails
+and after each of the redactor's trigger words. The timing sweep in
+`tests/test_redaction_floor.py` runs every shape through every public entry
+point at growing sizes and asserts the growth is linear.
+
+Stdlib only.
+"""
+
+from __future__ import annotations
+
+import re
+from collections.abc import Callable, Iterable
+
+try:  # Python 3.11+
+    import re._parser as sre_parse  # type: ignore[import-not-found]
+    from re import _constants as sre_constants  # type: ignore[attr-defined]
+except ImportError:  # Python 3.10
+    import sre_constants  # type: ignore[no-redef]
+    import sre_parse  # type: ignore[no-redef]
+
+#: Characters after a repeated unit that make a match fail late: a letter, a
+#: space, a line break, an operator, each quote and closing bracket.
+TAILS = ("", "a", " ", "\n", "=", '"', "'", ")", "\\", " abc", "=abc123")
+#: What precedes the run: nothing, or a word that arms one of the rules.
+LEADS = (
+    "",
+    "x ",
+    "token",
+    "password=",
+    "Bearer x",
+    "x Bearer",
+    "Authorization: x",
+    "arn:",
+    "https://h/?",
+    "code ",
+)
+
+_CATEGORY_CHARS = {
+    sre_constants.CATEGORY_SPACE: " \n",
+    sre_constants.CATEGORY_NOT_SPACE: "a",
+    sre_constants.CATEGORY_DIGIT: "1",
+    sre_constants.CATEGORY_NOT_DIGIT: "a",
+    sre_constants.CATEGORY_WORD: "a_",
+    sre_constants.CATEGORY_NOT_WORD: " -",
+}
+_FALLBACK = "a1-_ :=\n\"')]}.,;/\\&"
+
+
+def _class_chars(items: list[tuple[object, object]]) -> str:
+    chars = ""
+    negate = False
+    members: set[str] = set()
+    for op, arg in items:
+        if op is sre_constants.NEGATE:
+            negate = True
+        elif op is sre_constants.LITERAL:
+            members.add(chr(arg))  # type: ignore[arg-type]
+        elif op is sre_constants.RANGE:
+            lo, hi = arg  # type: ignore[misc]
+            members.update({chr(lo), chr(hi)})
+        elif op is sre_constants.CATEGORY:
+            members.update(_CATEGORY_CHARS.get(arg, ""))  # type: ignore[arg-type]
+    if negate:
+        excluded = members
+        chars = "".join(c for c in _FALLBACK if c not in excluded)[:3]
+    else:
+        chars = "".join(sorted(members))[:4]
+    return chars
+
+
+def units(pattern: str, flags: int = 0) -> set[str]:
+    """The repeatable pieces of ``pattern``: literal runs, one to four
+    representatives of each class, each alternative of an alternation."""
+    found: set[str] = set()
+
+    def walk(tree: Iterable[tuple[object, object]]) -> str:
+        """Returns the literal text the subtree always starts with."""
+        literal = ""
+        for op, arg in tree:
+            if op is sre_constants.LITERAL:
+                literal += chr(arg)  # type: ignore[arg-type]
+                continue
+            if literal:
+                found.add(literal)
+                literal = ""
+            if op is sre_constants.IN:
+                found.update(_class_chars(arg))  # type: ignore[arg-type]
+            elif op is sre_constants.CATEGORY:
+                found.update(_CATEGORY_CHARS.get(arg, ""))  # type: ignore[arg-type]
+            elif op in (sre_constants.MAX_REPEAT, sre_constants.MIN_REPEAT):
+                walk(arg[2])  # type: ignore[index]
+            elif op is sre_constants.SUBPATTERN:
+                walk(arg[-1])  # type: ignore[index]
+            elif op is sre_constants.BRANCH:
+                for branch in arg[1]:  # type: ignore[index]
+                    walk(branch)
+            elif op in (sre_constants.ASSERT, sre_constants.ASSERT_NOT):
+                walk(arg[1])  # type: ignore[index]
+        if literal:
+            found.add(literal)
+        return literal
+
+    walk(sre_parse.parse(pattern, flags))
+    return {u for u in found if u}
+
+
+def shapes(patterns: Iterable[re.Pattern[str]]) -> dict[str, Callable[[int], str]]:
+    """Every shape, keyed by a readable name: ``lead + unit * k + tail``,
+    sized to ``n`` characters."""
+    all_units: set[str] = set()
+    for pattern in patterns:
+        all_units |= units(pattern.pattern, pattern.flags)
+    out: dict[str, Callable[[int], str]] = {}
+    for unit in sorted(all_units):
+        for lead in LEADS:
+            for tail in TAILS:
+                name = f"{lead!r}+{unit!r}*k+{tail!r}"
+
+                def make(
+                    n: int, lead: str = lead, unit: str = unit, tail: str = tail
+                ) -> str:
+                    return (
+                        lead
+                        + unit * max(1, (n - len(lead) - len(tail)) // len(unit))
+                        + tail
+                    )
+
+                out[name] = make
+    # a key word glued to a separator or joiner (`secret:secret:...`,
+    # `token-token-...`): a key and a separator at every step
+    words = sorted(u for u in all_units if len(u) >= 3 and u.isalpha())
+    for word in words:
+        for glue in (":", "-", "=", "_", " ", "/"):
+            for lead in ("", "arn:", "x "):
+                for tail in ("", "=abc123", " abc"):
+                    name = f"{lead!r}+{word + glue!r}*k+{tail!r}"
+
+                    def make3(
+                        n: int,
+                        lead: str = lead,
+                        unit: str = word + glue,
+                        tail: str = tail,
+                    ) -> str:
+                        return (
+                            lead
+                            + unit * max(1, (n - len(lead) - len(tail)) // len(unit))
+                            + tail
+                        )
+
+                    out[name] = make3
+    # two-unit alternations (`a-`, `=:`, `\"`): a word boundary or a
+    # backtracking point at every step
+    singles = sorted(u for u in all_units if len(u) == 1)
+    for a in singles:
+        for b in singles:
+            if a == b:
+                continue
+            for lead in ("", "x ", "token", "Bearer x"):
+                for tail in ("", "a", " "):
+                    name = f"{lead!r}+{a + b!r}*k+{tail!r}"
+
+                    def make2(
+                        n: int, lead: str = lead, unit: str = a + b, tail: str = tail
+                    ) -> str:
+                        return (
+                            lead
+                            + unit * max(1, (n - len(lead) - len(tail)) // 2)
+                            + tail
+                        )
+
+                    out[name] = make2
+    return out
+
+
+def redactor_patterns() -> list[re.Pattern[str]]:
+    """Every compiled pattern the redactor's modules hold."""
+    import pmcp.auth
+    import pmcp.keyword_matcher
+    import pmcp.policy.policy
+    import pmcp.redaction_floor
+
+    found: list[re.Pattern[str]] = []
+    for module in (
+        pmcp.redaction_floor,
+        pmcp.keyword_matcher,
+        pmcp.auth,
+        pmcp.policy.policy,
+    ):
+        for value in vars(module).values():
+            candidates = (
+                value
+                if isinstance(value, (tuple, list))
+                else value.values()
+                if isinstance(value, dict)
+                else [value]
+            )
+            found.extend(c for c in candidates if isinstance(c, re.Pattern))
+    found.extend(
+        re.compile(p, re.IGNORECASE)
+        for p in pmcp.policy.policy.DEFAULT_REDACTION_PATTERNS
+    )
+    unique = {(p.pattern, p.flags): p for p in found if isinstance(p.pattern, str)}
+    return list(unique.values())
+
+
+# ------------------------------------------------------------ compositions
+#
+# The shapes above come from the regular expressions alone, one lead at a
+# time. The replay and the additive rules also have Python loops whose cost
+# depends on how passes COMPOSE: a match of one pass that spans many
+# markers left by an earlier one (a Bearer value over a URL's rewritten
+# pairs), a lookup per match over structures built by another pass (every
+# keyword match asking every resource name), a scan per span over text
+# other spans share (the backslash run before every escape). So the second
+# family is built from the loop structure: two leads that arm different
+# passes, a repeated unit that makes one pass leave many markers or ranges
+# for the other, units interleaved, and each composition nested inside a
+# JSON string and after a header.
+
+CONTEXTS = (
+    "",
+    "Authorization: ",
+    "Bearer ",
+    "password=",
+    "token ",
+    "https://h/?",
+    "https://h/?password=x",
+    "Authorization: https://h/?q",
+    "Bearer https://h/?password=x",
+    "see https://h/?password=x",
+    "arn:x ",
+    "x=urn:a:b&",
+    '{"t": "',
+    "code ",
+    "\\",
+)
+#: Units that make one pass leave many markers, ranges or spans behind.
+LOOP_UNITS = (
+    "&a+b",
+    "password=x&",
+    "&a",
+    "a=%41&",
+    "&token=%2541",
+    "token=x ",
+    "arn:x ",
+    "arn:x:secret=abc123 ",
+    "password=abc123 ",
+    'tokens: ["a1b2c3d4"] ',
+    "[REDACTED]",
+    "\\u00e9",
+    '\\"',
+    "\\\\",
+    "Bearer x ",
+    "Authorization: x ",
+    "secret:",
+    "a-",
+    "=:",
+    ")",
+    "\n",
+    "%25",
+    "ghp_abcdefghij1234 ",
+    "https://u:p@h/?a=1 ",
+)
+COMPOSITION_TAILS = ("", " end", '"')
+
+
+def compositions() -> dict[str, Callable[[int], str]]:
+    """Two leads + a repeated unit (or two interleaved) + a tail, and each
+    nested in a JSON string."""
+    import json
+
+    out: dict[str, Callable[[int], str]] = {}
+
+    def add(name: str, lead: str, unit: str, tail: str, nest: bool) -> None:
+        def make(n: int, lead: str = lead, unit: str = unit, tail: str = tail) -> str:
+            text = lead + unit * max(1, (n - len(lead) - len(tail)) // len(unit)) + tail
+            return json.dumps({"t": text}) if nest else text
+
+        out[name] = make
+
+    for first in CONTEXTS:
+        for second in CONTEXTS:
+            for unit in LOOP_UNITS:
+                for tail in COMPOSITION_TAILS:
+                    lead = first + second
+                    add(f"{lead!r}+{unit!r}*k+{tail!r}", lead, unit, tail, False)
+    for lead in CONTEXTS:
+        for a in LOOP_UNITS:
+            for b in LOOP_UNITS:
+                if a != b:
+                    add(f"{lead!r}+{a + b!r}*k", lead, a + b, "", False)
+    for lead in CONTEXTS:
+        for unit in LOOP_UNITS:
+            add(f"json({lead!r}+{unit!r}*k)", lead, unit, "", True)
+            add(
+                f"hdr+json({lead!r}+{unit!r}*k)",
+                "Authorization: " + lead,
+                unit,
+                "",
+                True,
+            )
+    return out
+
+
+# ------------------------------------------------ inside one atomised piece
+#
+# The replay turns some input into ONE piece that stands for all of it: a
+# URL query key or value that changes when it is re-encoded (`+`, `%xx`), a
+# host that is re-cased, a marker standing for what it replaced, the whole
+# URL when its alignment falls back. A match INSIDE such a piece spans the
+# whole piece, so the third family repeats each match-producing unit inside
+# each context that makes a piece (B-5 of rev 13's board: every unit of the
+# compositions above held a space or `&`, which ends a URL component).
+
+ATOM_CONTEXTS = (
+    "https://h/?q=+",
+    "https://h/?q=%41",
+    "https://h/?+",
+    "https://h/?%41",
+    "https://H.EXAMPLE/",
+    "https://h:99999/",
+    "https://u:p@h/?token=x&q=+",
+    "Authorization: https://h/?q=+",
+    "Bearer https://h/?q=%41",
+    '{"t": "https://h/?q=+',
+    "password=",
+    "Authorization: ",
+    "Bearer ",
+    '{"t": "',
+)
+#: Units that each produce a match of some pass and survive re-encoding
+#: (letters, digits, `_ . - ~ /`), and a few that re-encoding changes.
+MATCH_UNITS = (
+    "aaaaaaaaaa.bbbbbbbbbb.cccccccccc/",
+    "sk-abcdef/",
+    "ghp_abcdefghij/",
+    "github_pat_abcdefghij/",
+    "AKIAABCDEFGHIJKLMNOP/",
+    "xoxb-1234567890/",
+    "glpat-abcdefgh12345678/",
+    "Ab3dE6gH9jK2mN5pQ8/",
+    "token/",
+    "bearer/",
+    "token=abc123/",
+    "password:x1/",
+    "Bearer%20x/",
+    "api_key=x/",
+    "[REDACTED]/",
+    "%41%42/",
+    "+x+/",
+)
+
+
+def atom_repeats() -> dict[str, Callable[[int], str]]:
+    out: dict[str, Callable[[int], str]] = {}
+    for context in ATOM_CONTEXTS:
+        for unit in MATCH_UNITS:
+            for tail in ("", " end"):
+
+                def make(
+                    n: int, lead: str = context, unit: str = unit, tail: str = tail
+                ) -> str:
+                    return (
+                        lead
+                        + unit * max(1, (n - len(lead) - len(tail)) // len(unit))
+                        + tail
+                    )
+
+                out[f"atom({context!r})+{unit!r}*k+{tail!r}"] = make
+    return out
diff --git a/tests/fixtures/regen_redaction_main_oracle.py b/tests/fixtures/regen_redaction_main_oracle.py
new file mode 100644
index 0000000000000000000000000000000000000000..8c761da34fafaefcc584f9c73c47476febecf8ac
--- /dev/null
+++ b/tests/fixtures/regen_redaction_main_oracle.py
@@ -0,0 +1,126 @@
+"""Record `main`'s oracle over the redaction differential corpora
+(Consiliency/pmcp#234). Run it from this checkout against a MAIN tree:
+
+  PYTHONPATH=<main checkout>/src python tests/fixtures/regen_redaction_main_oracle.py \
+      <main checkout>/src tests/fixtures/redaction_main_oracle.b64
+
+then copy the output to `.consiliency/plans/detailed-234-redactor-main-oracle.b64`
+(the two stay `cmp`-identical). Not collected by pytest.
+
+Offline and deterministic (seeded corpora, gzip mtime=0, sorted keys, base64
+wrapped at 76). Keys:
+  grammar[i]     tests/_redaction_grammar.py's observe() code for row i of
+                 corpus(2) (tier 1 is its prefix);
+  grammar_fingerprint  {"1": ..., "2": ...}: fingerprint() of each tier;
+  dict[i]        pieces main's process_output removed from the serialised
+                 result of _dict_corpus() row i;
+  dict_types[i]  / fuzz_types[i]: type name of process_output(obj)['result']
+                 for the dict and JSON-fuzz corpora.
+"""
+
+import ast
+import base64
+import gzip
+import importlib.util
+import io
+import json
+import multiprocessing
+import random
+import re
+import string
+import sys
+import uuid
+from pathlib import Path
+
+import pmcp
+from pmcp.auth import sanitize_auth_diagnostic
+from pmcp.policy.policy import PolicyManager
+
+MAIN_SRC = str(Path(sys.argv[1]).absolute())
+assert pmcp.__file__.startswith(MAIN_SRC), (pmcp.__file__, MAIN_SRC)
+TESTS = Path(__file__).resolve().parents[1]
+spec = importlib.util.spec_from_file_location(
+    "_redaction_grammar", TESTS / "_redaction_grammar.py"
+)
+G = importlib.util.module_from_spec(spec)
+assert spec is not None and spec.loader is not None
+spec.loader.exec_module(G)
+
+tree = ast.parse((TESTS / "test_redaction.py").read_text())
+FUNCS = {"_dict_corpus", "_json_fuzz_corpus"}
+
+
+def keep(n):
+    if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
+        t = n.targets[0] if isinstance(n, ast.Assign) else n.target
+        return isinstance(t, ast.Name) and t.id.startswith("_DIFF_")
+    return isinstance(n, ast.FunctionDef) and n.name in FUNCS
+
+
+ns = {"random": random, "json": json, "re": re, "string": string, "uuid": uuid}
+body = [n for n in tree.body if keep(n)]
+assert {n.name for n in body if isinstance(n, ast.FunctionDef)} == FUNCS
+exec(compile(ast.Module(body=body, type_ignores=[]), "c", "exec"), ns)
+TOK = ns["_DIFF_TOKEN"]
+pm = PolicyManager()
+ROWS = G.corpus(2)
+
+
+def _po(obj):
+    return pm.process_output(obj, redact=True, max_bytes=G.BIG)["result"]
+
+
+def observe(i):
+    return G.observe(
+        ROWS[i],
+        lambda t: sanitize_auth_diagnostic(t, max_length=None),
+        pm.redact_secrets,
+        _po,
+    )
+
+
+def rem(a, b):
+    return sorted(set(TOK.findall(a)) - set(TOK.findall(b)))
+
+
+if __name__ == "__main__":
+    with multiprocessing.get_context("fork").Pool(20) as pool:
+        grammar = pool.map(observe, range(len(ROWS)), chunksize=500)
+    out = {
+        "grammar": grammar,
+        "grammar_fingerprint": {
+            "1": G.fingerprint(G.corpus(1)),
+            "2": G.fingerprint(ROWS),
+        },
+        "dict": [],
+        "dict_types": [],
+        "fuzz_types": [],
+    }
+    for obj, *_ in ns["_dict_corpus"]():
+        r = pm.process_output(obj, redact=True)["result"]
+        out["dict"].append(
+            rem(
+                json.dumps(obj, indent=2),
+                r if isinstance(r, str) else json.dumps(r, indent=2),
+            )
+        )
+        out["dict_types"].append(type(r).__name__)
+    for obj in ns["_json_fuzz_corpus"]():
+        out["fuzz_types"].append(
+            type(pm.process_output(obj, redact=True)["result"]).__name__
+        )
+    buf = io.BytesIO()
+    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
+        g.write(json.dumps(out, sort_keys=True, separators=(",", ":")).encode())
+    Path(sys.argv[2]).write_text(base64.encodebytes(buf.getvalue()).decode())
+    print(
+        "recorded from",
+        pmcp.__file__,
+        {k: len(v) for k, v in out.items()},
+        "tier1",
+        len(G.corpus(1)),
+        "dict_types",
+        {t: out["dict_types"].count(t) for t in sorted(set(out["dict_types"]))},
+        "fuzz_types",
+        {t: out["fuzz_types"].count(t) for t in sorted(set(out["fuzz_types"]))},
+    )
diff --git a/tests/test_redaction.py b/tests/test_redaction.py
new file mode 100644
index 0000000000000000000000000000000000000000..d2d2252cb9050035de4b6ca98e11d0dd3fa1a913
--- /dev/null
+++ b/tests/test_redaction.py
@@ -0,0 +1,3347 @@
+"""Both directions of secret redaction, pinned together (Consiliency/pmcp#234).
+
+The redactor stands between a downstream server's error text and a
+prompt-injectable context window. A false negative puts a credential into that
+window; a false positive teaches readers that `[REDACTED]` means nothing. So a
+redactor tested only on secrets gets tuned until it redacts everything, and one
+tested only on prose gets tuned until it redacts nothing. This file holds both
+corpora and ranks them equally: `PROSE` must survive byte-identical, and every
+`CREDENTIALS` entry must vanish with its surroundings intact.
+
+Both surfaces are covered -- `sanitize_auth_diagnostic` (the engine, used
+directly by the client manager, the CLI and the doctor) and
+`PolicyManager.redact_secrets` (the engine plus the operator's patterns).
+
+The never-worse-than-main differential is grammar-derived
+(`tests/_redaction_grammar.py`). Its tier 1 runs in the default suite; its
+full set is marked `slow` and runs only on request (`pytest
+tests/test_redaction.py -m slow`, about 2-3 minutes). `addopts` excludes
+`slow`, so CI (`pytest tests/`) does not run it.
+"""
+
+from __future__ import annotations
+
+import base64
+import gzip
+import json
+import random
+import re
+import string
+import uuid
+from pathlib import Path
+from collections.abc import Callable
+from typing import Any
+
+import pytest
+
+from pmcp.auth import (
+    AUTH_DIAGNOSTIC_SECRET_KEYS,
+    AUTH_SECRET_QUERY_KEYS,
+    REDACTED,
+    WEAK_SECRET_KEYS,
+    apply_redaction_spans,
+    collect_redaction_spans,
+    redact_auth_url,
+    sanitize_auth_diagnostic,
+)
+from pmcp.policy.policy import (
+    _ADDITIVE_DEFAULT_PATTERNS,
+    PolicyManager,
+)
+from tests import _redaction_grammar as G
+
+# --------------------------------------------------------------------------- #
+# The prose corpus. Every line is text a downstream server, pip, git, httpx or
+# the interpreter has produced or could produce, and none of it is a credential.
+# Each keyword the pre-#234 rule mangled appears at least once in the position
+# that mangled it. Extend it when a false positive is found; never trim it to
+# make a rule pass.
+# --------------------------------------------------------------------------- #
+
+PROSE = """\
+The token bucket rate limiter refused the call; the secret ingredient is
+patience. Your session expired, so the cookie consent banner reappeared and the
+password reset flow sent a status code 401 back. The tokenizer choked on unicode
+input and the encoded payload was base64. Set the PMCP_FEEDBACK_TOKEN environment
+variable to enable submission; the API key is read from the env store. Keys are
+rotated weekly. A secret to good code is small functions. Missing bearer token:
+the bearer of bad news said a Bearer Token is required. Session re-use is off.
+
+Traceback (most recent call last):
+  File "/home/u/.venv/lib/python3.10/site-packages/httpx/_client.py", line 1013, in send
+    response = self._send_handling_auth(
+  File "/home/u/code/pmcp/src/pmcp/client/manager.py", line 2641, in _run_transport
+    raise ResourceServerAuthError("invalid_token", str(exc)) from exc
+UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0
+httpx.HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.example.com/v1/tools'
+<pmcp.client.manager.ClientManager object at 0x7f3a2b1c4d50> pod pmcp-7d9f8b6c5-x2k9q
+JSONDecodeError SSLCertVerificationError IPv6Address Ed25519PrivateKey X509Cert
+Rsa2048Key Oauth2ClientError Base64UrlEncoder Sha256HashAlgorithm parseJSON2Dict
+
+status_code=401 error_code=invalid_grant
+token_endpoint=https://auth.example/oauth/token code=404 token v2 is out
+{"code": -32601, "message": "Method not found", "data": {"code": "not_found"}}
+{"code": "not_found", "message": "no such tool", "request_id": "550e8400-e29b-41d4-a716-446655440000"}
+secret_arn=arn:aws:secretsmanager:us-east-1:123456789012:secret:MySecret-a1b2c3
+exit code 137; error code 0x80070005; zip code 94105; status code 503
+error_code=AADSTS50011 sqlstate_code=42P01 error_codes=[50011] reason_code=E-1234
+
+commit 3843d2f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6 (HEAD -> main, origin/main)
+Author: Example <dev@example.test>
+Date:   2026-09-23T04:12:00+00:00
+    docs(plans): plan CONSENT and PKGID (#239)
+sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
+Successfully installed pmcp-1.19.2 httpx-0.27.0 anyio-4.4.0 exceptiongroup-1.2.1
+Downloading pmcp-1.19.2-py3-none-any.whl (312 kB) for x86_64-linux-gnu
+TLS: ECDHE-RSA-AES256-GCM-SHA384 / TLS_AES_128_GCM_SHA256 negotiated at 20260923T041200Z
+tests/test_redaction.py::test_s11_an_approved_project_policy_cannot_widen_the_user_policy PASSED
+detailed-234-redactor-shape-based-20260922-1130.md build-20260922123456 s3bucket2024
+[REDACTED] [REDACTED_EMAIL] ghp_ sk- xoxb- AKIA
+"""
+
+# --------------------------------------------------------------------------- #
+# The credential corpus: (label, text, must_vanish, must_survive). The label is
+# the CLASS the entry stands for, not the vendor; a vendor nobody listed is
+# still caught if its shape is here. The four the issue measured come first.
+#
+# Samples deliberately do NOT match GitHub secret-scanning detectors (a Stripe
+# key with 24 body characters, a DigitalOcean token with 64 hex, a Slack token
+# with a numeric segment and a mixed-case body): push protection rejects a
+# commit that carries one, however synthetic. Each sample keeps the SHAPE the
+# rule needs and nothing more; the Slack rule is exercised by the issue's own
+# `xoxb-2444-2444-abcdefghijklmnop`, which the detector leaves alone.
+# --------------------------------------------------------------------------- #
+
+CREDENTIALS: list[tuple[str, str, str, list[str]]] = [
+    (
+        "credential-in-url-path",
+        "failed to fetch https://api.example.com/v1/sk-live-abc123def456/status",
+        "sk-live-abc123def456",
+        ["failed to fetch https://api.example.com/v1/", "/status"],
+    ),
+    (
+        "aws-access-key-id",
+        "unexpected value AKIAIOSFODNN7EXAMPLE in the request",
+        "AKIAIOSFODNN7EXAMPLE",
+        ["unexpected value", "in the request"],
+    ),
+    (
+        "prefixed-random-body",
+        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
+        "16C7e42F292c6912E7710c838347Ae178B4a",
+        [],
+    ),
+    (
+        "slack-documented-shape",
+        "xoxb-2444-2444-abcdefghijklmnop",
+        "2444-2444-abcdefghijklmnop",
+        [],
+    ),
+    (
+        "bare-random-alnum",
+        "bare 4eC39HqLyjWDarjtT1zdp7dc here",
+        "4eC39HqLyjWDarjtT1zdp7dc",
+        ["bare", "here"],
+    ),
+    (
+        "base64-with-slashes",
+        "aws secret wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY leaked",
+        "wJalrXUtnFEMI",
+        ["aws secret", "leaked"],
+    ),
+    (
+        "base64-padded",
+        "basic dXNlcjpwYXNzd29yZA== auth",
+        "dXNlcjpwYXNzd29yZA",
+        ["basic", "auth"],
+    ),
+    (
+        "prefixed-hex-body",
+        "dop_v1_9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822c",
+        "9f86d081884c7d659a2feaa0c55ad015",
+        [],
+    ),
+    (
+        "prefixed-short-sample",
+        "sk_test_4eC39HqLyjWDar7dc",
+        "4eC39HqLyjWDar7dc",
+        [],
+    ),
+    (
+        "prefixed-low-entropy-with-digits",
+        "sk-abcdef123456",
+        "abcdef123456",
+        [],
+    ),
+    (
+        "prefixed-long-body",
+        "sk-proj-Ab3dEf6GhI9jKl2MnO5pQr8StU1vWx4Yz7AbCdEfGhIjKlMnOpQrStUvWxYz",
+        "Ab3dEf6GhI9jKl2MnO5pQr8StU1vWx4Yz7AbCdEfGhIjKlMnOpQrStUvWxYz",
+        [],
+    ),
+    (
+        "google-api-key",
+        "AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBxY",
+        "9tSrke72PouQMnMX",
+        [],
+    ),
+    (
+        "webhook-path-keeps-route",
+        "https://hooks.example/services/T0123ABCD/B0123ABCD/a1B2c3D4e5F6g7H8i9J0k1L2",
+        "a1B2c3D4e5F6g7H8i9J0k1L2",
+        ["https://hooks.example/services/"],
+    ),
+    (
+        "pem-block",
+        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ\n-----END RSA PRIVATE KEY-----",
+        "MIIEow",
+        [],
+    ),
+    (
+        "json-quoted-value",
+        '{"password": "hunter2"}',
+        "hunter2",
+        ['"password"'],
+    ),
+    (
+        "flag-with-shaped-value",
+        "--token abc123def456 --password hunter2",
+        "abc123def456",
+        ["--token", "--password"],
+    ),
+    (
+        "flag-with-passphrase",
+        "--password correct-horse-battery-staple",
+        "correct-horse-battery-staple",
+        ["--password"],
+    ),
+    (
+        "camelcase-key",
+        "accessToken=AbC123dEf456GhI",
+        "AbC123dEf456GhI",
+        ["accessToken="],
+    ),
+    (
+        "oauth-code-bare",
+        "code=super-secret",
+        "super-secret",
+        ["code="],
+    ),
+    (
+        "oauth-code-qualified",
+        "auth_code=SplxlOBeZQQYbYS6WxSbIA",
+        "SplxlOBeZQQYbYS6WxSbIA",
+        ["auth_code="],
+    ),
+    (
+        "oauth-code-hex",
+        "code=a1b2c3d4e5f6a7b8c9d0",
+        "a1b2c3d4e5f6a7b8c9d0",
+        ["code="],
+    ),
+    (
+        "keyword-colon-low-entropy",
+        "password: hunter2",
+        "hunter2",
+        ["password:"],
+    ),
+    (
+        "header-with-qualifier",
+        "X-Auth-Token: abc123",
+        "abc123",
+        ["X-Auth-Token:"],
+    ),
+    (
+        "cookie-header",
+        "Set-Cookie: session=abc; Path=/",
+        "abc",
+        ["Set-Cookie:", "Path=/"],
+    ),
+    (
+        "session-id",
+        "session=013G8iK4noj1iNbSTqJVFEX6",
+        "013G8iK4noj1iNbSTqJVFEX6",
+        ["session="],
+    ),
+    (
+        "bearer-header",
+        "Authorization: Bearer abc.def",
+        "abc.def",
+        ["Authorization:"],
+    ),
+    (
+        "bearer-bare",
+        "sent Bearer test-token",
+        "test-token",
+        ["sent Bearer"],
+    ),
+    (
+        "jwt",
+        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
+        "eyJzdWIiOiJzZWNyZXQifQ",
+        [],
+    ),
+]
+
+TOKEN = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
+
+
+def _engine(text: str) -> str:
+    return sanitize_auth_diagnostic(text, max_length=None)
+
+
+def _policy(text: str) -> str:
+    return PolicyManager().redact_secrets(text)
+
+
+# === the prose direction ================================================== #
+
+
+def test_prose_survives_the_engine_byte_identical() -> None:
+    assert _engine(PROSE) == PROSE
+
+
+def test_prose_survives_the_policy_surface_byte_identical() -> None:
+    assert _policy(PROSE) == PROSE
+
+
+def test_the_keyword_rule_needs_a_separator_or_a_credential_shaped_value() -> None:
+    """`token bucket` is prose; `--token abc123def456` is a flag with a secret.
+
+    The pre-#234 rule treated whitespace as a separator, so any 3+ letter word
+    after a keyword vanished. `the secret to good code` survived only because
+    `to` is two letters -- an accident, not a guard.
+    """
+    assert _engine("token bucket rate limiting is enabled") == (
+        "token bucket rate limiting is enabled"
+    )
+    assert _engine("the secret ingredient is love") == "the secret ingredient is love"
+    assert _engine("session expired, password reset sent") == (
+        "session expired, password reset sent"
+    )
+    assert _engine("--token abc123def456") == "--token [REDACTED]"
+    assert _engine("token secret-bearer failed") == "token [REDACTED] failed"
+
+
+def test_bearer_is_a_scheme_not_a_word() -> None:
+    """`Bearer <token>` is redacted; `bearer token` (the phrase) is not.
+
+    On main `\\bbearer` also fired *inside* `secret-bearer failed` and redacted
+    `failed` -- the word after the credential rather than the credential. A
+    hyphenated word that ends in `bearer` is a word, not the scheme: what
+    follows `secret-bearer` or `non-bearer` is not a token.
+    """
+    assert _engine("Missing bearer token") == "Missing bearer token"
+    assert _engine("the bearer of bad news") == "the bearer of bad news"
+    assert _engine("Bearer Token is required") == "Bearer Token is required"
+    assert _engine("Bearer test-token") == "Bearer [REDACTED]"
+    assert _engine("Bearer hunter2") == "Bearer [REDACTED]"
+    # not the Bearer SCHEME: the word after a compound is not a token ...
+    assert _engine("secret-bearer failed") == "secret-bearer failed"
+    # ... but `secret-bearer` is a suffixed `secret` key, and a credential-shaped
+    # value after it is redacted by that rule, as on main
+    assert _engine("secret-bearer hunter2") == "secret-bearer [REDACTED]"
+    # `non-bearer 2024-01-01`: main's `\\bbearer` fires after a joiner, and a
+    # credential-shaped value there is not in any stated class, so the floor
+    # keeps main's redaction (B2 of rev 10's board: `--bearer s3cr3tvalue`)
+    assert _engine("non-bearer 2024-01-01 report") == "non-bearer [REDACTED] report"
+    # a `name=value` pair right after the scheme is its value, as on main
+    # (the maintainer's N11 decision after rev 11: never directly after
+    # Bearer/Authorization; `expires_in=3600` and `realm="api"` go as main's
+    # `bearer\\s+[^\\s,;]+` took them)
+    assert _engine('Bearer realm="api", error="x"') == 'Bearer [REDACTED]", error="x"'
+    assert _engine("token_type=Bearer expires_in=3600") == (
+        "token_type=Bearer [REDACTED]"
+    )
+
+
+def test_code_is_a_credential_only_in_the_oauth_sense() -> None:
+    """Bare `code=` is the OAuth callback parameter; `status_code=` is a status.
+
+    JSON-RPC (`{"code": -32601}`) is every MCP error and REST (`{"code":
+    "not_found"}`) every other one, so even bare `code` keeps a word or number.
+    """
+    assert _engine('{"code": -32601, "message": "x"}') == (
+        '{"code": -32601, "message": "x"}'
+    )
+    assert _engine('{"code": "not_found"}') == '{"code": "not_found"}'
+    assert _engine("status_code=401 error_code=invalid_grant") == (
+        "status_code=401 error_code=invalid_grant"
+    )
+    assert _engine("code=404 exit code 137") == "code=404 exit code 137"
+    assert _engine("code=super-secret") == "code=[REDACTED]"
+    assert _engine("auth_code=SplxlOBeZQQYbYS6WxSbIA") == "auth_code=[REDACTED]"
+    assert _engine("device_code=a1b2c3d4e5f6a7b8c9d0") == "device_code=[REDACTED]"
+
+
+def test_the_policy_defaults_probe_stays_invisible_to_the_engine() -> None:
+    """`ghp_abcdefghijklmnop` proves the policy DEFAULTS apply (SECURITY.md C-13).
+
+    Those proofs (`tests/test_project_source_consent_policy.py`,
+    `tests/test_trust_boundaries_e2e.py`) assert the probe is redacted *because
+    a default pattern is still present*. If the engine ever caught it too they
+    would pass with the defaults dropped. The probe is deliberately whole-alpha
+    after its prefix; keep it that way and keep the engine blind to it.
+    """
+    assert _engine("ghp_abcdefghijklmnop") == "ghp_abcdefghijklmnop"
+    assert "ghp_abcdefghijklmnop" not in _policy("ghp_abcdefghijklmnop")
+
+
+# === the credential direction ============================================= #
+
+
+@pytest.mark.parametrize(
+    ("text", "must_vanish", "must_survive"),
+    [entry[1:] for entry in CREDENTIALS],
+    ids=[entry[0] for entry in CREDENTIALS],
+)
+def test_the_engine_redacts_the_credential_and_keeps_its_surroundings(
+    text: str, must_vanish: str, must_survive: list[str]
+) -> None:
+    out = _engine(text)
+    assert must_vanish not in out, out
+    assert "[REDACTED]" in out
+    for kept in must_survive:
+        assert kept in out, out
+
+
+@pytest.mark.parametrize(
+    ("text", "must_vanish", "must_survive"),
+    [entry[1:] for entry in CREDENTIALS],
+    ids=[entry[0] for entry in CREDENTIALS],
+)
+def test_the_policy_surface_redacts_the_credential_and_keeps_its_surroundings(
+    text: str, must_vanish: str, must_survive: list[str]
+) -> None:
+    out = _policy(text)
+    assert must_vanish not in out, out
+    for kept in must_survive:
+        assert kept in out, out
+
+
+def test_a_url_path_credential_is_redacted_in_diagnostics_not_in_the_url() -> None:
+    """The engine reaches a path segment; `redact_auth_url` deliberately does not.
+
+    `redact_auth_url` is what `sanitize_url_elicitation_url` returns -- the URL
+    the operator must OPEN to authorize -- and an IdP's authorization-server id
+    in that path (`/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize`) is exactly the
+    shape a credential has. A diagnostic can lose it; the login flow cannot.
+    """
+    webhook = (
+        "https://hooks.example/services/T0123ABCD/B0123ABCD/a1B2c3D4e5F6g7H8i9J0k1L2"
+    )
+    assert _engine(f"failed: {webhook}") == (
+        "failed: https://hooks.example/services/T0123ABCD/B0123ABCD/[REDACTED]"
+    )
+    authorize = (
+        "https://dev-1.okta.com/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize?state=ok"
+    )
+    assert redact_auth_url(authorize) == authorize
+
+
+def test_identifiers_that_are_not_credentials_are_kept() -> None:
+    """The shape rule's named exemptions, one probe each.
+
+    hex digests and UUIDs (uniform hex), `0x` addresses (uniform hex behind a
+    prefix), camelCase (no `xAB` signature), identifiers with one embedded
+    number (`X509Cert`), timestamps (low transition ratio), and hyphen-joined
+    short pieces (`pmcp-7d9f8b6c5-x2k9q`: scored per segment, never whole).
+    """
+    for kept in [
+        "3843d2f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
+        "550e8400-e29b-41d4-a716-446655440000",
+        "0x7f3a2b1c4d50",
+        "UnicodeDecodeError",
+        "IPv6Address",
+        "Ed25519PrivateKey",
+        "20260923T041200Z",
+        "x86_64-linux-gnu",
+        "ECDHE-RSA-AES256-GCM-SHA384",
+        "pmcp-7d9f8b6c5-x2k9q",
+    ]:
+        assert _engine(kept) == kept
+
+
+def test_payload_sized_runs_are_data_not_credentials() -> None:
+    """A base64 image or encoded file survives; a 200-character token does not.
+
+    `gateway.tasks_result` redacts by default and `process_output` JSON-dumps
+    the whole result, so `ImageContent.data` goes through this engine. No
+    vendor issues an unbroken token past 256 characters (JWTs are dot-joined
+    and have their own rule; private keys are PEM blocks).
+    """
+    blob = base64.b64encode(bytes(range(256)) * 24).decode()
+    assert len(blob) > 8000
+    assert _engine(blob) == blob
+    assert _policy(blob) == blob
+    rng = random.Random(234)
+    token = "".join(
+        rng.choice(string.ascii_letters + string.digits) for _ in range(200)
+    )
+    assert _engine(f"leaked {token} here") == "leaked [REDACTED] here"
+
+
+def test_a_slash_leading_payload_is_not_split_into_scored_pieces() -> None:
+    """The payload bound applies to the WHOLE run, before any path splitting.
+
+    JPEG base64 always starts `/9j/` and carries a `/` every ~64 characters;
+    scoring each piece between slashes on its own turned an ordinary image into
+    `[REDACTED]/[REDACTED]/...` (board finding on the first revision). A long
+    run that reads as a route -- short pieces, two of them plain words -- is
+    still split, so a credential segment deep in a REST path is still caught.
+    """
+    leading = "/" + "4eC39HqLyjWDarjtT1zdp7dc" + "/" + "A" * 8190
+    assert _engine(leading) == leading
+    assert _policy(leading) == leading
+    rng = random.Random(234)
+    body = base64.b64encode(bytes(rng.getrandbits(8) for _ in range(4500))).decode()
+    jpeg = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgH" + body
+    assert body.count("/") > 50  # the shape that broke: a slash every ~64 chars
+    assert _engine(jpeg) == jpeg
+    assert _policy(jpeg) == jpeg
+    route = (
+        "/api/v1/organizations/acme-corp/projects/"
+        + "/".join(f"segment{i}" for i in range(30))
+        + "/secrets/4eC39HqLyjWDarjtT1zdp7dc/versions/latest"
+    )
+    assert len(route) > 256
+    assert _engine(route) == route.replace("4eC39HqLyjWDarjtT1zdp7dc", "[REDACTED]")
+
+
+# === composition ========================================================== #
+
+
+def test_redaction_is_idempotent() -> None:
+    """`[REDACTED]` must not itself be re-matched; surfaces apply the engine twice."""
+    for text in [PROSE, *[entry[1] for entry in CREDENTIALS]]:
+        once = _engine(text)
+        assert _engine(once) == once, text
+        twice = _policy(text)
+        assert _policy(twice) == twice, text
+
+
+def test_redact_secrets_keeps_a_key_but_never_splits_inside_a_secret() -> None:
+    """The post-pass splits `key=value` at the separator, and only there.
+
+    On main the split took the FIRST `:`/`=` in the match, so an operator
+    pattern for a base64 shape kept the secret and replaced its padding:
+    `dXNlcjpwYXNzd29yZA= [REDACTED]`. The probe value is deliberately
+    low-entropy (uniform hex letters) so the engine's shape pass leaves it
+    alone and only the operator pattern -- and therefore the split -- is
+    exercised; a real base64 secret would be gone before the post-pass ran.
+    """
+    manager = PolicyManager()
+    manager._redaction_regexes = [re.compile(r"[A-Za-z0-9+/]{16,}={1,2}")]
+    assert (
+        sanitize_auth_diagnostic("abcdabcdabcdabcdabcd==") == "abcdabcdabcdabcdabcd=="
+    )
+    assert manager.redact_secrets("basic abcdabcdabcdabcdabcd== auth") == (
+        "basic [REDACTED] auth"
+    )
+    manager._redaction_regexes = [re.compile(r"mykey\s*[:=]\s*\S+")]
+    # main's split of this pattern is the floor (it takes the value too), and
+    # the two merge into one marker
+    assert manager.redact_secrets("mykey: abcdef") == "mykey:[REDACTED]"
+
+
+def test_the_engine_redacts_before_it_truncates() -> None:
+    """The cut lands on `[REDACTED]`, never on a token prefix.
+
+    The token starts at offset 391 and `max_length` is 400: were the cut taken
+    first, nine characters of it would survive and no longer have a redactable
+    shape.
+    """
+    text = "x" * 390 + " " + TOKEN
+    out = sanitize_auth_diagnostic(text, max_length=400)
+    assert len(out) == 400
+    assert "ghp_" not in out
+    assert out.endswith(" [REDACTED")
+
+
+def test_process_output_never_ends_on_a_partial_token() -> None:
+    """The byte cut is taken BEFORE redaction, so it must not split a token.
+
+    With `max_bytes=300` the cut falls 200 bytes in, nine characters into the
+    token; `ghp_16C7e` has no shape any rule recognises. Backing the cut up to
+    the preceding boundary removes the exposed prefix.
+    """
+    policy = PolicyManager()
+    output = "a" * 190 + " " + TOKEN + " " + "c" * 100
+    processed = policy.process_output(output, redact=True, max_bytes=300)
+    assert processed["truncated"] is True
+    assert "ghp_" not in processed["result"], processed["result"]
+    assert processed["result"].startswith("a" * 190)
+
+
+def test_process_output_never_ends_on_a_partial_token_after_a_path() -> None:
+    """The trailing run is the PATH plus the token, and the whole run is backed
+    out of (board finding on the first revision: a 64-character bound measured
+    on the run left `/bbb.../ghp_16C7e` in the output). Past 256 characters the
+    run is a payload or a long path, and its last `/`-segment -- where a
+    credential in a path sits -- is still backed out of.
+    """
+    policy = PolicyManager()
+    output = "a" * 120 + " /" + "b" * 68 + "/" + TOKEN + " " + "c" * 100
+    processed = policy.process_output(output, redact=True, max_bytes=300)
+    assert processed["truncated"] is True
+    assert "ghp_" not in processed["result"], processed["result"]
+    assert processed["result"].startswith("a" * 120 + " ")
+    long_path = "a" * 20 + " /" + "b" * 300 + "/" + TOKEN + " " + "c" * 100
+    processed = policy.process_output(long_path, redact=True, max_bytes=450)
+    assert processed["truncated"] is True
+    assert "ghp_" not in processed["result"], processed["result"]
+    assert processed["result"].startswith("a" * 20 + " /" + "b" * 300)
+
+
+def test_process_output_still_truncates_a_single_giant_run() -> None:
+    """A run of `b`s is not a credential, and the cut lands where it always
+    did (`max_bytes - 100`): redaction ran BEFORE the cut, so nothing has to
+    be backed out of (revs 2-3 backed the cut up to a run boundary; rev 4
+    redacts the window first and the back-up is gone)."""
+    processed = PolicyManager().process_output("b" * 1000, redact=True, max_bytes=600)
+    assert processed["truncated"] is True
+    assert processed["result"].startswith("b" * 500)
+    processed = PolicyManager().process_output("b" * 400, redact=True, max_bytes=300)
+    assert processed["truncated"] is True
+    assert processed["result"].startswith("b" * 200 + "\n\n[... OUTPUT TRUNCATED")
+
+
+def test_a_quoted_value_runs_to_its_closing_quote() -> None:
+    """An escaped quote inside a JSON string does not end the value.
+
+    `"[^"]*"` stopped at the `\\"` in `{"password": "a\\"hunter2"}` and left
+    `hunter2"` behind on both surfaces (board finding on the first revision).
+    """
+    for text, expected in [
+        ('{"password": "a\\"hunter2"}', '{"password": "[REDACTED]"}'),
+        ("{'password': 'a\\'hunter2'}", "{'password': '[REDACTED]'}"),
+        (
+            '{"password": "hunter2", "user": "bob"}',
+            '{"password": "[REDACTED]", "user": "bob"}',
+        ),
+    ]:
+        assert _engine(text) == expected
+        assert _policy(text) == expected
+
+
+def test_an_earlier_pass_never_eats_the_boundary_a_later_pass_needs() -> None:
+    """Keyed values are redacted first, and the looser passes stop at quotes.
+
+    Rev 2 ran the `Bearer` pass before the keyword pass with a value class of
+    `[^\\s,;]+`, so on `{"password": "hunter2 Bearer test-token"}` it consumed
+    the closing quote and brace and the keyword rule could no longer match:
+    `{"password": "hunter2 Bearer [REDACTED]` on both surfaces, `hunter2`
+    visible (board finding on rev 2). The `Authorization` rule had the same
+    value class and also missed a JSON-quoted key entirely.
+    """
+    for text, expected in [
+        ('{"password": "hunter2 Bearer test-token"}', '{"password": "[REDACTED]"}'),
+        ("{'password': 'hunter2 Bearer x'}", "{'password': '[REDACTED]'}"),
+        (
+            '{"authorization": "Bearer abc123def456", "x": 1}',
+            '{"authorization": "[REDACTED]", "x": 1}',
+        ),
+        ('Authorization: "Bearer abc.def"', 'Authorization: "[REDACTED]"'),
+        ('{"note": "see Bearer abc123def456"}', '{"note": "see Bearer [REDACTED]"}'),
+        ('token="Bearer abc"', 'token="[REDACTED]"'),
+    ]:
+        assert _engine(text) == expected, text
+        assert "hunter2" not in _policy(text) and "abc" not in _policy(text), text
+
+
+def test_truncation_never_leaks_a_quoted_multi_word_password() -> None:
+    """Sweep the cut across quoted passwords that contain spaces.
+
+    The byte cut can land inside a quoted value after a space: no trailing
+    token run ends there, and with the closing quote gone the rev-2 keyword
+    rule could not match, so the first word of the password stood in the
+    output (board finding on rev 2: 128 of 1 452 cuts). A quoted value with
+    no closing quote on its line now runs to the end of the line. The sweep
+    asserts that it actually covers the value -- a range that misses the cut
+    region reports zero leaks for the wrong reason.
+    """
+    policy = PolicyManager()
+    leaks: list[str] = []
+    for prefix in (50, 120, 177, 190):
+        for password in ("hunter2 tail", "correct horse battery staple", "p4ss w0rd!"):
+            output = "a" * prefix + ' {"password": "' + password + '"}' + "c" * 100
+            opening = output.index('"' + password)
+            closing = opening + len(password) + 1
+            inside = 0
+            for max_bytes in range(prefix + 80, prefix + 201):
+                cut = max_bytes - 100  # `truncate_output` leaves room for the marker
+                inside += opening < cut < closing
+                result = policy.process_output(output, redact=True, max_bytes=max_bytes)
+                if any(word in result["result"] for word in password.split()):
+                    leaks.append(
+                        f"prefix={prefix} password={password!r} max_bytes={max_bytes}"
+                    )
+            assert inside > 0, (prefix, password)  # the sweep reached the value
+    assert leaks == []
+
+
+def test_no_pass_can_consume_a_boundary_another_pass_needs() -> None:
+    """Every pass reads the original text; replacements land in one step.
+
+    Rev 3 ran the URL pass first and rewrote the text: redacting the query
+    value removed the backslash that escaped a quote, which turned the escaped
+    quote into a closing one, and `{"password": "https://example.test/?token=
+    abc123def456\\"hunter2"}` came out as `{"password": [REDACTED]hunter2"}` on
+    both surfaces (board finding on rev 3). With span collection the keyed
+    value's span contains the URL's span and wins.
+    """
+    text = '{"password": "https://example.test/?token=abc123def456\\"hunter2"}'
+    assert _engine(text) == '{"password": "[REDACTED]"}'
+    assert _policy(text) == '{"password": "[REDACTED]"}'
+    # a URL that is NOT inside a keyed value keeps its host and route
+    assert _engine("see https://example.test/?token=abc123def456&page=2.") == (
+        "see https://example.test/?token=[REDACTED]&page=2."
+    )
+
+
+def test_truncation_after_a_backslash_cannot_expose_a_password() -> None:
+    """The cut lands right after the `\\` of an escaped character.
+
+    Rev 3 cut first and then asked the redactor to make sense of `"hunter2\\`
+    (board finding on rev 3: 48 of 2 880 escape-heavy cuts leaked). Rev 4
+    redacts the window before the cut, so the redactor sees the whole value.
+    """
+    output = "a" * 177 + " " + json.dumps({"password": "hunter2\\tail"}) + "c" * 100
+    processed = PolicyManager().process_output(output, redact=True, max_bytes=300)
+    assert processed["truncated"] is True
+    assert "hunter2" not in processed["result"], processed["result"]
+
+
+def test_the_redaction_window_reaches_past_the_cap() -> None:
+    """A keyed value that straddles the cap is seen whole by the redactor.
+
+    The window is `max_bytes + _REDACTION_WINDOW_SLACK` characters; a
+    4 000-character value (PEM-sized) that starts before the cap and ends
+    after it is inside the window and is redacted before the cut.
+    """
+    value = "".join(random.Random(4).choice(string.ascii_letters) for _ in range(4000))
+    output = "a" * 190 + ' {"password": "' + value + '"}' + "c" * 100
+    processed = PolicyManager().process_output(output, redact=True, max_bytes=400)
+    assert processed["truncated"] is True  # the cut is at byte 300, inside the value
+    assert value[:4] not in processed["result"], processed["result"][180:240]
+    assert processed["result"].startswith("a" * 190 + ' {"password": "[REDACTED]')
+
+
+# === properties =========================================================== #
+
+_PRINTABLE = (
+    "".join(c for c in string.printable if c not in "\t\n\r\x0b\x0c") + "éßñ日本語🙂"
+)
+_DELIMITERS = frozenset(" \t\"',;&()[]{}")
+
+
+def _random_passwords(rng: random.Random, count: int) -> list[str]:
+    return [
+        "".join(rng.choice(_PRINTABLE) for _ in range(rng.randint(4, 24)))
+        for _ in range(count)
+    ]
+
+
+def _single_quoted(value: str) -> str:
+    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
+
+
+def _keyed_forms(password: str) -> list[tuple[str, str, str]]:
+    """(text, encoded value as it appears in the text, probe).
+
+    The probe is the first four characters of the value as encoded -- what a
+    leak would show. Bare forms (`password=x`, a header) exist only for
+    passwords without delimiters: an unquoted syntax cannot carry a space or
+    a quote at all.
+    """
+    forms: list[tuple[str, str, str]] = []
+    for encoded, text in [
+        (json.dumps(password), json.dumps({"password": password})),
+        (
+            json.dumps(password, ensure_ascii=False),
+            json.dumps({"password": password}, ensure_ascii=False),
+        ),
+        (_single_quoted(password), "{'password': " + _single_quoted(password) + "}"),
+    ]:
+        forms.append((text, encoded, encoded[1:5]))
+    if not any(c in _DELIMITERS for c in password) and not password.startswith("="):
+        # `key==x` reads as a comparison (`if token == expected`), so a bare
+        # value cannot begin with `=`; it can carry one anywhere else.
+        forms.append((f"password={password}", password, password[:4]))
+        forms.append((f"X-Api-Key: {password}", password, password[:4]))
+    return forms
+
+
+def _probe_is_usable(text: str, encoded: str, probe: str) -> bool:
+    """Skip a probe shorter than four characters or one the surrounding text
+    already contains (the assertion could not tell a leak from the template)."""
+    return len(probe) == 4 and probe not in text.replace(encoded, "", 1)
+
+
+def test_property_every_keyed_password_is_redacted_whole() -> None:
+    """Thousands of random passwords from every printable character.
+
+    Quotes, backslashes, spaces, brackets, unicode: in JSON (ASCII-escaped and
+    not), single-quoted, and -- for delimiter-free passwords -- bare and as a
+    header. The redacted output never contains the first four characters of
+    the value as it appeared. The board's cases (`a\\"hunter2`, `hunter2 Bearer
+    x`, `https://…\\"hunter2`) are samples of this property.
+    """
+    rng = random.Random(234)
+    checked = skipped = 0
+    for password in _random_passwords(rng, 3000):
+        for text, encoded, probe in _keyed_forms(password):
+            if not _probe_is_usable(text, encoded, probe):
+                skipped += 1
+                continue
+            checked += 1
+            assert probe not in _engine(text), (password, text, _engine(text))
+            assert probe not in _policy(text), (password, text, _policy(text))
+    assert checked > 10000, (checked, skipped)
+
+
+def test_property_truncation_never_exposes_a_keyed_password() -> None:
+    """The same forms, with the cut swept across every offset of the value.
+
+    `process_output(redact=True)` must never show the first four characters of
+    the value, wherever the cut lands: before it, inside it (after a space,
+    after a backslash, after a quote), or after it. The test asserts that the
+    sweep actually lands inside every value it tries -- a sweep that misses
+    the cut region reports zero leaks for the wrong reason.
+    """
+    rng = random.Random(4234)
+    policy = PolicyManager()
+    cuts = inside = values = 0
+    for password in _random_passwords(rng, 300):
+        prefix = rng.choice((50, 120, 177, 190))
+        for form, encoded, probe in _keyed_forms(password):
+            text = "a" * prefix + " " + form + "c" * 100
+            if not _probe_is_usable(text, encoded, probe):
+                continue
+            start = text.index(encoded)
+            start_bytes = len(text[:start].encode("utf-8"))
+            end_bytes = start_bytes + len(encoded.encode("utf-8"))
+            landed = 0
+            for max_bytes in range(start_bytes - 2 + 100, end_bytes + 3 + 100):
+                cut = max_bytes - 100  # `truncate_output` leaves room for the marker
+                result = policy.process_output(text, redact=True, max_bytes=max_bytes)
+                cuts += 1
+                landed += start_bytes < cut < end_bytes
+                assert probe not in result["result"], (
+                    password,
+                    form,
+                    max_bytes,
+                    result,
+                )
+            assert landed > 0, (password, form)
+            inside += landed
+            values += 1
+    assert values > 1000 and cuts > 20000 and inside > 15000, (values, cuts, inside)
+
+
+_PROSE_WORDS = (
+    "the token bucket secret ingredient session expired password reset cookie "
+    "consent bearer of bad news rate limit refused call key keys rotated weekly "
+    "code status exit error invalid grant not found request failed because "
+    "timeout retry server client scope read write admin user tenant api jwt "
+    "saml assertion ticket sid tokens secrets passwords sessions cookies"
+).split()
+_PROSE_IDENTIFIERS = (
+    "ResourceServerAuthError UnicodeDecodeError IPv6Address Ed25519PrivateKey "
+    "X509Cert Rsa2048Key Oauth2ClientError parseJSON2Dict toHTML5String sha256sum"
+).split()
+
+
+def _random_prose(rng: random.Random) -> str:
+    """Prose and diagnostic text made of plain words, numbers, identifiers,
+    digests, ids and timestamps. A keyword is followed only by a word or a
+    number: the design says a digit-bearing non-word after a bare keyword is
+    a credential, and this corpus is the other half of that contract."""
+
+    def words() -> str:
+        return " ".join(rng.choice(_PROSE_WORDS) for _ in range(rng.randint(2, 8)))
+
+    def number() -> str:
+        return str(rng.randint(0, 99999))
+
+    def hexdigits(n: int) -> str:
+        return "".join(rng.choice("0123456789abcdef") for _ in range(n))
+
+    clauses = [
+        words,
+        lambda: f"{words()} {number()}",
+        lambda: f"status_code={number()} error_code={rng.choice(_PROSE_WORDS)}_{rng.choice(_PROSE_WORDS)}",
+        lambda: f"exit code {number()}",
+        lambda: f"in {rng.choice(_PROSE_IDENTIFIERS)} at 0x{hexdigits(12)}",
+        lambda: f"commit {hexdigits(40)}",
+        lambda: f"request_id {uuid.UUID(int=rng.getrandbits(128))}",
+        lambda: f"at 2026-09-{rng.randint(10, 28)}T{rng.randint(10, 23)}:{rng.randint(10, 59)}:00+00:00",
+        lambda: f"installed pmcp-1.{rng.randint(0, 30)}.{rng.randint(0, 9)}",
+        lambda: f"Bearer {rng.choice(_PROSE_WORDS).capitalize()}",
+        lambda: f"code={number()}",
+        # a flag right after a keyword is that keyword's value on main (`secret
+        # --bucket`), and the floor keeps main's redaction: the flag follows a
+        # plain verb here
+        lambda: f"run --{rng.choice(_PROSE_WORDS)} {rng.choice(_PROSE_WORDS)}",
+        lambda: f'{{"code": -{rng.randint(32000, 32768)}, "message": "{words()}"}}',
+    ]
+
+    def clause() -> str:
+        text = rng.choice(clauses)()
+        # whatever follows the word `bearer` is its value on main (a
+        # `name=value` clause too: the maintainer's N11 decision), so the
+        # word never ends a clause here
+        return text + " of bad news" if text.lower().endswith("bearer") else text
+
+    return rng.choice([" ", ", ", "; ", ". ", "\n"]).join(
+        clause() for _ in range(rng.randint(3, 6))
+    )
+
+
+def test_property_prose_survives_byte_identical() -> None:
+    """Thousands of generated prose/diagnostic lines survive both surfaces."""
+    rng = random.Random(9234)
+    for _ in range(2000):
+        text = _random_prose(rng)
+        assert _engine(text) == text, text
+        assert _policy(text) == text, text
+
+
+def _declared_secret_keys() -> set[str]:
+    """Every key the redactor DECLARES sensitive, from both surfaces' sources.
+
+    `AUTH_DIAGNOSTIC_SECRET_KEYS`, plus every name in the first alternation
+    group of each `DEFAULT_REDACTION_PATTERNS` entry (`(secret|password|
+    passwd|pwd)`, `(api[_-]?key|apikey)` expanded to `api_key`, `api-key`,
+    `apikey`, ...). A key named anywhere as sensitive but not redacted in every
+    form below fails `test_property_every_declared_key_is_redacted_in_every_form`
+    on its own -- no case has to be hand-picked (board finding on rev 4:
+    `passwd`, `pwd`, `private_key`, `credentials`, `auth` were named or
+    expected and covered by nothing).
+    """
+    keys = set(AUTH_DIAGNOSTIC_SECRET_KEYS)
+    for pattern in _ADDITIVE_DEFAULT_PATTERNS:
+        group = re.search(r"\(([a-z_|\[\]?-]+)\)", pattern)
+        if group is None:
+            continue  # a bare token shape (`sk-`, `ghp_`), not a key
+        for name in group.group(1).split("|"):
+            if "[_-]?" in name:
+                keys.update(name.replace("[_-]?", sep) for sep in ("_", "-", ""))
+            else:
+                keys.add(name)
+    keys.add("private-key")
+    keys.add("privateKey")
+    return keys
+
+
+#: Bare (unquoted) syntaxes cannot carry a quote or a list/query separator
+#: (`,` `;` `&`) in a value; everything else, including leading punctuation
+#: and spaces, is fair.
+_BARE_VALUE_ALPHABET = "".join(c for c in _PRINTABLE if c not in "\"',;&")
+
+
+def _key_forms(key: str, value: str) -> list[tuple[str, str, str]]:
+    """(text, encoded value, probe) for one key: JSON, single-quoted, `key=`,
+    header. The bare forms carry the value's first token with quotes and list
+    separators removed, which is all an unquoted syntax can carry; the probe
+    is the first four encoded characters."""
+    bare_tokens = "".join(c for c in value if c not in "\"',;&").split()
+    # a bare value never ends on a closing bracket or a backslash
+    # a bare value never starts on `[`/`{` nor ends on a closing bracket or a backslash
+    bare = bare_tokens[0].lstrip("[{").rstrip(")]}\\") if bare_tokens else ""
+    forms = [
+        (json.dumps({key: value}), json.dumps(value), json.dumps(value)[1:5]),
+        (
+            "{'" + key + "': " + _single_quoted(value) + "}",
+            _single_quoted(value),
+            _single_quoted(value)[1:5],
+        ),
+    ]
+    if bare and not bare.startswith("="):  # `key==x` is a comparison, not a value
+        forms.append((f"{key}={bare}", bare, bare[:4]))
+        forms.append((f"{key}: {bare}", bare, bare[:4]))
+    return forms
+
+
+def test_property_every_declared_key_is_redacted_in_every_form() -> None:
+    """Every declared key × every form × random values, both surfaces.
+
+    Values are drawn from every printable character (quoted forms) or every
+    printable character but quotes and list separators (bare forms); a third
+    of them start with punctuation and a third contain a space. For a WEAK
+    key (`WEAK_SECRET_KEYS`) a plain word or number is kept by design, so
+    those values are skipped for those keys; for a strong key nothing is
+    skipped. The per-key result table in the plan comes from this loop.
+    """
+    rng = random.Random(5234)
+    checked = 0
+    leaks: list[str] = []
+    for key in sorted(_declared_secret_keys()):
+        for _ in range(60):
+            alphabet = _PRINTABLE if rng.random() < 0.5 else _BARE_VALUE_ALPHABET
+            value = "".join(rng.choice(alphabet) for _ in range(rng.randint(4, 20)))
+            roll = rng.random()
+            if roll < 0.33:
+                value = rng.choice("!#$%&()*+-./:<=>?@[\\]^_`{|}~") + value
+            elif roll < 0.66:
+                value = value[:2] + " " + value[2:]
+            for text, encoded, probe in _key_forms(key, value):
+                if not _probe_is_usable(text, encoded, probe):
+                    continue
+                if (
+                    key.lower() in WEAK_SECRET_KEYS
+                    and _is_plain_word_or_number_for_test(encoded)
+                ):
+                    continue
+                if not encoded.strip("\"'").strip():
+                    continue
+                checked += 1
+                for surface, out in (
+                    ("engine", _engine(text)),
+                    ("policy", _policy(text)),
+                ):
+                    if probe in out:
+                        leaks.append(f"{surface} key={key!r} text={text!r} out={out!r}")
+    assert checked > 5000, checked
+    assert leaks == [], "\n".join(leaks[:20])
+
+
+def _is_plain_word_or_number_for_test(encoded: str) -> bool:
+    """The weak-key exemption, restated: a word (letters/underscores) or a
+    number, quotes and trailing sentence punctuation stripped."""
+    value = encoded.strip("\"'").rstrip(".:!?")
+    return bool(re.fullmatch(r"[A-Za-z_]+|[+-]?[0-9]+|0[xX][0-9a-fA-F]+", value))
+
+
+# === rev 6: the composition property and the regressions it replaces ====== #
+
+
+def test_secrets_inside_a_url_query_are_redacted_whatever_the_key() -> None:
+    """The URL pass yields component spans, never a rewritten URL.
+
+    Rev 5 replaced the whole URL with `redact_auth_url(url)` and dropped every
+    span inside it as "contained" -- so a keyed value or a token under a
+    query key `redact_auth_url` does not know leaked, and main (which ran the
+    keyword rule over the rewritten URL) did better (board finding on rev 5).
+    """
+    for text, expected in [
+        (
+            "https://example.com/?aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
+            "https://example.com/?aws_secret_access_key=[REDACTED]",
+        ),
+        (
+            "https://api.github.com/repos?access=" + TOKEN,
+            "https://api.github.com/repos?access=[REDACTED]",
+        ),
+        (
+            "https://example.com/login?pwd=hunter2",
+            "https://example.com/login?pwd=[REDACTED]",
+        ),
+        (
+            "https://user:pass@auth.example/cb?code=oauth-code&state=ok#access_token=abc.def",
+            "https://auth.example/cb?code=[REDACTED]&state=ok",
+        ),
+        (
+            "https://h.example/?q=4eC39HqLyjWDarjtT1zdp7dc&page=2",
+            "https://h.example/?q=[REDACTED]&page=2",
+        ),
+        # grok/codex, round 4: userinfo stripping is a no-op rewrite for the
+        # query, and `q`/`file` will never be on `AUTH_SECRET_QUERY_KEYS`
+        (
+            "https://alice:hunter2@example.test/cb?q=" + TOKEN,
+            "https://example.test/cb?q=[REDACTED]",
+        ),
+        (
+            "https://example.test/dl?file=sk-live-abc123def456",
+            "https://example.test/dl?file=[REDACTED]",
+        ),
+    ]:
+        assert _engine(text) == expected, text
+        assert _policy(text) == expected, text
+    # an OPERATOR pattern inside a URL path is never suppressed by a built-in
+    manager = PolicyManager()
+    manager._redaction_regexes = [re.compile(r"abcdef")]
+    assert (
+        manager.redact_secrets("https://example.test/abcdef")
+        == "https://example.test/[REDACTED]"
+    )
+    assert _engine("password=https://h.example/?a=1,b=2") == "password=[REDACTED],b=2"
+    assert "h.example" not in _policy("password=https://h.example/?a=1,b=2")
+    # `redact_auth_url` itself is byte-for-byte main's: the elicitation URL
+    assert (
+        redact_auth_url("https://u:p@auth.example/cb?ticket=secret&x=1#frag")
+        == "https://auth.example/cb?ticket=%5BREDACTED%5D&x=1"
+    )
+
+
+def test_a_containing_span_may_drop_an_inner_one_only_if_it_covers_it() -> None:
+    """The merge rule, stated as a property of `apply_redaction_spans`.
+
+    An outer span whose replacement is `[REDACTED]` or empty covers whatever
+    it contains; any other outer replacement is weaker than the inner spans
+    it would suppress (grok's framing), so the two merge to `[REDACTED]`.
+    Rev 5's whole-URL rewrite was exactly such a weaker outer span.
+    """
+    text = "xx SECRET yy"
+    inner = (3, 9, REDACTED)
+    assert apply_redaction_spans(text, [(0, 12, "xx SECRET yy"), inner]) == "[REDACTED]"
+    assert apply_redaction_spans(text, [(0, 12, "rewritten"), inner]) == "[REDACTED]"
+    assert apply_redaction_spans(text, [(0, 12, REDACTED), inner]) == "[REDACTED]"
+    assert apply_redaction_spans(text, [(0, 3, ""), inner]) == "[REDACTED] yy"
+
+
+def test_a_literal_marker_in_the_input_is_not_a_shield() -> None:
+    """A downstream server can write `[REDACTED]` itself; that must not disarm
+    the rule next to it. Rev 5 dropped every span touching an existing marker
+    (board finding: `password=hunter2[REDACTED]` survived, main redacted it).
+    Existing markers are spans and merge with whatever touches them; a marker
+    nothing touches is replaced by itself, so the surfaces stay idempotent.
+    """
+    for text, expected in [
+        ("password=hunter2[REDACTED]", "password=[REDACTED]"),
+        ("api_key=abc123def456[REDACTED]", "api_key=[REDACTED]"),
+        ('{"password": "hunter2 [REDACTED]"}', '{"password": "[REDACTED]"}'),
+        ('{"password": "[REDACTED] hunter2"}', '{"password": "[REDACTED]"}'),
+        ("token=[REDACTED]abc123def456 tail", "token=[REDACTED] tail"),
+        ("password=[REDACTED]", "password=[REDACTED]"),
+        ('{"password": [REDACTED]}', '{"password": [REDACTED]}'),
+        (
+            "[REDACTED] [REDACTED_EMAIL] ghp_ sk-",
+            "[REDACTED] [REDACTED_EMAIL] ghp_ sk-",
+        ),
+    ]:
+        assert _engine(text) == expected, text
+        assert "hunter2" not in _policy(text) and "abc123def456" not in _policy(text), (
+            text
+        )
+    assert _policy("password= [REDACTED]") == _policy(_policy("password= [REDACTED]"))
+
+
+def test_authorization_is_a_header_not_a_word() -> None:
+    """`authorization: none` is prose; `if token == expected` is a comparison."""
+    assert _engine("authorization: none") == "authorization: none"
+    assert (
+        _engine("Set the Authorization:\nheader first")
+        == "Set the Authorization:\nheader first"
+    )
+    assert _engine("Authorization: Bearer abc.def") == "Authorization: [REDACTED]"
+    assert _engine("Authorization=Basic dXNlcjpwYXNz") == "Authorization=[REDACTED]"
+    assert _engine("if token == expected:") == "if token == expected:"
+    assert (
+        _engine("token = await self._get_token()")
+        == "token = [REDACTED] self._get_token()"
+    )
+
+
+def _span_texts(text: str, spans: list[tuple[int, int, str]]) -> list[str]:
+    """The source text of every covering span (replacement `[REDACTED]` or
+    empty) of four or more characters that occurs exactly once in ``text``,
+    so its absence from the output is unambiguous."""
+    return [
+        text[start:end]
+        for start, end, replacement in spans
+        if replacement in (REDACTED, "")
+        and end - start >= 4
+        # a literal marker in the input is replaced by the marker
+        and text[start:end] != REDACTED
+        and text.count(text[start:end]) == 1
+    ]
+
+
+_URL_SAFE = string.ascii_letters + string.digits + "-_.~%+"
+_NON_SECRET_QUERY_KEYS = ("q", "page", "access", "u", "foo", "next", "redirect")
+
+
+def _query_string_texts(rng: random.Random) -> list[str]:
+    """Secrets in URL query strings under secret AND non-secret keys, with a
+    keyed value around the URL sometimes."""
+    texts = []
+    for _ in range(400):
+        secret = "".join(
+            rng.choice(string.ascii_letters + string.digits) for _ in range(24)
+        )
+        key = rng.choice([*sorted(AUTH_SECRET_QUERY_KEYS), *_NON_SECRET_QUERY_KEYS])
+        extra = "".join(rng.choice(_URL_SAFE) for _ in range(rng.randint(0, 8)))
+        url = f"https://h.example/p/{extra}?{key}={secret}&page=2"
+        if rng.random() < 0.3:
+            url = f"https://u:{secret[:8]}@h.example/cb?{key}={secret}#frag"
+        texts.append(
+            rng.choice([url, f"see {url}.", f"password={url}", f'{{"note": "{url}"}}'])
+        )
+    return texts
+
+
+def _marker_texts(rng: random.Random) -> list[str]:
+    """Secrets next to, and inside quotes with, a literal `[REDACTED]`."""
+    texts = []
+    keys = ("password", "api_key", "token", "secret", "pwd")
+    for _ in range(400):
+        secret = "".join(
+            rng.choice(string.ascii_letters + string.digits)
+            for _ in range(rng.randint(6, 16))
+        )
+        key = rng.choice(keys)
+        texts.append(
+            rng.choice(
+                [
+                    f"{key}={secret}[REDACTED]",
+                    f"{key}=[REDACTED]{secret}",
+                    f'{{"{key}": "{secret} [REDACTED]"}}',
+                    f'{{"{key}": "[REDACTED] {secret}"}}',
+                    f"[REDACTED] {key}: {secret} [REDACTED]",
+                ]
+            )
+        )
+    return texts
+
+
+def test_property_every_collected_span_is_gone_from_the_output() -> None:
+    """For every span the redactor itself collected, the span's text is absent
+    from the output -- on both surfaces, over the static corpora and over
+    generators that put secrets inside URL query strings under secret and
+    non-secret keys, and next to literal `[REDACTED]` markers. This is the
+    property the containment rule and the marker rule broke on rev 5.
+    """
+    rng = random.Random(6234)
+    texts = [entry[1] for entry in CREDENTIALS]
+    for password in _random_passwords(rng, 500):
+        texts.extend(text for text, _, _ in _keyed_forms(password))
+    texts.extend(_query_string_texts(rng))
+    texts.extend(_marker_texts(rng))
+    policy = PolicyManager()
+    checked = 0
+    leaks: list[str] = []
+    for text in texts:
+        for surface, spans, out in (
+            ("engine", collect_redaction_spans(text), _engine(text)),
+            ("policy", policy.redaction_spans(text), policy.redact_secrets(text)),
+        ):
+            for piece in _span_texts(text, spans):
+                checked += 1
+                if piece in out:
+                    leaks.append(f"{surface} {text!r} -> {out!r} still has {piece!r}")
+    assert checked > 4000, checked
+    assert leaks == [], "\n".join(leaks[:20])
+
+
+def test_property_the_window_edge_never_emits_an_unredacted_value() -> None:
+    """Nothing from past the cap is emitted except inside a span.
+
+    A prefix the redactor shrinks (one oversized PEM block) put the window's
+    far edge -- a second, unredacted cut -- into the returned text on rev 5:
+    with the value straddling `max_bytes + 16384`, its head was emitted. The
+    sweep asserts that some cuts straddle the edge.
+    """
+    policy = PolicyManager()
+    max_bytes = 1000
+    edge = max_bytes + 16384
+    pem = (
+        "-----BEGIN PRIVATE KEY-----" + "x" * (edge - 200) + "-----END PRIVATE KEY-----"
+    )
+    straddling = 0
+    for pad in range(120, 200):
+        text = pem + "\n" * pad + '{"password": "hunter2 tail"}' + "c" * 100
+        start = text.index('"hunter2')
+        end = start + len('"hunter2 tail"')
+        straddling += start < edge < end
+        result = policy.process_output(text, redact=True, max_bytes=max_bytes)
+        assert "hunter2" not in result["result"], (pad, result["result"][-120:])
+        assert len(result["result"].encode("utf-8")) <= max_bytes, pad
+    assert straddling > 0
+
+
+def test_the_window_edge_is_not_emitted_when_ordinary_values_shrink_it() -> None:
+    """Codex's construction: no PEM needed. Two 8 300-char `token=` values fill
+    the 16 684-char window with 70 chars of an unterminated JSON object at
+    its far edge; redaction shrinks the window to ~100 chars, and rev 5
+    returned all of it, `hunter2` included. Every value is under 16 KiB, so
+    this is not the documented residual. Now only the cap, extended to the
+    end of the span that straddles it, is kept.
+    """
+    text = ("token=" + "a" * 8300 + "\n") * 2
+    text += '{"password": "hunter2 ' + "tail " * 20 + '"}'
+    result = PolicyManager().process_output(text, redact=True, max_bytes=300)
+    assert result["truncated"] is True
+    assert "hunter2" not in result["result"], result["result"]
+    assert result["result"].startswith("token=[REDACTED]\n")
+    assert len(result["result"].encode("utf-8")) <= 300
+
+
+#: (input, secrets main removes on at least one surface). Built by running
+#: main @ 860636a's engine and policy over these inputs and recording every
+#: token-character piece (3+ chars) that disappeared, minus main's collateral
+#: (`next`, `home`: main's keyword rule ate the whole query tail; `access_token`:
+#: a key name in a dropped fragment). Every one must still disappear here.
+_MAIN_REDACTS: list[tuple[str, list[str]]] = [
+    (
+        "https://example.com/?aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
+        ["wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"],
+    ),
+    (
+        "https://api.github.com/repos?access=ghp_16C7e42F292c6912E7710c838347Ae178B4a",
+        ["ghp_16C7e42F292c6912E7710c838347Ae178B4a"],
+    ),
+    ("https://example.com/login?pwd=hunter2", ["hunter2"]),
+    ("https://example.com/login?password=hunter2&next=/home", ["hunter2"]),
+    ("https://auth.example/cb?code=oauth-code&state=ok", ["oauth-code"]),
+    (
+        "https://auth.example/cb?bearer=secret-bearer&token=access-token",
+        ["access-token", "secret-bearer"],
+    ),
+    (
+        "https://auth.example/cb?jwt=eyJhbGciOiJIUzI1NiJ9.payload.sig",
+        ["eyJhbGciOiJIUzI1NiJ9.payload.sig"],
+    ),
+    (
+        "https://auth.example/cb?assertion=saml-secret&ticket=ticket-secret",
+        ["saml-secret", "ticket-secret"],
+    ),
+    (
+        "https://user:pass@auth.example/cb?code=oauth-code",
+        ["oauth-code", "pass", "user"],
+    ),
+    ("https://auth.example/cb#access_token=abc.def.ghi", ["abc.def.ghi"]),
+    ("password=hunter2", ["hunter2"]),
+    ("password: hunter2", ["hunter2"]),
+    ("api_key=abc123def456", ["abc123def456"]),
+    ("secret=s3cr3t", ["s3cr3t"]),
+    ("client_secret=abc", ["abc"]),
+    ("access_token=AbC123dEf456GhI", ["AbC123dEf456GhI"]),
+    ("refresh_token=xyz789xyz789", ["xyz789xyz789"]),
+    ("authorization: Bearer abc.def", ["Bearer", "abc.def"]),
+    ("Authorization=Basic dXNlcjpwYXNz", ["Basic"]),
+    ("Bearer abc123def456", ["abc123def456"]),
+    ("sk-live-abc123def456", ["sk-live-abc123def456"]),
+    (
+        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
+        ["ghp_16C7e42F292c6912E7710c838347Ae178B4a"],
+    ),
+    (
+        "github_pat_11ABCDEFG0abcdefghijklmnop_Ab3dEf6GhI9jKl2",
+        ["github_pat_11ABCDEFG0abcdefghijklmnop_Ab3dEf6GhI9jKl2"],
+    ),
+    ("set-cookie: session=abc123; Path=/", ["abc123", "session"]),
+    ("cookie: sid=abc123def", ["abc123def", "sid"]),
+    ("sid=abc123def456", ["abc123def456"]),
+    ("session=013G8iK4noj1iNbSTqJVFEX6", ["013G8iK4noj1iNbSTqJVFEX6"]),
+    (
+        "id_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
+        ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"],
+    ),
+    ("saml=PHNhbWw6QXNzZXJ0aW9u", ["PHNhbWw6QXNzZXJ0aW9u"]),
+    ("assertion=abc123def456", ["abc123def456"]),
+    ("tenant_id=acme-123", ["acme-123"]),
+    ("tenant-id: acme-123", ["acme-123"]),
+    ("aws_secret=wJalrXUtnFEMI", ["wJalrXUtnFEMI"]),
+    ("aws_access=AKIAIOSFODNN7EXAMPLE", ["AKIAIOSFODNN7EXAMPLE"]),
+    ("passwd=hunter2", ["hunter2"]),
+    ("pwd=hunter2", ["hunter2"]),
+    ("apikey: abc123def456", ["abc123def456"]),
+    ("X-Api-Key: abc123def456", ["abc123def456"]),
+    (
+        "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
+        ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"],
+    ),
+    ("--token abc123def456 --password hunter2", ["abc123def456", "hunter2"]),
+    ("token: abc123def456", ["abc123def456"]),
+    ("auth_code=SplxlOBeZQQYbYS6WxSbIA", ["SplxlOBeZQQYbYS6WxSbIA"]),
+]
+
+
+@pytest.mark.parametrize(
+    ("text", "secrets"), _MAIN_REDACTS, ids=[t[:40] for t, _ in _MAIN_REDACTS]
+)
+def test_never_worse_than_main(text: str, secrets: list[str]) -> None:
+    for secret in secrets:
+        assert secret not in _engine(text), (secret, _engine(text))
+        assert secret not in _policy(text), (secret, _policy(text))
+
+
+# === rev 7: the differential against main is THE never-worse test ========= #
+
+
+def test_quoted_bearer_and_httpie_and_ruby_separators() -> None:
+    """Rev 6 regressions against main, each a rule narrowed for a false
+    positive without re-running the differential (board round 5).
+
+    A quote before `Bearer` is how every JSON-serialised string arrives;
+    `:=`/`==` are httpie's syntax; `=>` is Ruby's. `if token == expected:`
+    (the false positive the narrowing was for) still survives.
+    """
+    for text, expected in [
+        ('{"text": "Bearer test-token"}', '{"text": "Bearer [REDACTED]"}'),
+        ("'Bearer abc123def456'", "'Bearer [REDACTED]'"),
+        ('"Bearer abc123def456"', '"Bearer [REDACTED]"'),
+        ("password:=hunter2", "password:=[REDACTED]"),
+        ("token:=abc123def456", "token:=[REDACTED]"),
+        ("password==hunter2", "password==[REDACTED]"),
+        ('{"password"=>"hunter2"}', '{"password"=>"[REDACTED]"}'),
+        ("if token == expected:", "if token == expected:"),
+        ("token_type=Bearer expires_in=3600", "token_type=Bearer [REDACTED]"),
+    ]:
+        assert _engine(text) == expected, text
+        assert "hunter2" not in _policy(text) and "abc123def456" not in _policy(text), (
+            text
+        )
+    assert PolicyManager().process_output({"text": "Bearer test-token"}, redact=True)[
+        "result"
+    ] == {"text": "Bearer [REDACTED]"}
+
+
+def test_percent_encoded_query_values_are_decoded_before_the_detectors() -> None:
+    encoded = "".join(f"%{ord(c):02X}" for c in "ghp_") + TOKEN[4:]
+    assert (
+        _engine(f"https://h.example/?q={encoded}") == "https://h.example/?q=[REDACTED]"
+    )
+    assert (
+        _policy(f"https://h.example/?q={encoded}") == "https://h.example/?q=[REDACTED]"
+    )
+    assert (
+        _engine("https://h.example/?q=%20hello%20world")
+        == "https://h.example/?q=%20hello%20world"
+    )
+
+
+def test_a_value_may_sit_on_the_next_indented_line() -> None:
+    """YAML block style, pretty-printed JSON and a folded header are formats;
+    a keyword at the end of a sentence followed by an unindented line, or by
+    a bullet, is prose."""
+    assert _engine('"password":\n    "hunter2"') == '"password":\n    "[REDACTED]"'
+    assert _engine("password:\n  hunter2") == "password:\n  [REDACTED]"
+    assert _engine("[x-api-key\n  abc123def456]") == "[x-api-key\n  [REDACTED]]"
+    assert (
+        _engine("Missing bearer token:\nthe bearer of")
+        == "Missing bearer token:\nthe bearer of"
+    )
+    assert _engine("token:\n  - a bullet") == "token:\n  - a bullet"
+
+
+# --- the differential ------------------------------------------------------- #
+
+_DIFF_KEYS = [
+    "password",
+    "passwd",
+    "pwd",
+    "secret",
+    "token",
+    "access_token",
+    "refresh_token",
+    "api_key",
+    "apikey",
+    "client_secret",
+    "session",
+    "sid",
+    "cookie",
+    "set-cookie",
+    "authorization",
+    "bearer",
+    "jwt",
+    "saml",
+    "assertion",
+    "id_token",
+    "aws_secret",
+    "aws_access",
+    "tenant_id",
+    "code",
+    "auth_code",
+    "x-api-key",
+    "private_key",
+    "credentials",
+    # rev 9 (B3): dotted keys -- a config path or an attribute
+    "db.password",
+    "spring.datasource.password",
+    "self.password",
+    "config.api_key",
+    # rev 9 (B4): PascalCase and camelCase keys
+    "AccessToken",
+    "ClientSecret",
+    "SessionToken",
+    "DbPassword",
+    "clientSecret",
+    "sessionToken",
+    "passwordHash",
+    "apiKey",
+    # rev 9 (N1): suffixed keys -- credential suffixes and descriptive ones
+    "password2",
+    "password_confirmation",
+    "secret_value",
+    "token_id",
+    "SECRET_KEY",
+    "token_type",
+    "password_length",
+    "token_endpoint",
+    "secret_arn",
+    # rev 9 (N1): command-line flags
+    "--token",
+    "--password",
+    "--clientSecret",
+    "--sessionToken",
+    "--token_id",
+]
+_DIFF_VALUES = [
+    "hunter2",
+    "abc123def456",
+    "s3cr3t",
+    "correct-horse-battery-staple",
+    "4eC39HqLyjWDarjtT1zdp7dc",
+    "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
+    "sk-live-abc123def456",
+    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.N2QwODhm",
+    "AKIAIOSFODNN7EXAMPLE",
+    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
+    "dXNlcjpwYXNzd29yZA==",
+    "Xk9#mQ2vL",
+    "p@ss w0rd",
+    "abc",
+    "12345678",
+    "letters",
+    "a.b.c",
+    "x-y-z",
+    "https://h.example/?t=1",
+    "(paren)",
+    "tok[1]",
+    "🙂ß",
+    # rev 9 (B1): JSON literals -- after a quoted key they are JSON, not text
+    "null",
+    "true",
+    "false",
+    "12345",
+    "-1.5e3",
+    # rev 9 (N3): values starting on a JSON delimiter
+    ",hunter22",
+    "}hunter22",
+    ", s3cr3tval",
+]
+_DIFF_SEPS = [
+    "=",
+    ": ",
+    ":",
+    " = ",
+    "=>",
+    " => ",
+    "->",
+    ":=",
+    "\n  ",
+    ":\n  ",
+    "\t",
+    " ",
+    "  ",
+    '="{}"',
+    "='{}'",
+    ': "{}"',
+    '": "{}"',
+    '\\": \\"{}\\"',
+    " is ",
+    "|",
+    # rev 8 (grok): CRLF header folding and Windows dumps, a no-break space,
+    # and an unindented CRLF continuation -- every whitespace class a rule touches
+    ":\r\n  ",
+    "\r\n  ",
+    ":\xa0",
+    ":\r\n",
+    # rev 9 (G1): a spaced `==`, and a quoted key before a bare value (B1)
+    " == ",
+    "== ",
+    " ==",
+    '": ',
+]
+#: rev 9 (B1): the JSON literals among `_DIFF_VALUES`, and the wraps that
+#: take the pair as an object member.
+_DIFF_JSON_LITERALS = ["null", "true", "false", "12345", "-1.5e3"]
+_DIFF_JSON_MEMBER_WRAPS = [
+    "{{{}}}",
+    '{{"id": 7, {}, "ok": true, "n": null}}',
+    '{{"outer": {{"inner": {{{}}}, "n": 1.5}}}}',
+    '{{"items": [{{"id": 1, {}}}, {{"id": 2, "name": "prod"}}]}}',
+    '[{{{}}}, {{"id": 2, "tags": ["a", "b"]}}]',
+]
+#: rev 9 (B1): JSON scalars as structured values in the dict corpus.
+_DIFF_JSON_SCALARS: list[object] = [None, True, False, 42, -1.5, 0]
+#: rev 9 (B5): every character `str.isspace()` accepts except `\r` and `\n`
+#: (pinned against a full enumeration in the axis test), as the whole
+#: separator and after a colon.
+_DIFF_SPACE_CHARS = (
+    "\t\x0b\x0c\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003"
+    "\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
+)
+_DIFF_SEPS += [
+    sep for ch in _DIFF_SPACE_CHARS for sep in (ch, ":" + ch) if sep not in _DIFF_SEPS
+]
+_DIFF_WRAPS = [
+    "{}",
+    "{} tail",
+    "prefix {}",
+    "{{{}}}",
+    "[{}]",
+    "({})",
+    '"{}"',
+    "'{}'",
+    '{{"a": "{}"}}',
+    "<x>{}</x>",
+    "-- {} --",
+    "https://h.example/?{}",
+    "https://h.example/?x=1&{}&y=2",
+    "https://h.example/#{}",
+    "https://u:p@h.example/{}",
+    "curl -H '{}' https://h.example",
+    "export {}",
+    "log: {}",
+    "{}\n{}",
+    "{}; {}",
+    # rev 9 (B1, B2, N2, N3): JSON documents -- mixed value types, the pair
+    # as a member, nested objects, lists of objects
+    '{{"id": 7, "ok": true, "n": null, "v": "{}"}}',
+    '{{"id": 7, {}, "ok": true, "n": null}}',
+    '{{"outer": {{"inner": {{{}}}, "n": 1.5}}}}',
+    '{{"items": [{{"id": 1, {}}}, {{"id": 2, "name": "prod"}}]}}',
+    '[{{{}}}, {{"id": 2, "tags": ["a", "b"]}}]',
+]
+_DIFF_TOKEN = re.compile(r"[A-Za-z0-9_.+/-]{3,}")
+
+
+def _differential_corpus() -> list[tuple[str, str, str, str, str]]:
+    """The board's differential corpus, widened in rev 9 (seed 20260923,
+    8 000 inputs): keys × values × separators × wraps, one random draw per
+    axis per row, the key upper- or title-cased 30% of the time, then two
+    rows per key holding a JSON literal as a quoted member of a JSON wrap. The oracle
+    fixture is recorded from `main` @ 860636a over exactly these rows.
+
+    Axes (each asserted present by
+    `test_the_differential_corpus_covers_every_axis`):
+
+    * keys -- plain, snake, kebab and header names; dotted (`db.password`);
+      PascalCase and camelCase (`AccessToken`, `clientSecret`); suffixed,
+      credential (`password2`, `secret_value`) and descriptive (`token_type`,
+      `secret_arn`); `--flag` forms;
+    * separators -- `=`, `:`, `=>`, `:=`, quoted forms, a quoted key before a
+      bare value, prose non-separators, CRLF and LF continuations, spaced
+      `==`, and every `str.isspace()` character but `\\r`/`\\n`, alone and
+      after `:`;
+    * values -- credential-shaped, plain words and numbers, punctuation,
+      URLs, non-ASCII, JSON literals (`null`, `true`, `false`, numbers) and
+      values starting on `,` or `}`;
+    * wraps -- prose, brackets, quotes, markup, URLs (query, fragment,
+      userinfo), shell, repeated pairs, and JSON documents with mixed value
+      types, the pair as a member, nested objects and lists of objects.
+    """
+    rng = random.Random(2026_09_23)
+    out = []
+    for _ in range(8000 - 2 * len(_DIFF_KEYS)):
+        key = rng.choice(_DIFF_KEYS)
+        if rng.random() < 0.3:
+            key = key.upper() if rng.random() < 0.5 else key.title()
+        value = rng.choice(_DIFF_VALUES)
+        sep = rng.choice(_DIFF_SEPS)
+        kv = f"{key}{sep.format(value)}" if "{}" in sep else f"{key}{sep}{value}"
+        if sep.startswith('"'):
+            kv = f'"{kv}'  # the key's opening quote
+        wrap = rng.choice(_DIFF_WRAPS)
+        text = wrap.format(kv, kv) if wrap.count("{}") == 2 else wrap.format(kv)
+        out.append((text, key, sep, value, wrap))
+    # B1's axis, which random draws almost never land in a valid document:
+    # each key, quoted, as a JSON member holding a literal -- once bare after
+    # the key (`"token": null`), once as a quoted string (`"token": "null"`)
+    for key in _DIFF_KEYS:
+        for sep in ('": ', '": "{}"'):
+            value = rng.choice(_DIFF_JSON_LITERALS)
+            wrap = rng.choice(_DIFF_JSON_MEMBER_WRAPS)
+            kv = f'"{key}{sep.format(value)}' if "{}" in sep else f'"{key}{sep}{value}'
+            out.append((wrap.format(kv), key, sep, value, wrap))
+    return out
+
+
+def _main_oracle() -> dict[str, Any]:
+    """`main`'s recorded behaviour (auth.py/policy.py unchanged from 860636a to
+    9ca081e): per grammar-corpus row what survives on each surface
+    (`grammar`, `_redaction_grammar.observe` codes over `corpus(2)`, whose
+    prefix is tier 1) and the corpus digest it was recorded over
+    (`grammar_fingerprint`); per dict-corpus row the pieces `process_output`
+    removed from the serialised result (`dict`) and the result's type
+    (`dict_types`); per fuzz object the `process_output` result type
+    (`fuzz_types`). Recorded by `tests/fixtures/regen_redaction_main_oracle.py`."""
+    blob = (
+        Path(__file__).parent / "fixtures" / "redaction_main_oracle.b64"
+    ).read_text()
+    return json.loads(gzip.decompress(base64.b64decode(blob)).decode("utf-8"))
+
+
+#: Words main removed as collateral (its `[\s:=]+` rule ate the word after a
+#: keyword, its URL rewrite dropped userinfo, its Bearer rule ate the scheme
+#: word, its policy default ate the key name itself): never a secret, never
+#: counted.
+_DIFF_COLLATERAL = frozenset(
+    {
+        "bearer",
+        "basic",
+        "token",
+        "tail",
+        "prefix",
+        "export",
+        "curl",
+        "example",
+        "h.example",
+        "https",
+        "http",
+    }
+)
+
+
+# === rev 10: the grammar-derived differential ============================= #
+#
+# The never-worse-than-main differential is derived from the ORACLE's grammar
+# (main's rules plus what `json.dumps` emits), not from past findings:
+# `tests/_redaction_grammar.py` holds the generator (its docstring has every
+# axis and the sampling density), the observation of each surface and the
+# accepted-regression classes. Tier 1 runs here; the full set (tier 2) is
+# `-m slow`.
+
+
+def _grammar_differential(
+    rows: list[G.Row], main_codes: list[str]
+) -> tuple[dict[str, int], list[str], int]:
+    """Per (row, surface) this redactor is worse on than main: its accepted
+    class (decided from the row as written and the surface, never from the
+    output), or a bug. Also counts the positive control: rows on which main
+    removed a piece on some surface."""
+    policy = PolicyManager()
+
+    def process(obj: object) -> object:
+        return policy.process_output(obj, redact=True, max_bytes=G.BIG)["result"]
+
+    counts = dict.fromkeys(G.CLASSES, 0)
+    bugs: list[str] = []
+    controls = 0
+    for row, main in zip(rows, main_codes, strict=True):
+        assert len(main) == (2 if "o" in row else len(G.TEXT_SURFACES) + 1), main
+        here = G.observe(row, _engine, policy.redact_secrets, process)
+        controls += bool(row["value"]) and G.removed_by_main(row, main)
+        for surface in G.worse_surfaces(row, main, here):
+            cls = (
+                G.accepted(row["f"], surface)
+                if row["f"] is not None and not surface.endswith(".type")
+                else None
+            )
+            if cls is None:
+                shown = row.get("t", row.get("o"))
+                bugs.append(f"{surface}: {shown!r} main={main} here={here}")
+            else:
+                counts[cls] += 1
+    return counts, bugs, controls
+
+
+def test_grammar_differential_never_worse_than_main_except_by_stated_class() -> None:
+    """Tier 1 (12 535 rows x 12 surfaces, plus the result type of every
+    dict): every piece of the value main removed on a surface is removed here
+    too, and every dict main kept stays a dict, unless the (row, surface) is
+    in a stated class. The oracle is main's recorded output over exactly this
+    corpus (the fingerprint)."""
+    rows = G.corpus(1)
+    oracle = _main_oracle()
+    assert oracle["grammar_fingerprint"]["1"] == G.fingerprint(rows)
+    assert len(rows) == 12_535 and len(oracle["grammar"]) == 166_183
+    counts, bugs, controls = _grammar_differential(rows, oracle["grammar"][: len(rows)])
+    assert bugs == [], f"{len(bugs)} unaccepted:\n" + "\n".join(bugs[:25])
+    text_rows = sum(1 for row in rows if row["value"])
+    # the oracle is not vacuous: main removes the value on most rows, and the
+    # classes are exercised (each class's count is in the plan)
+    assert controls > 0.85 * text_rows, (controls, text_rows)
+    assert sum(counts.values()) > 10_000, counts
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("block", G.BLOCKS)
+def test_grammar_differential_full_set(block: str) -> None:
+    """Tier 2, the full set (166 183 rows), one block per case so a failure
+    is attributed to an axis and each case stays well inside the per-test
+    timeout."""
+    rows = G.corpus(2)
+    oracle = _main_oracle()
+    assert oracle["grammar_fingerprint"]["2"] == G.fingerprint(rows)
+    assert len(rows) == len(oracle["grammar"]) == 166_183
+    picked = [(r, c) for r, c in zip(rows, oracle["grammar"]) if r["block"] == block]
+    assert picked, block
+    counts, bugs, controls = _grammar_differential(
+        [r for r, _ in picked], [c for _, c in picked]
+    )
+    assert bugs == [], f"{len(bugs)} unaccepted:\n" + "\n".join(bugs[:25])
+    if block != "scalar":
+        assert controls > 0.5 * len(picked), (controls, len(picked))
+
+
+def test_the_grammar_corpus_covers_every_axis() -> None:
+    """Each axis the generator's docstring claims is really produced (a check
+    is only a check if it would fail were the axis missing)."""
+    spaces = {chr(i) for i in range(0x110000) if chr(i).isspace()}
+    assert set(G.SPACES) == spaces and len(G.SPACES) == len(spaces) == 29
+    rows = G.corpus(1)
+    by_block: dict[str, list[G.Row]] = {}
+    for row in rows:
+        by_block.setdefault(row["block"], []).append(row)
+    assert set(by_block) == set(G.BLOCKS)
+    # every 1- and 2-character separator over `\s` + `:` + `=`
+    seps = {row["f"]["sep"] for row in by_block["sep"]}
+    assert len(G.PAIRS) == 31 + 31 * 31 and set(G.PAIRS) <= seps
+    # every pre character, and every JSON escape the serialiser emits
+    pres = {row["f"]["pre"] for row in by_block["pre"]}
+    assert {ch for chars in G.PRE_CHARS.values() for ch in chars} <= pres
+    dumped = "".join(json.dumps(row["t"]) for row in by_block["pre"])
+    for escape in (
+        '\\"',
+        "\\\\",
+        "\\b",
+        "\\f",
+        "\\n",
+        "\\r",
+        "\\t",
+        "\\u0000",
+        "\\u001b",
+    ):
+        assert escape in dumped, escape
+    for escape in (
+        "\\u007f",
+        "\\u0085",
+        "\\u00a0",
+        "\\u3000",
+        "\\u00e9",
+        "\\ud83d\\ude42",
+    ):
+        assert escape in dumped, escape
+    buckets = {row["bucket"] for row in rows}
+    # `code` under every diagnostic and credential qualifier, both values
+    code_quals = {row["f"]["qual"][:-1] for row in by_block["code"]}
+    diagnostic = set(G.DIAGNOSTIC_CODE_QUALIFIERS)
+    assert code_quals == diagnostic | set(G.CREDENTIAL_CODE_QUALIFIERS)
+    assert not set(G.CREDENTIAL_CODE_QUALIFIERS) & diagnostic
+    # every part of main's URL grammar
+    url_kinds = {k for row in by_block["focus:url"] for k in row["kinds"]}
+    for key_kind in ("secret", "secret-encoded", "other"):
+        for value_kind in ("cred", "plain", "encoded", "empty"):
+            assert f"query-{key_kind}-{value_kind}" in url_kinds, (key_kind, value_kind)
+    assert {"userinfo", "fragment"} <= url_kinds
+    urls = " ".join(row["t"] for row in by_block["focus:url"])
+    assert (
+        ":99999" in urls and "[::1]" in urls and "://:" in urls and "://user:" in urls
+    )
+    for kinds in (
+        [f"pre:{c}" for c in G.PRE_CHARS],
+        ["qual:joined", "qual:glued", "qual:glued-long", "qual:leading-joiner"],
+        ["qual:multi-segment", "name:case", "name:code-qualified"],
+        ["name:code-diagnostic", "name:code-credential", "wrap:none", "wrap:prose"],
+        ["wrap:url-path", "wrap:url-query-pair", "wrap:url-query-value"],
+        ["wrap:url-fragment", "code:diagnostic", "code:credential"],
+        ["suffix:declared", "suffix:trailing-joiner", "suffix:descriptive"],
+        ["suffix:random", "suffix:long"],
+        ["sep:line-break", "sep:operator-run", "sep:whitespace-only", "sep:mixed"],
+        ["value:main-class", "value:plain", "value:number", "value:quoted"],
+        ["value:unterminated-quote", "value:bracketed", "value:json-literal"],
+        ["value:punctuated", "bearer:after-:/=", "bearer:line-break"],
+        ["bearer:wrapped-value", "bearer:plain-context"],
+        ["authorization:line-break", "authorization:wrapped-value"],
+        ["authorization:scheme", "authorization:plain", "mix"],
+        [f"scalar:{name}" for name in G.SCALARS],
+    ):
+        assert set(kinds) <= buckets, set(kinds) - buckets
+    # every scalar under every key in every shape; every case of the key word
+    assert len(by_block["scalar"]) == len(G.SCALARS) * len(G.SCALAR_KEYS) * 3
+    names = {row["f"]["name"] for row in by_block["focus:name"]}
+    assert any(n.isupper() for n in names) and any(n.istitle() for n in names)
+    assert any(not n.isupper() and not n.islower() and not n.istitle() for n in names)
+    # the observation covers every surface, and each class is decidable
+    code = G.observe(rows[0], _engine, _policy, lambda obj: obj)
+    assert len(code) == len(G.TEXT_SURFACES) + 1 == 13
+    assert set(G.SERIALISED) >= {"POd", "EjAC", "EjUI", "PjAC", "PjUI"}
+
+
+def _dict_corpus() -> list[tuple[dict, str, str, str]]:
+    """Structured results (seed 20260924, 800), widened in rev 9. Shapes: a
+    JSON text leaf, a header leaf, a prose leaf with a URL, a top-level key,
+    a nested key, a list of leaves, and (rev 9) mixed value types, a
+    three-deep nested object and a list of objects. Keys and string values
+    come from the string corpus's axes; a quarter of the values are JSON
+    scalars (`None`, `True`, `False`, an int, a float) instead. The last
+    element is the value as the text shapes spell it."""
+    rng = random.Random(2026_09_24)
+    out: list[tuple[dict, str, str, str]] = []
+    for _ in range(800):
+        key = rng.choice(_DIFF_KEYS)
+        value: object = (
+            rng.choice(_DIFF_JSON_SCALARS)
+            if rng.random() < 0.25
+            else rng.choice(_DIFF_VALUES)
+        )
+        spelled = value if isinstance(value, str) else json.dumps(value)
+        sep = rng.choice([": ", "=", ': "{}"', " == ", ":\u3000"])
+        kv = f"{key}{sep.format(spelled)}" if "{}" in sep else f"{key}{sep}{spelled}"
+        shape = rng.choice(
+            [
+                "leaf-json",
+                "leaf-header",
+                "leaf-text",
+                "top-key",
+                "nested",
+                "list",
+                "mixed",
+                "deep",
+                "list-of-objects",
+            ]
+        )
+        if shape == "leaf-json":
+            obj: dict = {
+                "content": [{"type": "text", "text": json.dumps({key: value})}],
+                "isError": rng.random() < 0.5,
+            }
+        elif shape == "leaf-header":
+            obj = {
+                "content": [
+                    {"type": "text", "text": f"HTTP/1.1 401\r\n{key}: {spelled}\r\n"}
+                ]
+            }
+        elif shape == "leaf-text":
+            obj = {
+                "content": [
+                    {
+                        "type": "text",
+                        "text": f"error: {kv} while calling https://h.example/?{key}={spelled}",
+                    }
+                ]
+            }
+        elif shape == "top-key":
+            obj = {key: value, "note": "ok"}
+        elif shape == "nested":
+            obj = {"result": {"auth": {key: value}, "items": [1, 2]}}
+        elif shape == "list":
+            obj = {
+                "content": [
+                    {"type": "text", "text": spelled},
+                    {"type": "text", "text": kv},
+                ]
+            }
+        elif shape == "mixed":
+            obj = {"id": 7, "ok": True, key: value, "n": None, "ratio": 0.5}
+        elif shape == "deep":
+            obj = {"outer": {"inner": {"leaf": {key: value}}, "n": 1.5}}
+        else:
+            obj = {
+                "items": [
+                    {"id": 1, key: value},
+                    {"id": 2, "name": "prod", "tags": ["a", "b"]},
+                ]
+            }
+        out.append((obj, key, sep, spelled))
+    return out
+
+
+#: The key words a `_DIFF_KEYS` key is made of, for the structured
+#: differential's classifier (which reads a row as `qual + name + suffix`).
+_KEY_WORDS = sorted(
+    {*AUTH_DIAGNOSTIC_SECRET_KEYS, "api_key", "api-key", "apikey", "private_key"}
+    | {"authorization", "bearer"}
+)
+
+
+def _grammar_fields(key: str, sep: str, value: str) -> dict[str, str]:
+    """A structured-corpus row as the grammar classifier's fields: the key
+    word is the one that ends last in the key (the longest on a tie)."""
+    low = key.lower()
+    end, length, start = max(
+        (low.rfind(w) + len(w), len(w), low.rfind(w)) for w in _KEY_WORDS if w in low
+    )
+    before, _, after = sep.partition("{}")
+    quote = before[-1:] if before[-1:] in "\"'" else ""
+    return {
+        "pre": "",
+        "qual": key[:start],
+        "name": key[start:end],
+        "suffix": key[end:],
+        "sep": before[: len(before) - len(quote)],
+        "value": quote + value + after if quote else value,
+    }
+
+
+def test_differential_on_structured_results_never_worse_than_main() -> None:
+    """A dict result goes through the CORE path (serialise, then redact the
+    window); JSON text inside a leaf is Consiliency/pmcp#290's problem. The
+    bar here is main's: every piece main's `process_output` removed from the
+    serialised result is removed here too, or the row is in an accepted class
+    (the same classes as the string differential). 800 structured results;
+    main removes something on 172 of them.
+    """
+    policy = PolicyManager()
+    oracle = _main_oracle()["dict"]
+    corpus = _dict_corpus()
+    assert len(corpus) == len(oracle) == 800
+    bugs: list[str] = []
+    accepted: dict[str, int] = {}
+    main_types = _main_oracle()["dict_types"]
+    assert len(main_types) == 800 and main_types.count("dict") == 786
+    for (obj, key, sep, value), main_removed, main_type in zip(
+        corpus, oracle, main_types
+    ):
+        dumped = json.dumps(obj, indent=2)
+        result = policy.process_output(obj, redact=True)["result"]
+        # A dict result stays a dict wherever main's did: gateway.invoke and
+        # tasks_result put this on the wire, and a redaction that breaks the
+        # serialised JSON silently turns the result into a string.
+        if main_type == "dict" and not isinstance(result, dict):
+            bugs.append(f"{obj!r} came back as {type(result).__name__}: {result!r}")
+        out = result if isinstance(result, str) else json.dumps(result, indent=2)
+        removed_here = set(_DIFF_TOKEN.findall(dumped)) - set(_DIFF_TOKEN.findall(out))
+        kept = [
+            p
+            for p in main_removed
+            if len(p) >= 4
+            and p.lower() not in _DIFF_COLLATERAL
+            and p not in removed_here
+        ]
+        if kept:
+            reason = G.accepted(_grammar_fields(key, sep, value), "POd")
+            if reason is None:
+                bugs.append(f"{obj!r} keeps {kept}; here: {out!r}")
+            else:
+                accepted[reason] = accepted.get(reason, 0) + 1
+    assert bugs == [], "\n".join(bugs[:10])
+    # the named case: main catches the Bearer value in a dumped leaf; so does this
+    result = policy.process_output(
+        {
+            "content": [
+                {
+                    "type": "text",
+                    "text": json.dumps(
+                        {"password": "hunter2", "Authorization": "Bearer hunter2tok"}
+                    ),
+                }
+            ]
+        },
+        redact=True,
+    )["result"]
+    assert "hunter2tok" not in json.dumps(result)
+    assert result["content"][0]["text"].endswith(
+        '"Authorization": "Bearer [REDACTED]"}'
+    )  # JSON intact
+    assert PolicyManager().process_output({"text": "Bearer test-token"}, redact=True)[
+        "result"
+    ] == {"text": "Bearer [REDACTED]"}
+
+
+_DIFF_FUZZ_KEYS = [
+    "password",
+    "token",
+    "api_key",
+    "secret",
+    "client_secret",
+    "authorization",
+    "code",
+    "auth",
+    "credentials",
+    "Authorization",
+    "pwd",
+    "session",
+    "cookie",
+    "private_key",
+    "msg",
+    "text",
+    "note",
+]
+_DIFF_FUZZ_PIECES = [
+    *"abcXYZ0129 _-/+=.:;,&!@#$%^*()[]{}<>|~`'\"\\\n\t",
+    "é",
+    "\u3000",
+    "\xa0",
+    "password=",
+    "token: ",
+    "Bearer ",
+    "https://h/?token=x&",
+    '"password": "',
+    "[",
+    "]",
+]
+
+
+def _json_fuzz_corpus() -> list[dict]:
+    """The claude seat's random-JSON fuzz (seed 7), committed: 1 500 random
+    objects whose keys are credential and neutral names and whose values are
+    random strings of delimiters, quotes, backslashes, whitespace (U+3000,
+    NBSP) and redactor trigger words, nested lists and objects up to three
+    deep, and JSON scalars."""
+    rng = random.Random(7)
+
+    def value(depth: int = 0) -> object:
+        roll = rng.random()
+        if roll < 0.6 or depth > 2:
+            return "".join(
+                rng.choice(_DIFF_FUZZ_PIECES) for _ in range(rng.randint(0, 14))
+            )
+        if roll < 0.8:
+            return [value(depth + 1) for _ in range(rng.randint(0, 3))]
+        if roll < 0.9:
+            return {
+                rng.choice(_DIFF_FUZZ_KEYS): value(depth + 1)
+                for _ in range(rng.randint(1, 3))
+            }
+        return rng.choice([123, -32601, True, None, 1.5])
+
+    return [
+        {rng.choice(_DIFF_FUZZ_KEYS): value() for _ in range(rng.randint(1, 4))}
+        for _ in range(1500)
+    ]
+
+
+def test_redaction_never_breaks_a_json_document() -> None:
+    """Wherever the input parses as JSON, the output does too, on both surfaces.
+    Main breaks 8 of the widened corpus's 810 JSON rows (it ate a closing
+    quote); this redactor breaks none. A keyed value in quotes is redacted INSIDE the quotes."""
+    policy = PolicyManager()
+    checked = 0
+    for text, *_ in _differential_corpus():
+        try:
+            json.loads(text)
+        except ValueError:
+            continue
+        checked += 1
+        for surface, out in (
+            ("engine", _engine(text)),
+            ("policy", policy.redact_secrets(text)),
+        ):
+            try:
+                json.loads(out)
+            except ValueError:
+                pytest.fail(f"{surface} broke JSON: {text!r} -> {out!r}")
+    assert checked == 810, checked
+
+
+@pytest.mark.parametrize(
+    ("obj", "expected"),
+    [
+        ({"password": "hunter2"}, {"password": REDACTED}),
+        ({"password": ["hunter2", "s3cr3t99"]}, {"password": [REDACTED, REDACTED]}),
+        (
+            {"result": {"auth": {"api_key": "abc123def456"}}},
+            {"result": {"auth": {"api_key": REDACTED}}},
+        ),
+        ({"password": ""}, {"password": ""}),
+        ({"code": ["red", "x9Kq2mZ7"]}, {"code": ["red", REDACTED]}),
+    ],
+)
+def test_structured_result_round_trips_as_a_dict(obj: dict, expected: dict) -> None:
+    """The headline case, on the structured surface: redacted, still a dict."""
+    assert PolicyManager().process_output(obj, redact=True)["result"] == expected
+
+
+def test_pretty_printed_list_value_is_redacted_inside_its_quotes() -> None:
+    """`"password": [\n  "hunter2"\n]` -- a list under a secret key. Rev 7 ate
+    the `[`; main and the rev-8 spike left the value. Each quoted element is
+    redacted in place and the brackets stay."""
+    text = '{\n  "password": [\n    "hunter2"\n  ]\n}'
+    for out in (_engine(text), _policy(text)):
+        assert "hunter2" not in out
+        assert json.loads(out) == {"password": [REDACTED]}
+
+
+def test_operator_pattern_applies_to_a_percent_encoded_query_value(
+    tmp_path: Path,
+) -> None:
+    """Item 4 of rev 8: an operator's own pattern, written for the decoded
+    shape, still redacts the value when it arrives percent-encoded in a URL
+    query (main decoded before matching)."""
+    policy_file = tmp_path / "policy.yaml"
+    policy_file.write_text("redaction:\n  patterns:\n    - 'acme-[0-9]{6}'\n")
+    policy = PolicyManager(policy_path=policy_file)
+    plain = policy.redact_secrets("id acme-123456 end")
+    assert "123456" not in plain  # the pattern is live on the plain surface
+    out = policy.redact_secrets("https://h.example/?q=%61%63%6D%65%2D123456")
+    assert "123456" not in out and "%61%63%6D%65" not in out, out
+
+
+def test_url_decode_depth_bound_fails_closed() -> None:
+    """Item 3 of rev 8: a value that decodes past the depth bound raises no
+    RecursionError and is redacted whole rather than passed through."""
+    text = "https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"
+    for out in (_engine(text), _policy(text)):
+        assert "%25%25" not in out
+        assert REDACTED in out
+
+
+def test_crlf_and_no_break_space_are_whitespace_too() -> None:
+    """HTTP header folding and Windows dumps use CRLF; `\\s` on main matched
+    `\\xa0`. Rev 7 narrowed continuations to LF with an indent gate (grok):
+    each of these leaked while main redacted it.
+    """
+    for text, expected in [
+        ("password:\r\nhunter2", "password:\r\n[REDACTED]"),
+        ("password:\r\n  hunter2", "password:\r\n  [REDACTED]"),
+        ("token\r\nabc123def456", "token\r\n[REDACTED]"),
+        ("Authorization:\r\n  s3cr3tvalue", "Authorization:\r\n  [REDACTED]"),
+        ("password:\xa0hunter2", "password:\xa0[REDACTED]"),
+        (
+            "Missing bearer token:\r\nthe bearer of",
+            "Missing bearer token:\r\nthe bearer of",
+        ),
+        ("token:\nthe bearer of", "token:\nthe bearer of"),
+    ]:
+        assert _engine(text) == expected, text
+        if "\xa0" in text:
+            # the policy default now reads `\xa0` as whitespace too (like
+            # main's `\s`) and replaces it with the value: same secret
+            # removed, one fewer separator character
+            assert "hunter2" not in _policy(text), _policy(text)
+        else:
+            assert _policy(text) == expected, text
+
+
+def test_encoded_query_values_are_decoded_a_bounded_number_of_times() -> None:
+    """Decoding a query value and asking the engine about it can meet another
+    encoded URL; unbounded, a downstream-controlled diagnostic raised
+    `RecursionError` (grok and codex, independently). Past three levels the
+    value is treated as covered and redacted -- failing closed.
+    """
+    codex = "https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"
+    assert len(codex) < 10_000
+    assert _engine(codex) == "https://h.example/?q=[REDACTED]"
+    assert _policy(codex) == "https://h.example/?q=[REDACTED]"
+    grok = "https://h.example/?q=" + "%25" * 400 + "67%68%70%5F" + TOKEN[4:]
+    assert _engine(grok) == "https://h.example/?q=[REDACTED]"
+    assert (
+        _engine("https://h.example/?q=%20hello%20world")
+        == "https://h.example/?q=%20hello%20world"
+    )
+
+
+def test_encoded_query_values_are_matched_by_the_policy_patterns_too() -> None:
+    """`%67%68%70%5Fabcdefghijklmnop` decodes to the C-13 probe the engine
+    ignores on purpose; the policy's `ghp_` default must still catch it, as
+    main did (codex): the operator's and default patterns are asked about
+    the decoded value and a hit redacts the encoded value whole.
+    """
+    text = "https://h.example/?q=%67%68%70%5Fabcdefghijklmnop"
+    assert _engine(text) == text
+    assert _policy(text) == "https://h.example/?q=[REDACTED]"
+    manager = PolicyManager()
+    manager._redaction_regexes = [re.compile(r"mytoken-[a-z]+")]
+    assert (
+        manager.redact_secrets("https://h.example/?q=mytoken%2Ddeadbeef")
+        == "https://h.example/?q=[REDACTED]"
+    )
+
+
+def test_a_comparison_survives_on_both_surfaces() -> None:
+    """`if token == expected:` is untouched by the engine AND by the policy
+    surface (whose default `token` pattern now carries the same separator
+    grammar; rev 7 pinned only the engine -- grok)."""
+    assert _engine("if token == expected:") == "if token == expected:"
+    assert _policy("if token == expected:") == "if token == expected:"
+    assert _policy("password==hunter2") == "password=[REDACTED]"
+    assert _policy("password:=hunter2") == "password:[REDACTED]"
+
+
+# === rev 9: the board's second round and the differential it widened ====== #
+#
+# One test per finding class, each red on rev 8 (`56d7f80`) and green here;
+# where a finding lives on both surfaces, both are asserted. Where main's exact
+# text differs from ours only by a separator character (the policy default
+# re-spells `:　` as `:`), the assertion is `secret not in out`.
+
+
+def _both(text: str) -> list[tuple[str, str]]:
+    return [("engine", _engine(text)), ("policy", _policy(text))]
+
+
+def _process(obj: dict) -> object:
+    return PolicyManager().process_output(obj, redact=True)["result"]
+
+
+def test_the_differential_corpus_covers_every_axis() -> None:
+    """The widened generator really produces each axis it claims (a check is
+    only a check if it would fail were the axis missing)."""
+    corpus = _differential_corpus()
+    keys = {key.lower() for _, key, *_ in corpus}
+    seps = {sep for _, _, sep, _, _ in corpus}
+    values = {value for *_, value, _ in corpus}
+    wraps = {wrap for *_, wrap in corpus}
+    assert {k.lower() for k in _DIFF_KEYS} <= keys
+    assert set(_DIFF_SEPS) <= seps and set(_DIFF_VALUES) <= values
+    assert set(_DIFF_WRAPS) <= wraps
+    for axis in (
+        "db.password",
+        "accesstoken",
+        "clientsecret",
+        "password_confirmation",
+        "--clientsecret",
+    ):
+        assert axis in keys, axis
+    # every `str.isspace()` character but CR and LF, alone and after `:`
+    spaces = {chr(i) for i in range(0x110000) if chr(i).isspace()} - {"\r", "\n"}
+    assert set(_DIFF_SPACE_CHARS) == spaces
+    assert {c for c in spaces} <= seps and {":" + c for c in spaces} <= seps
+    assert {" == ", "== ", " ==", '": '} <= seps
+    assert {"null", "true", "false", "-1.5e3", ",hunter22", "}hunter22"} <= values
+    # JSON literals really sit after a quoted key in a valid document, and
+    # the JSON wraps really produce nested objects and lists of objects
+    documents = []
+    for text, *_ in corpus:
+        try:
+            documents.append(json.loads(text))
+        except ValueError:
+            continue
+    corpus_keys = {k.lower() for k in _DIFF_KEYS}
+
+    def literal_members(node: object) -> int:
+        if isinstance(node, list):
+            return sum(literal_members(item) for item in node)
+        if not isinstance(node, dict):
+            return 0
+        return sum(
+            (
+                k.lower() in corpus_keys
+                and (v is None or isinstance(v, (bool, int, float)))
+            )
+            + literal_members(v)
+            for k, v in node.items()
+        )
+
+    literal_after_quoted_key = [doc for doc in documents if literal_members(doc)]
+    assert len(literal_after_quoted_key) >= len(_DIFF_KEYS)
+    assert any(isinstance(d, dict) and "items" in d for d in documents)
+    assert any(isinstance(d, dict) and "outer" in d for d in documents)
+    assert any(isinstance(d, list) and isinstance(d[0], dict) for d in documents)
+    shapes = [obj for obj, *_ in _dict_corpus()]
+    assert any(v is None for obj in shapes if "note" in obj for v in obj.values())
+    assert any("items" in obj for obj in shapes) and any(
+        "outer" in obj for obj in shapes
+    )
+
+
+@pytest.mark.parametrize(
+    ("text", "expected"),
+    [
+        ('{"access_token": null}', '{"access_token": null}'),
+        ('{"token": true}', '{"token": true}'),
+        ('{"password": false}', '{"password": false}'),
+        # rev 10 (F5/F6, the maintainer's decision 1): a bare number after
+        # a quoted key stays unchanged, as on main; numeric secrets under a
+        # quoted key are Consiliency/pmcp#290's scope
+        ('{"token": 42}', '{"token": 42}'),
+        ('{"token": -1.5e3}', '{"token": -1.5e3}'),
+    ],
+)
+def test_b1_a_json_literal_after_a_quoted_key_keeps_the_document(
+    text: str, expected: str
+) -> None:
+    """B1: a bare JSON scalar after a quoted key is not a secret; the
+    document stays JSON and a dict result stays a dict."""
+    for surface, out in _both(text):
+        assert out == expected, (surface, out)
+    assert _process(json.loads(text)) == json.loads(expected)
+
+
+@pytest.mark.parametrize(
+    "obj", [{"Authorization": {"a": "b"}}, {"Authorization": ["9"]}]
+)
+def test_b2_authorization_never_starts_on_a_bracket(obj: dict) -> None:
+    """B2: an object or list under `Authorization` is structure, not a value."""
+    text = json.dumps(obj)
+    for surface, out in _both(text):
+        assert out == text, (surface, out)
+    assert _process(obj) == obj
+
+
+@pytest.mark.parametrize(
+    "key",
+    [
+        "db.password",
+        "self.password",
+        "spring.datasource.password",
+        # B4: PascalCase
+        "AccessToken",
+        "ClientSecret",
+        "SessionToken",
+        "DbPassword",
+    ],
+)
+@pytest.mark.parametrize("sep", ["=", " = ", ": "])
+def test_b3_b4_dotted_and_pascal_case_keys(key: str, sep: str) -> None:
+    text = f"{key}{sep}hunter22"
+    for surface, out in _both(text):
+        assert "hunter22" not in out and out.startswith(key), (surface, out)
+
+
+@pytest.mark.parametrize(
+    "space",
+    [c for c in _DIFF_SPACE_CHARS if c not in "\n\r"],
+    ids=lambda c: f"U+{ord(c):04X}",
+)
+def test_b5_every_whitespace_character_separates(space: str) -> None:
+    """B5: every `str.isspace()` character but CR/LF, after `:` and alone."""
+    for text in (f"password:{space}hunter22", f"token{space}abc123def456"):
+        for surface, out in _both(text):
+            assert "hunter22" not in out and "abc123def456" not in out, (
+                surface,
+                out,
+            )
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        "password2=hunter22",
+        "password_confirmation=hunter22",
+        "passwordHash=hunter22",
+        "secret_value=hunter22",
+        "SECRET_KEY abc123def456",
+        "--clientSecret abc123def456",
+        "--sessionToken abc123def456",
+        "--token_id abc123def456",
+        # the whitespace form of a suffixed key (defect B of round 3)
+        "password2 abc123def456",
+        "passwordHash hunter2",
+        "passwordHash\tabc123def456",
+        "secret_value\tabc123def456",
+    ],
+)
+def test_n1_suffixed_keys_redact_a_credential_shaped_value(text: str) -> None:
+    secret = re.split(r"[=\s]", text)[-1]
+    for surface, out in _both(text):
+        assert secret not in out, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        "token_type=bearer",
+        "password_length=12",
+        "token_endpoint=https://h.example/oauth/token",
+        "secret_arn=arn:aws:secretsmanager:us-east-1:1:secret:x",
+        "token_endpoint https://h.example/oauth/token",
+        "passwords expired now",
+    ],
+)
+def test_n1_suffixed_keys_keep_metadata(text: str) -> None:
+    """Guards (already green on rev 8): a suffix naming metadata keeps its
+    non-credential value."""
+    for surface, out in _both(text):
+        assert out == text, (surface, out)
+
+
+def test_n2_a_list_of_objects_is_not_a_flat_value() -> None:
+    obj = {"secrets": [{"id": "db", "name": "prod"}]}
+    for surface, out in _both(json.dumps(obj)):
+        assert out == json.dumps(obj), (surface, out)
+    assert _process(obj) == obj
+
+
+def test_n3_a_quote_opening_on_structure_is_not_a_value() -> None:
+    """N3: after an UNQUOTED key (`token: ` at the end of a string), a quote
+    whose content starts on `,:]}` closes that string. After a QUOTED key the
+    quote always opens the value."""
+    obj = {"msg": "missing token: ", "code": 401}
+    for surface, out in _both(json.dumps(obj)):
+        assert out == json.dumps(obj), (surface, out)
+    assert _process(obj) == obj
+    for surface, out in _both('{"password": ", secret"}'):
+        assert json.loads(out) == {"password": REDACTED}, (surface, out)
+
+
+def _clock_growth(
+    run: Callable[[str], object], shape: Callable[[int], str], small: int, large: int
+) -> float:
+    import time
+
+    def best(text: str) -> float:
+        times = []
+        for _ in range(2):
+            start = time.perf_counter()
+            run(text)
+            times.append(time.perf_counter() - start)
+        return min(times)
+
+    return best(shape(large)) / max(best(shape(small)), 1e-4)
+
+
+def test_n4_the_key_qualifier_is_bounded() -> None:
+    """N4: rev 8 took ~216 s on 66 KB of this. Growth over 16x the input,
+    best of two at each size: linear is ~16x, quadratic ~256x; the bound of
+    64x is a factor of four from either, so host load cannot flip it (rev
+    12's board, T-1: an absolute bound near the measured time can)."""
+    for surface, redact in (("engine", _engine), ("policy", _policy)):
+        ratio = _clock_growth(redact, lambda n: "a_" * (n // 2), 4_096, 65_536)
+        assert ratio < 64, (surface, ratio)
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("password == hunter2", "hunter2"),
+        ("password== hunter2", "hunter2"),
+        ("if token == hunter2:", "hunter2"),
+    ],
+)
+def test_g1_a_spaced_double_equals_before_a_secret(text: str, secret: str) -> None:
+    for surface, out in _both(text):
+        assert secret not in out, (surface, out)
+    for surface, out in _both("if token == expected:"):
+        assert out == "if token == expected:", (surface, out)
+
+
+def test_c2_a_percent_encoded_key_is_still_the_key() -> None:
+    for surface, out in _both("https://h.example/?api%2Dkey=hunter2"):
+        assert "hunter2" not in out, (surface, out)
+
+
+def test_c4_bearer_across_a_crlf() -> None:
+    for surface, out in _both("Bearer\r\nhunter2"):
+        assert out == "Bearer\r\n[REDACTED]", (surface, out)
+    # a flag on the next line after `bearer` was its value on main (`\\s+`
+    # crosses the break): the floor keeps main's redaction
+    prose = "--rotated bearer\n--ingredient assertion"
+    for surface, out in _both(prose):
+        assert out == "--rotated bearer\n[REDACTED] assertion", (surface, out)
+
+
+@pytest.mark.parametrize(
+    ("obj", "expected"),
+    [
+        ({"text": 'token hunter2value" hi'}, {"text": 'token [REDACTED]" hi'}),
+        (
+            {"text": 'Authorization: hunter2value" hi'},
+            {"text": 'Authorization: [REDACTED]" hi'},
+        ),
+    ],
+)
+def test_c6_a_quote_inside_a_leaf_keeps_the_dict(obj: dict, expected: dict) -> None:
+    assert _process(obj) == expected
+
+
+def test_c7_a_bracket_inside_a_quoted_list_element() -> None:
+    """C7: `"first]"` does not end the list (this leaked on main too); a list
+    under a non-secret key is untouched."""
+    text = '{"password": ["first]", "hunter2"]}'
+    for surface, out in _both(text):
+        assert json.loads(out) == {"password": [REDACTED, REDACTED]}, (surface, out)
+    assert _process(json.loads(text)) == {"password": [REDACTED, REDACTED]}
+    kept = '{"error_codes": ["AADSTS50011"]}'
+    for surface, out in _both(kept):
+        assert out == kept, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        'password="hunter2\\\\"',
+        "password=abc123\\",
+        "token='abc123def456'",
+        'api_key="abc123def456"',
+        'aws_secret="wJalrXUtnFEMI\\\\"',
+    ],
+)
+def test_policy_defaults_never_start_on_a_quote_or_end_on_a_backslash(
+    text: str,
+) -> None:
+    """The operator-visible default patterns: the captured value never starts
+    on a quote (rev 8 ate the opening one) and never ends on a backslash (the
+    escape of a JSON quote); the engine redacts quoted values INSIDE them.
+    Rev 10's forms (`_ADDITIVE_DEFAULT_PATTERNS`) carry this; main's own
+    defaults are the floor, whose JSON adjustment keeps the same guarantee."""
+    for pattern in _ADDITIVE_DEFAULT_PATTERNS:
+        for match in re.finditer(pattern, text, re.IGNORECASE):
+            if match.lastindex is None:
+                continue
+            value = match.group(match.lastindex)
+            assert not value.startswith(('"', "'")), (pattern, value)
+            assert not value.endswith("\\"), (pattern, value)
+    out = _policy(text)
+    quote = text[text.index("=") + 1]
+    if quote in "\"'":
+        assert out.startswith(text[: text.index("=") + 2] + REDACTED), out
+
+
+@pytest.mark.parametrize(
+    "obj",
+    [
+        {"a": "token: [x", "b": "]"},
+        {"a": "password: [x", "b": "y]"},
+    ],
+)
+def test_a_keyed_list_never_straddles_a_string_boundary(obj: dict) -> None:
+    """After an unquoted key inside a leaf, `[` opens a list only if a literal
+    array follows (quoted strings or JSON scalars): `[x", "b": "]` is two
+    string values, not one list."""
+    text = json.dumps(obj)
+    for surface, out in _both(text):
+        assert set(json.loads(out)) == set(obj), (surface, out)
+        assert json.loads(out)["b"] == obj["b"], (surface, out)
+
+
+@pytest.mark.parametrize(
+    "obj",
+    [
+        {"a": "password='x", "b": "y'"},
+        {"a": "secret='q", "b": 1, "c": "z'"},
+    ],
+)
+def test_a_quoted_value_never_straddles_via_the_other_quote(obj: dict) -> None:
+    """After an unquoted key, a single-quoted "value" that holds an unescaped
+    double quote runs across a JSON string boundary; the additive rules do not
+    read it as a value. Main's policy default did (`[\"']?([^\\s\"']+)` took
+    the `x`), so the floor redacts it there -- inside the string, and the
+    document stays JSON."""
+    text = json.dumps(obj)
+    for surface, out in _both(text):
+        if surface == "engine":
+            assert json.loads(out) == obj, (surface, out)
+        else:
+            key = next(iter(obj))
+            assert json.loads(out) == {
+                **obj,
+                key: obj[key].split("'")[0] + "'" + REDACTED,
+            }, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "space", ["\x0b", "\x85", "\xa0", "\u2003", "\u2028", "\u3000"]
+)
+@pytest.mark.parametrize("key", ["token", "bearer:", "password", "SECRET_KEY"])
+def test_no_span_starts_inside_a_json_escape(space: str, key: str) -> None:
+    """Defect A of round 3: `json.dumps` spells non-ASCII whitespace
+    `\\uXXXX`; a span starting at the `u` left a lone backslash, so the
+    document broke and a dict result came back as a string."""
+    obj = {"text": f"{key}{space}abc123def456"}
+    text = json.dumps(obj)
+    for surface, out in _both(text):
+        assert "abc123def456" not in json.loads(out)["text"], (surface, out)
+    result = _process(obj)
+    assert isinstance(result, dict) and "abc123def456" not in result["text"]
+
+
+@pytest.mark.parametrize(
+    "key",
+    [
+        "CLIENTSECRET",
+        "Clientsecret",
+        "clientsecret",
+        "DBPASSWORD",
+        "dbpassword",
+        "ACCESSTOKEN",
+        "accesstoken",
+        "MYPASSWORD",
+    ],
+)
+@pytest.mark.parametrize("sep", ["=", ": ", "\t"])
+def test_a_case_folded_compound_key_is_still_a_key(key: str, sep: str) -> None:
+    """Defect C of round 3: with its case boundary folded away, a compound
+    key still names a secret when the value is credential-shaped."""
+    for surface, out in _both(f"{key}{sep}abc123def456"):
+        assert "abc123def456" not in out, (surface, out)
+
+
+def test_a_mixed_case_identifier_is_not_a_glued_key() -> None:
+    for text in ("Ed25519PrivateKey X509Cert", "errorcode=invalid_grant"):
+        for surface, out in _both(text):
+            assert out == text, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        "secret -1.5e3",
+        "password2\xa0-1.5e3",
+        "refresh_token:\r\n  -1.5e3",
+        "token -abc123def",
+        "password\n  -hunter22",
+        "password:\n  -hunter22",
+        "Bearer\n-abc123def",
+    ],
+)
+def test_a_dash_glued_to_a_value_is_part_of_it(text: str) -> None:
+    """A base64url secret can start with `-`; only a `--` flag or a lone
+    marker followed by whitespace is a flag or a bullet (as on main)."""
+    secret = text.split()[-1]
+    for surface, out in _both(text):
+        assert secret not in out, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        "token:\n  - item",
+        "token:\n- item",
+    ],
+)
+def test_a_flag_or_a_bullet_after_a_keyword_is_not_its_value(text: str) -> None:
+    for surface, out in _both(text):
+        assert out == text, (surface, out)
+
+
+@pytest.mark.parametrize(
+    ("text", "surfaces", "expected"),
+    [
+        ("secret --bucket", ("engine", "policy"), "secret [REDACTED]"),
+        ("password:\n  * item", ("policy",), "password:\n  [REDACTED] item"),
+        (
+            "--rotated bearer\n--ingredient assertion",
+            ("engine", "policy"),
+            "--rotated bearer\n[REDACTED] assertion",
+        ),
+        ("Bearer\n--flag12345", ("engine", "policy"), "Bearer\n[REDACTED]"),
+    ],
+)
+def test_a_flag_or_a_bullet_main_redacted_stays_redacted(
+    text: str, surfaces: tuple[str, ...], expected: str
+) -> None:
+    """Rev 10 kept these; main redacted them (its value classes take a flag
+    or a bullet after a keyword), and no stated class covers a flag or a
+    bullet, so the floor keeps main's redaction on the surfaces main made it."""
+    for surface, out in _both(text):
+        assert out == (expected if surface in surfaces else text), (surface, out)
+
+
+def _has_escaped_quote(text: str) -> bool:
+    """A backslash-escaped quote in the INPUT: JSON inside a leaf, which is
+    Consiliency/pmcp#290's scope."""
+    return '\\"' in text
+
+
+def test_property_json_fuzz_keeps_documents_and_dicts() -> None:
+    """The claude seat's JSON fuzz, committed (seed 7, 1 500 objects, each
+    serialised four ways -- compact and indented, ASCII-escaped and not --
+    so 6 000 documents per surface). Two invariants:
+
+    * a document with no backslash-escaped quote (`\\"`, decided from the
+      INPUT; Consiliency/pmcp#290's scope) still parses after redaction on both surfaces --
+      main breaks ~1 000 of them per surface;
+    * `process_output(obj)` returns a dict wherever main's did (main's type
+      per object is recorded in the oracle fixture, not computed here).
+    """
+    policy = PolicyManager()
+    objects = _json_fuzz_corpus()
+    main_types = _main_oracle()["fuzz_types"]
+    assert len(objects) == len(main_types) == 1500
+    checked = excluded = 0
+    broken: list[str] = []
+    for obj, main_type in zip(objects, main_types):
+        for indent in (None, 2):
+            for ascii_only in (True, False):
+                text = json.dumps(obj, indent=indent, ensure_ascii=ascii_only)
+                if _has_escaped_quote(text):
+                    excluded += 1
+                    continue
+                checked += 1
+                for surface, out in (
+                    ("engine", _engine(text)),
+                    ("policy", policy.redact_secrets(text)),
+                ):
+                    try:
+                        json.loads(out)
+                    except ValueError:
+                        broken.append(f"{surface}: {text!r} -> {out!r}")
+        result = policy.process_output(obj, redact=True)["result"]
+        if main_type == "dict" and not isinstance(result, dict):
+            broken.append(f"process_output: {obj!r} -> {result!r}")
+    assert broken == [], "\n".join(broken[:10])
+    assert checked > 3000 and excluded > 0, (checked, excluded)
+    assert main_types.count("dict") > 1000
+
+
+# === rev 10: regression tests, one per fix family ========================== #
+#
+# Each is red on rev 9 (`60ca193`) and green here; each fix is also pinned by a
+# mutant of its production change that these tests kill (the plan lists them).
+
+
+def _leaf(text: str) -> str:
+    """`process_output({"t": text})`'s leaf, which must still be a dict."""
+    result = _process({"t": text})
+    assert isinstance(result, dict), result
+    return str(result["t"])
+
+
+def _gone(text: str, secret: str) -> None:
+    for surface, out in _both(text):
+        assert secret not in out, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "obj",
+    [
+        {"token": float("nan")},
+        {"secret": float("inf")},
+        {"session": float("-inf")},
+        {"password": float("nan"), "x": 1},
+        {"stats": {"tokens": float("nan")}},
+        {"usage": {"input_tokens": 12, "output_tokens": 34}, "max_tokens": 1024},
+        {"sessions": 3, "secrets": 0, "Authorization": 7},
+    ],
+)
+def test_f5_f6_a_bare_json_scalar_after_a_quoted_key_is_unchanged(obj: dict) -> None:
+    """F5/F6 (the maintainer's decision 1): every scalar `json.dumps` emits,
+    `NaN` and `+-Infinity` included, stays as it is after a quoted key, as on
+    main: the result stays a dict and no leaf changes type. Numeric secrets
+    under a quoted key are Consiliency/pmcp#290's."""
+    result = _process(obj)
+    assert isinstance(result, dict), result
+    assert json.dumps(result, sort_keys=True) == json.dumps(obj, sort_keys=True)
+    for text in ('{"token": NaN}', '{"token": -Infinity}', '{"Authorization": 1e+20}'):
+        for surface, out in _both(text):
+            assert out == text, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "pre",
+    ["\xa0", "\x08", "\x0c", "\x0b", "\x1b", "\x7f", "\x85", "\u3000", "\u2028"]
+    + ["\xe9", "\u2022", "\u201c", "\u5bc6", "\U0001f642", "\\", '"'],
+    ids=lambda c: f"U+{ord(c):04X}",
+)
+@pytest.mark.parametrize(
+    ("body", "secret"),
+    [
+        ("password=hunter22x", "hunter22x"),
+        ("Token hunter2tok", "hunter2tok"),
+        ("Secret abc123def456", "abc123def456"),
+        ("Bearer hunter2tok", "hunter2tok"),
+    ],
+)
+def test_f3_a_json_escape_before_a_key_is_a_boundary(
+    pre: str, body: str, secret: str
+) -> None:
+    """F3: `process_output` serialises a dict leaf with `ensure_ascii=True`,
+    so every control, non-ASCII and escaped whitespace character before a key
+    arrives as an escape whose tail is alphanumeric (`\\u00a0password`,
+    `\\bToken`); main's `\\b[A-Za-z0-9_-]*` swallowed the tail and redacted."""
+    assert secret not in _leaf(pre + body)
+    for surface, out in _both(json.dumps({"t": pre + body})):
+        assert secret not in out, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        '{"t": "\\u00a0password=hunter22x"}',
+        '{"t": "\\bsecret: hunter22x"}',
+        '{"t": "\\u2022token=hunter22x"}',
+        '{"t": "\\ftoken: hunter22x"}',
+    ],
+)
+def test_f3_the_policy_defaults_read_a_json_escape_as_a_boundary(text: str) -> None:
+    """F3 on the operator-visible defaults themselves (the engine covers the
+    policy surface too, so only the patterns can show it): `\\b` made a key
+    start right after an escape's alphanumeric tail on main."""
+    hits = [
+        m.group(m.lastindex)
+        for pattern in _ADDITIVE_DEFAULT_PATTERNS
+        for m in re.finditer(pattern, text, re.IGNORECASE)
+        if m.lastindex
+    ]
+    assert any(h.startswith("hunter22x") for h in hits), hits
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("Cookie: session=bearer \u2006(t8B7fzJ0L)", "t8B7fzJ0L"),
+        ("token: BEARER \r{hM6odfJdqx}", "hM6odfJdqx"),
+        ("Authorization:\u2006'gn1y2tpo'", "gn1y2tpo"),
+        ("AUTHORIZATION:\u2006Bearer\n\n'ay47NjWsmUf'", "ay47NjWsmUf"),
+        ("authorization=\u2029bearer\t'gS6CJ7XD'", "gS6CJ7XD"),
+    ],
+)
+def test_f3_an_escaped_space_after_bearer_or_authorization_separates(
+    text: str, secret: str
+) -> None:
+    """F3's follow-up: in a serialised leaf the whitespace between
+    `Bearer`/`Authorization:` and the value is an escape (`\\u2006`, `\\r`,
+    `\\t`); main's `\\s+[^\\s,;]+` took it as part of the value and
+    redacted the token with it."""
+    assert secret not in _leaf(text)
+    for surface, out in _both(json.dumps({"t": text})):
+        assert secret not in out, (surface, out)
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("password:\n\nhunter22x", "hunter22x"),
+        ("Temporary password:\n\n    hunter22x", "hunter22x"),
+        ("password:\r\n\r\nhunter22x", "hunter22x"),
+        ("password:\rhunter22x", "hunter22x"),
+        ("password:\n\rhunter22x", "hunter22x"),
+        ("Secret:\r\rhunter22x", "hunter22x"),
+        ("token\n\nhunter22tok", "hunter22tok"),
+        ("token\r\r\nhunter22tok", "hunter22tok"),
+        ("token:\n\n  abc123def456", "abc123def456"),
+        ("Authorization:\n\nhunter22x", "hunter22x"),
+        # F4b: the break BEFORE the operator
+        ("password\n: hunter22x", "hunter22x"),
+        ("Authorization\r\n: hunter22x", "hunter22x"),
+        ("api_key\n=\nhunter22x", "hunter22x"),
+    ],
+)
+def test_f4_any_line_break_run_separates(text: str, secret: str) -> None:
+    """F4: a blank line, a bare CR, `\\n\\r` or `\\r\\r\\n`, on either side of
+    the operator: main's `[\\s:=]+` accepted any run. The unindented-break gate
+    still keeps prose (`token:\\n\\nThe next paragraph`, the FP table)."""
+    _gone(text, secret)
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("X-Auth: Bearer hunter22x", "hunter22x"),
+        ("X-Auth:Bearer hunter22x", "hunter22x"),
+        ("auth: bearer\thunter2tok", "hunter2tok"),
+        ("token: Bearer abc123def456", "abc123def456"),
+        ("X-Access-Token: Bearer hunter2tok", "hunter2tok"),
+        ("session=Bearer hunter2tok", "hunter2tok"),
+        ("Cookie: session=Bearer hunter2tok", "hunter2tok"),
+        ("headers: {X-Auth: Bearer hunter2tok}", "hunter2tok"),
+    ],
+)
+def test_f2_bearer_after_a_key_and_separator(text: str, secret: str) -> None:
+    """F2: a header or field whose value is `Bearer <token>`. The lookahead
+    and the plain-word gate already keep `token_type=Bearer expires_in=3600`
+    (the FP table)."""
+    _gone(text, secret)
+    assert secret not in _leaf(text)
+
+
+def test_f9_the_pem_rule_is_linear() -> None:
+    """F9: every BEGIN without an END scanned to the end of the text (rev 9:
+    5.6 s on 264 KB). Growth over 16x the input (16.5 KB -> 264 KB), best of
+    two at each size, under 64x: linear is ~16x, quadratic ~256x."""
+    unit = "-----BEGIN RSA PRIVATE KEY-----\n"
+    for surface, redact in (("engine", _engine), ("policy", _policy)):
+        ratio = _clock_growth(
+            redact, lambda n: unit * (n // len(unit)), 16_896, 270_336
+        )
+        assert ratio < 64, (surface, ratio)
+    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----"
+    assert _engine(f"key: {block} end") == "key: [REDACTED] end"
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("Database:Password=hunter22x", "hunter22x"),
+        ("ConnectionStrings:Password=hunter22x", "hunter22x"),
+        ("--Database:Password=hunter22x", "hunter22x"),
+        ("App:ClientSecret=hunter22x", "hunter22x"),
+        ("vault:secret=hunter22x", "hunter22x"),
+        ("env:SECRET=hunter22x", "hunter22x"),
+        ("mongodb:password: hunter22x", "hunter22x"),
+    ],
+)
+def test_f1_a_colon_qualified_key_is_a_key(text: str, secret: str) -> None:
+    """F1: `Section:Key` (.NET configuration, namespaced log fields). Not
+    `::` (a path) and not a key inside an `arn:`/`urn:` name (the FP table)."""
+    _gone(text, secret)
+    assert secret not in _leaf(text)
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("password===hunter22x", "hunter22x"),
+        ("password====hunter22x", "hunter22x"),
+        ("password:: hunter22x", "hunter22x"),
+        ("password=:\u205fhunter22x", "hunter22x"),
+        ("Database:Password=:hunter22x", "hunter22x"),
+        ("token:==abc123def456", "abc123def456"),
+    ],
+)
+def test_n1_an_operator_run_separates(text: str, secret: str) -> None:
+    """N1: main's `[\\s:=]+` took any run; a run holding `==` or `::` is a
+    comparison or a path unless the value is credential-shaped
+    (`if (token === expected)`, `token::Type`: the FP table)."""
+    _gone(text, secret)
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("password_=hunter22x", "hunter22x"),
+        ("password-=hunter22x", "hunter22x"),
+        ("password__=hunter22x", "hunter22x"),
+        ("id_token__Xv=hunter22x", "hunter22x"),
+        ("api_keyNf--sKOs: hunter22x", "hunter22x"),
+        ("password__ abc123def456", "abc123def456"),
+    ],
+)
+def test_n2_a_trailing_joiner_or_joiner_run_in_the_suffix(
+    text: str, secret: str
+) -> None:
+    _gone(text, secret)
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ('password="hunter22x', "hunter22x"),
+        ("api_key='hunter22x", "hunter22x"),
+        ('secret: "hunter22x', "hunter22x"),
+        ("aws_secret='hunter22x more", "hunter22x"),
+    ],
+)
+def test_n5_an_unterminated_opening_quote(text: str, secret: str) -> None:
+    """N5: main's policy defaults took the run after an opening quote."""
+    _gone(text, secret)
+    assert _engine('password="hunter22x') == 'password="[REDACTED]'
+    for kept in ('password="', 'He said "token', "it's the password's fault"):
+        for surface, out in _both(kept):
+            assert out == kept, (surface, out)
+
+
+@pytest.mark.parametrize(
+    ("text", "expected"),
+    [
+        ('Bearer "hunter22x"', 'Bearer "[REDACTED]"'),
+        ("Bearer 'hunter22x'", "Bearer '[REDACTED]'"),
+        ("Bearer (hunter22x)", "Bearer ([REDACTED])"),
+        ("Bearer [hunter22x]", "Bearer [[REDACTED]]"),
+        ("Bearer {hunter22x}", "Bearer {[REDACTED]}"),
+        ("Authorization: [hunter22x]", "Authorization: [[REDACTED]]"),
+        ("Authorization: {hunter22x}", "Authorization: {[REDACTED]}"),
+        ('Authorization: Bearer "hunter22x"', 'Authorization: Bearer "[REDACTED]"'),
+    ],
+)
+def test_n6_n7_a_wrapped_bearer_or_authorization_value(
+    text: str, expected: str
+) -> None:
+    """N6/N7: the inside of a wrapped value is redacted and the wrapping
+    kept. After a quoted key a bracket is JSON structure."""
+    for surface, out in _both(text):
+        assert out == expected, (surface, out)
+    for obj in ({"Authorization": [1.5]}, {"Authorization": {"a": "b"}}):
+        assert _process(obj) == obj
+
+
+@pytest.mark.parametrize(
+    "key",
+    [
+        "otp_code",
+        "verification_code",
+        "mfa_code",
+        "recovery_code",
+        "sms_code",
+        "invite_code",
+        # a key or a redeemable value is not a status
+        "key_code",
+        "promo_code",
+        "coupon_code",
+        "discount_code",
+    ],
+)
+def test_n8_code_under_a_non_status_qualifier_is_a_weak_key(key: str) -> None:
+    """N8: `code` under a qualifier that is not a diagnostic word redacts a
+    credential-shaped value, as main did; plain words and numbers are kept
+    (a weak key), and a diagnostic qualifier keeps its value (class C12)."""
+    _gone(f"{key}=abc123def456", "abc123def456")
+    _gone(f'{{"{key}": "abc123def456"}}', "abc123def456")
+    for kept in (
+        f"{key}=expired",
+        f"{key}=401",
+        "status_code=401",
+        "error_code=invalid_grant",
+        "sqlstate_code=42P01",
+    ):
+        for surface, out in _both(kept):
+            assert out == kept, (surface, out)
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("C:\\secret=hunter22x", "hunter22x"),
+        ("DOMAIN\\password=hunter22x", "hunter22x"),
+        ("\\password hunter22x", "hunter22x"),
+        ("\\0WM9qpSecret_old=tEDvZ67S1", "tEDvZ67S1"),
+        ("\\token=hunter22x", "hunter22x"),
+        ("\\tenant_id: hunter22x", "hunter22x"),
+    ],
+)
+def test_f8_a_key_after_a_backslash(text: str, secret: str) -> None:
+    """F8: a backslash is a key boundary, as `\\b` made it on main; only the
+    tail of a JSON escape (`\\u00a0`, `\\n`) is never part of a key."""
+    _gone(text, secret)
+    assert secret not in _leaf(text)
+    assert _leaf("\u2022-code-id=:hunter22x") == "\u2022-code-id=:[REDACTED]"
+
+
+@pytest.mark.parametrize(
+    ("text", "secret"),
+    [
+        ("java -jar x.jar -password hunter22x -user bob", "hunter22x"),
+        ("-token abc123def456", "abc123def456"),
+        ("_token hunter22x", "hunter22x"),
+        ("-secret\thunter22x", "hunter22x"),
+        ("---secret abc123def456", "abc123def456"),
+        ("_-password hunter22x", "hunter22x"),
+        ("a-b-c-d-e-f-g-h-i-j-password hunter22x", "hunter22x"),
+    ],
+)
+def test_n9_a_single_dash_flag_before_whitespace(text: str, secret: str) -> None:
+    """N9: a single-dash or `_` flag (any run of them), and a qualifier of
+    any number of joined segments, before a whitespace-separated value."""
+    _gone(text, secret)
+    for kept in ("-token bucket", "-secret santa", "_token ok", "use -session expired"):
+        for surface, out in _both(kept):
+            assert out == kept, (surface, out)
+
+
+@pytest.mark.parametrize(
+    "text", ["PGPassword abc123def456", "DBPassword hunter22x", "APIToken abc123def456"]
+)
+def test_n4b_an_acronym_glued_to_a_titlecase_key(text: str) -> None:
+    """N4b: `PGPassword` (libpq) is a key; `Ed25519PrivateKey X509Cert` and
+    random-case glued keys (`gby3zPassword x`, the maintainer's decision 4)
+    stay prose."""
+    _gone(text, text.split()[-1])
+    for kept in ("Ed25519PrivateKey X509Cert", "RSAPrivateKey Rsa2048Key"):
+        for surface, out in _both(kept):
+            assert out == kept, (surface, out)
+
+
+def test_n8_the_diagnostic_qualifiers_are_the_stated_class() -> None:
+    """The qualifiers under which `code` keeps its value are exactly the ones
+    the differential's class C12 states, and none names a credential."""
+    from pmcp.auth import _STATUS_CODE_QUALIFIERS
+
+    assert set(_STATUS_CODE_QUALIFIERS) == set(G.DIAGNOSTIC_CODE_QUALIFIERS)
+    assert not set(G.CREDENTIAL_CODE_QUALIFIERS) & set(_STATUS_CODE_QUALIFIERS)
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        'see https://:v1OW9iGwM@h.example:99999?SID=%69wX2caGkT7Xjb8Ll"',
+        'https://h.example/?token=abc123def456"',
+        'url https://h.example/?q=1&sid=hunter22x" more',
+    ],
+)
+def test_a_url_never_ends_on_the_backslash_of_an_escaped_quote(text: str) -> None:
+    """In a serialised leaf a URL runs up to the `\\` of the closing `\\"`; a
+    query-value span that ate it broke the JSON, and the dict came back as a
+    string (the URL axis found it; main kept such a URL when its port did not
+    parse)."""
+    result = _process({"t": text})
+    assert isinstance(result, dict), result
+    for surface, out in _both(json.dumps({"t": text})):
+        json.loads(out)
+
+
+#: The false positives each rev-9 narrowing was written for (the rev-10
+#: audit's guard list): `(text, engine, policy, dict leaf)`, `None` meaning
+#: unchanged. Four entries changed from rev 9, each commented (and four more
+#: after rev 11: a pair right after `Bearer`, as on main); every other
+#: entry is exactly rev 9's output.
+_FALSE_POSITIVE_TABLE: list[tuple[str, str | None, str | None, str | None]] = [
+    # N11 is never read directly after the scheme (the maintainer's decision
+    # after rev 11): what follows `Bearer` is its value, as on main
+    (
+        "token_type=Bearer expires_in=3600",
+        *(
+            "token_type=Bearer [REDACTED]",
+            "token_type=Bearer [REDACTED]",
+            "token_type=Bearer [REDACTED]",
+        ),
+    ),
+    ('{"token_type": "Bearer"}', None, None, None),
+    ("token_type: Bearer, expires_in: 3600", None, None, None),
+    ('{"token_type":"Bearer","expires_in":3600}', None, None, None),
+    ("token_type=bearer&expires_in=3600", None, None, None),
+    ("Missing bearer token", None, None, None),
+    ("the bearer of bad news", None, None, None),
+    (
+        'Bearer realm="api"',
+        *('Bearer [REDACTED]"', 'Bearer [REDACTED]"', 'Bearer [REDACTED]"'),
+    ),
+    (
+        'WWW-Authenticate: Bearer realm="x", error="invalid_token"',
+        *(
+            'WWW-Authenticate: Bearer [REDACTED]", error="invalid_token"',
+            'WWW-Authenticate: Bearer [REDACTED]", error="invalid_token"',
+            'WWW-Authenticate: Bearer [REDACTED]", error="invalid_token"',
+        ),
+    ),
+    (
+        "token_type: Bearer\nexpires_in: 3600",
+        "token_type: Bearer\nexpires_in: 3600",
+        "token_type: Bearer\nexpires_in: 3600",
+        "token_type: [REDACTED] 3600",
+    ),
+    ("auth: Bearer", None, None, None),
+    (
+        "scheme=Bearer scope=read",
+        *(
+            "scheme=Bearer [REDACTED]",
+            "scheme=Bearer [REDACTED]",
+            "scheme=Bearer [REDACTED]",
+        ),
+    ),
+    (
+        "arn:aws:secretsmanager:us-east-1:123456789012:secret:MyDbPassword-AbCdEf",
+        None,
+        None,
+        None,
+    ),
+    (
+        "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/db/password-Xk9mQ2",
+        None,
+        None,
+        None,
+    ),
+    ("arn:aws:iam::123456789012:role/token-refresher", None, None, None),
+    ("urn:ietf:params:oauth:token-type:access_token", None, None, None),
+    ("urn:ietf:params:oauth:grant-type:token-exchange", None, None, None),
+    ("at 12:30:token expired", None, None, None),
+    ("ns:token bucket", None, None, None),
+    (
+        "token:\nthe bearer of",
+        "token:\nthe bearer of",
+        "token:\nthe bearer of",
+        "token:[REDACTED] bearer of",
+    ),
+    (
+        "token:\n  - item",
+        "token:\n  - item",
+        "token:\n  - item",
+        "token:[REDACTED]  - item",
+    ),
+    (
+        "Set the Authorization:\nheader first",
+        "Set the Authorization:\nheader first",
+        "Set the Authorization:\nheader first",
+        "Set the Authorization:[REDACTED] first",
+    ),
+    (
+        "password:\n\n- bullet",
+        "password:\n\n- bullet",
+        "password:\n\n- bullet",
+        "password:[REDACTED] bullet",
+    ),
+    (
+        "token:\n\nThe next paragraph",
+        "token:\n\nThe next paragraph",
+        "token:\n\nThe next paragraph",
+        "token:[REDACTED] next paragraph",
+    ),
+    ("Secret\n\nIngredient list", None, None, None),
+    (
+        "password:\r\n\r\nSee the docs",
+        "password:\r\n\r\nSee the docs",
+        "password:\r\n\r\nSee the docs",
+        "password:[REDACTED] the docs",
+    ),
+    ("session\n\nexpired", None, None, None),
+    ("token\n\n", None, None, None),
+    (
+        "Enter your password:\n\n> ",
+        "Enter your password:\n\n> ",
+        "Enter your password:\n\n> ",
+        "Enter your password:[REDACTED] ",
+    ),
+    (
+        "token:\n\n  - a\n  - b",
+        "token:\n\n  - a\n  - b",
+        "token:\n\n  - a\n  - b",
+        "token:[REDACTED]  - a\n  - b",
+    ),
+    ("caf\xe9 tokenizer=bert", None, None, None),
+    ("na\xefve session expired", None, None, None),
+    ("\u201ctoken\u201d bucket", None, None, None),
+    ("\u2022token bucket", None, None, None),
+    (
+        "\u5bc6\u7801 token \u8fc7\u671f",
+        "\u5bc6\u7801 token \u8fc7\u671f",
+        "\u5bc6\u7801 token \u8fc7\u671f",
+        "\u5bc6\u7801 token [REDACTED]",
+    ),
+    ("if (token === expected)", None, None, None),
+    ("if token == expected:", None, None, None),
+    # rev 10 (N1): `::` is a path -- the engine keeps it; the `token` policy default still reads `:` as its separator
+    ("token::Type", "token::Type", "token: [REDACTED]", "token: [REDACTED]"),
+    ("std::secret::Holder", None, None, None),
+    # rev 10 (N1): as `token::Type`
+    ("secret::new()", "secret::new()", "secret: [REDACTED]", "secret: [REDACTED]"),
+    ("password === confirm", None, None, None),
+    ("a::token::b", "a::token::b", "a::token: [REDACTED]", "a::token: [REDACTED]"),
+    ('password="', None, None, None),
+    (
+        'He said "token: x',
+        'He said "token: [REDACTED]',
+        'He said "token:[REDACTED]',
+        'He said "token:[REDACTED]',
+    ),
+    ('"password": "', None, None, None),
+    ("token='", None, None, None),
+    ("it's the password's fault", None, None, None),
+    ("Bearer (see RFC 6750)", None, None, None),
+    # rev 10 (N6): the inside of a bracketed Authorization value is redacted, as main redacted `[required]`
+    (
+        "Authorization: [required]",
+        "Authorization: [[REDACTED]]",
+        "Authorization: [[REDACTED]]",
+        "Authorization: [[REDACTED]]",
+    ),
+    ('authorization: {"type": "bearer"}', None, None, None),
+    ('"authorization": ["read", "write"]', None, None, None),
+    ('Bearer "realm"', None, None, None),
+    ("use the Bearer [scheme]", None, None, None),
+    # rev 10 (N6): the bracket stays; rev 9 ate the opening one
+    (
+        "Authorization: (none)",
+        "Authorization: ([REDACTED])",
+        "Authorization: ([REDACTED])",
+        "Authorization: ([REDACTED])",
+    ),
+    ("error_code=E_TIMEOUT_42", None, None, None),
+    ("status_code=HTTP_401", None, None, None),
+    ("exit_code=137", None, None, None),
+    ("zip_code=94105", None, None, None),
+    ("country_code=US", None, None, None),
+    ("lang_code=en-US", None, None, None),
+    ("response_code=ERR_42x", None, None, None),
+    ("error_code=invalid_grant", None, None, None),
+    ("-token bucket", None, None, None),
+    ("-secret santa", None, None, None),
+    ("_token ok", None, None, None),
+    ("use -session expired", None, None, None),
+    ("token-based auth", None, None, None),
+    ("password_ reset", None, None, None),
+    ("the tokenizer=bert", None, None, None),
+    ("token bucket", None, None, None),
+    ("session expired", None, None, None),
+]
+
+
+def test_the_false_positive_guard_list_holds() -> None:
+    assert (
+        len(_FALSE_POSITIVE_TABLE) == len({t for t, *_ in _FALSE_POSITIVE_TABLE}) == 71
+    )
+    # four fewer unchanged entries after rev 11: a pair right after `Bearer`
+    assert sum(1 for _, e, *_ in _FALSE_POSITIVE_TABLE if e is None) >= 51
+    policy = PolicyManager()
+    for text, engine, pol, leaf in _FALSE_POSITIVE_TABLE:
+        assert _engine(text) == (text if engine is None else engine), text
+        assert policy.redact_secrets(text) == (text if pol is None else pol), text
+        result = policy.process_output({"t": text}, redact=True)["result"]
+        assert result == {"t": text if leaf is None else leaf}, (text, result)
+
+
+def test_sources_hold_no_literal_control_or_separator_characters() -> None:
+    """Review tooling refuses a bundle containing a transport-active control
+    character (a literal U+2028 in this file blocked every external seat on
+    plan rev 9): write such characters as escapes, in code and tests."""
+    import unicodedata
+
+    root = Path(__file__).resolve().parents[1]
+    offenders = []
+    for path in [
+        root / "src" / "pmcp" / "auth.py",
+        root / "src" / "pmcp" / "policy" / "policy.py",
+        Path(__file__),
+        Path(__file__).parent / "_redaction_grammar.py",
+    ]:
+        for lineno, line in enumerate(path.read_text("utf-8").split("\n"), 1):
+            for ch in line:
+                if (
+                    unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") and ch != "\t"
+                ) or ch == "\x85":
+                    offenders.append(f"{path.name}:{lineno}: {ch!r}")
+    assert offenders == [], offenders
diff --git a/tests/test_redaction_floor.py b/tests/test_redaction_floor.py
new file mode 100644
index 0000000000000000000000000000000000000000..b95aa07f92c167b1dfa9c28582d36c45b24db8e8
--- /dev/null
+++ b/tests/test_redaction_floor.py
@@ -0,0 +1,1533 @@
+"""Main's rules as the redactor's floor (Consiliency/pmcp#234).
+
+`pmcp.redaction_floor` replays main's redaction rules and hands the redactor
+every span main removed; the redactor applies each one unless a named
+suppression predicate drops it. The contract -- never worse than main except
+by a named suppression -- is proven here in four parts:
+
+* **Fidelity.** The replay's text equals main's output, byte for byte, on
+  every input (`tests/_main_redactor.py` is main's code, vendored verbatim).
+  Its keyword step is a linear matcher: it yields exactly the matches main's
+  real regex does.
+* **Construction.** On every input and surface, every character a floor span
+  covers is removed from the output unless a named predicate dropped that
+  span, and the JSON adjustment keeps syntax only.
+* **Differential.** Wherever a piece main removes survives here, a named
+  predicate fired on a floor span over that piece -- no hand classification.
+* **Predicates.** Each fires on its false positive and not on a
+  credential-shaped value.
+
+Then the board's B1-B5 against rev 10 (each red on `e79d75f`, green here,
+with a mutant that turns it red again) and the timing guards.
+"""
+
+from __future__ import annotations
+
+import multiprocessing
+import json
+import random
+import re
+import time
+from collections import Counter
+from collections.abc import Callable
+from urllib.parse import unquote
+
+import pytest
+
+import pmcp.redaction_floor as F
+from pmcp import keyword_matcher
+from pmcp.auth import (
+    AUTH_SECRET_QUERY_KEYS,
+    collect_redaction_spans,
+    merge_redaction_spans,
+    sanitize_auth_diagnostic,
+)
+from pmcp.policy import policy as policy_module
+from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
+from tests import _main_redactor as M
+from tests import _redaction_grammar as G
+from tests.test_redaction import _json_fuzz_corpus
+
+MAIN_PATTERNS = M.compiled_default_patterns()
+
+
+def _engine(text: str) -> str:
+    return sanitize_auth_diagnostic(text, max_length=None)
+
+
+def _policy(text: str) -> str:
+    return PolicyManager().redact_secrets(text)
+
+
+def _process(obj: object) -> object:
+    return PolicyManager().process_output(obj, redact=True, max_bytes=G.BIG)["result"]
+
+
+# ============================================================ fidelity ==== #
+
+
+def test_main_constants_are_frozen_as_main_wrote_them() -> None:
+    """The floor's copies of main's key sets and default patterns are main's,
+    and the live default list is main's again (rev 10's forms are additive)."""
+    assert F.MAIN_AUTH_SECRET_QUERY_KEYS == frozenset(M.AUTH_SECRET_QUERY_KEYS)
+    assert F.MAIN_DIAGNOSTIC_SECRET_KEYS == frozenset(M.AUTH_DIAGNOSTIC_SECRET_KEYS)
+    assert tuple(DEFAULT_REDACTION_PATTERNS) == F.MAIN_DEFAULT_REDACTION_PATTERNS
+    assert F.MAIN_DEFAULT_REDACTION_PATTERNS == tuple(M.DEFAULT_REDACTION_PATTERNS)
+    assert F.MAIN_AUTH_SECRET_QUERY_KEYS == frozenset(AUTH_SECRET_QUERY_KEYS)
+    assert set(G.DIAGNOSTIC_CODE_QUALIFIERS) == set(F.DIAGNOSTIC_CODE_QUALIFIERS)
+
+
+_MAIN_KEYWORD_RE = re.compile(
+    rf"(?i)\b([A-Za-z0-9_-]*(?:{F._main_keys_alternation(F.MAIN_DIAGNOSTIC_SECRET_KEYS)})"
+    r"[A-Za-z0-9_-]*)([\s:=]+)([A-Za-z0-9._~+/=-]{3,})"
+)
+
+
+def _real_keyword_matches(text: str) -> list[tuple[int, int, int, int]]:
+    """Main's keyword regex itself (module level: it runs in a worker)."""
+    return [
+        (m.start(), m.end(1), m.end(2), m.end())
+        for m in _MAIN_KEYWORD_RE.finditer(text)
+    ]
+
+
+def _linear_matches(text: str) -> list[tuple[int, int, int, int]]:
+    return list(F.main_keyword_matches(text))
+
+
+def test_the_keyword_regex_is_main_s_verbatim() -> None:
+    """The regex compared against is main's own expression, rebuilt from
+    main's key set as main's `sanitize_auth_diagnostic` builds it: on every
+    tier-1 text no other rule of main's touches, main's output is exactly
+    this regex's substitution."""
+    checked = 0
+    for row in G.corpus(1):
+        text = row.get("t", "")
+        if re.search(
+            r"(?i)https?://|bearer|authorization", text
+        ) or F._MAIN_JWT_RE.search(text):
+            continue
+        checked += 1
+        assert M.sanitize_auth_diagnostic(
+            text, max_length=None
+        ) == _MAIN_KEYWORD_RE.sub(r"\1\2[REDACTED]", text), text
+    assert checked > 5000, checked
+
+
+#: Fragments that exercise every decision the matcher makes: joiners and
+#: key fragments in a run (a word boundary at every joiner),
+#: separators over `[\s:=]` whose greedy backtracking hands `=` back to the
+#: value, value characters, Unicode `\w` characters glued before a run (no
+#: `\b`), and the case-fold partners `(?i)` admits (`ſ K ı İ`).
+_MATCHER_FRAGMENTS = (
+    "token", "password", "sid", "code", "api-key", "Api_Key", "set-cookie",
+    "SECRET", "tenant-id", "a", "b", "x1", "-", "_", "-_", "=", ":", " ",
+    "\t", "\n", "　", "\xe9", "ſ", "K", "ı", "İ",
+    ".", "/", "~", "+", '"', "'", "\\", "[", "é",
+)  # fmt: skip
+
+
+def _matcher_corpus(
+    seed: int, count: int, lengths: tuple[int, ...] = (1, 10, 100, 300)
+) -> list[str]:
+    rng = random.Random(seed)
+    texts = [
+        "".join(rng.choice(_MATCHER_FRAGMENTS) for _ in range(rng.randint(1, 16)))
+        for _ in range(count)
+    ]
+    # adversarial runs: long joiner-rich runs with and without a key, at
+    # every offset, ending in every separator/value shape
+    for base in ("a-", "a_", "a-_", "-a", "_-", "token-", "x-token", "tok-en", "a-b_c"):
+        for length in lengths:
+            for tail in (
+                "",
+                "=",
+                " = ab",
+                "==ab",
+                "=== ab",
+                "==== x",
+                ": abc",
+                "token=abc",
+            ):
+                texts.append(base * length + tail)
+                texts.append("password-" + base * length + tail)
+    return texts
+
+
+def _compare_with_timeout(
+    texts: list[str],
+    timeout: float,
+    reference: Callable[[str], list[tuple[int, int, int, int]]] | None = None,
+) -> list[str]:
+    """Differences between the linear matcher and the regex (``reference``),
+    the regex run in worker processes so a long input cannot hang the test:
+    past ``timeout`` the remaining input is reported (not skipped), and the
+    pool is terminated -- a running regex cannot be cancelled any other
+    way."""
+    reference = reference or _real_keyword_matches
+    problems: list[str] = []
+    pool = multiprocessing.get_context("spawn").Pool(4)
+    try:
+        pending = [(text, pool.apply_async(reference, (text,))) for text in texts]
+        deadline = time.monotonic() + timeout
+        for text, result in pending:
+            try:
+                expected = result.get(timeout=max(1.0, deadline - time.monotonic()))
+            except multiprocessing.TimeoutError:
+                problems.append(f"TIMEOUT {text[:60]!r} ({len(text)} chars)")
+                break
+            if _linear_matches(text) != expected:
+                problems.append(f"{text!r}: {expected} != {_linear_matches(text)}")
+    finally:
+        pool.terminate()
+        pool.join()
+    return problems
+
+
+def test_the_linear_keyword_matcher_equals_main_s_regex() -> None:
+    """20 000 random fragment strings, then 576 adversarial runs (up to 2.7
+    KB; main's regex in workers under a 60 s budget): the same
+    (start, key end, separator end, end) tuples as `finditer` of main's
+    regex."""
+    texts = _matcher_corpus(234, 20_000)
+    short, long = texts[:20_000], texts[20_000:]
+    assert [t for t in short if _linear_matches(t) != _real_keyword_matches(t)] == []
+    assert _compare_with_timeout(long, timeout=60) == []
+
+
+def test_the_linear_keyword_matcher_equals_main_s_regex_on_the_grammar_tier_1() -> None:
+    texts = [
+        spelling
+        for row in G.corpus(1)
+        if "t" in row
+        for spelling in (row["t"], json.dumps({"t": row["t"]}))
+    ]
+    mismatches = [t for t in texts if _linear_matches(t) != _real_keyword_matches(t)]
+    assert mismatches == []
+
+
+@pytest.mark.slow
+def test_the_linear_keyword_matcher_equals_main_s_regex_on_the_grammar_tier_2() -> None:
+    mismatches = []
+    for row in G.corpus(2):
+        if "t" not in row:
+            continue
+        for a in (True, False):
+            for i in (None, 2):
+                for text in (
+                    row["t"],
+                    json.dumps({"t": row["t"]}, ensure_ascii=a, indent=i),
+                ):
+                    if _linear_matches(text) != _real_keyword_matches(text):
+                        mismatches.append(text)
+    assert mismatches == []
+    texts = _matcher_corpus(2026, 200_000, lengths=(600, 1000))
+    assert [
+        t for t in texts[:200_000] if _linear_matches(t) != _real_keyword_matches(t)
+    ] == []
+    # the long adversarial runs: main's regex takes seconds on each
+    assert _compare_with_timeout(texts[200_000:], timeout=400) == []
+
+
+def _surface_texts(row: G.Row) -> list[tuple[str, str, bool]]:
+    """(surface, the text its redactor reads, policy?) for one row."""
+    if "o" in row:
+        return [("POo", json.dumps(row["o"], indent=2), True)]
+    t = row["t"]
+    dumped = [
+        (name, json.dumps({"t": t}, ensure_ascii=a, indent=i))
+        for name, a, i in G._SPELLINGS
+    ]
+    return [
+        ("E", t, False),
+        ("P", t, True),
+        *[("Ej" + name, d, False) for name, d in dumped],
+        *[("Pj" + name, d, True) for name, d in dumped],
+        ("POs", t, True),
+        ("POd", json.dumps({"t": t}, indent=2), True),
+    ]
+
+
+def _fidelity(texts: list[tuple[str, bool]]) -> tuple[list[str], int]:
+    bad = []
+    fallbacks = 0
+    for text, policy in texts:
+        patterns = MAIN_PATTERNS if policy else None
+        replayed, spans = F.replay(text, patterns)
+        fallbacks += sum(1 for s in spans if s.rule == "url.fallback")
+        main = (
+            M.redact_secrets(text, MAIN_PATTERNS)
+            if policy
+            else M.sanitize_auth_diagnostic(text, max_length=None)
+        )
+        if replayed != main:
+            bad.append(f"{'P' if policy else 'E'} {text!r}")
+    return bad, fallbacks
+
+
+def test_the_replay_reproduces_main_s_output_on_the_grammar_tier_1() -> None:
+    """Every text every tier-1 row puts on a surface: the replay's final text
+    is main's output, and no URL needed the whole-URL fallback."""
+    texts = {
+        (text, policy) for row in G.corpus(1) for _, text, policy in _surface_texts(row)
+    }
+    bad, fallbacks = _fidelity(sorted(texts))
+    assert bad == [] and fallbacks == 0, (bad[:10], fallbacks)
+
+
+@pytest.mark.slow
+def test_the_replay_reproduces_main_s_output_on_the_grammar_tier_2() -> None:
+    texts = {
+        (text, policy) for row in G.corpus(2) for _, text, policy in _surface_texts(row)
+    }
+    bad, fallbacks = _fidelity(sorted(texts))
+    assert bad == [] and fallbacks == 0, (bad[:10], fallbacks)
+
+
+def test_the_replay_reproduces_main_s_output_on_the_fuzz_and_random_text() -> None:
+    rng = random.Random(9)
+    alphabet = (
+        "password token Bearer Authorization: code= https://u:p@h.example/p?token=x&a=%41#f "
+        "api_key sk-abcdef1234 ghp_abcdefghij123 \\ \" ' = : ; , \n \t 　 é a- _ "
+        "aaaaaaaaaa.bbbbbbbbbb.cccccccccc [REDACTED] http://[::1 :99999/ "
+    ).split(" ")
+    texts = [
+        (json.dumps(obj, indent=2), policy)
+        for obj in _json_fuzz_corpus()
+        for policy in (False, True)
+    ]
+    for _ in range(5000):
+        text = "".join(rng.choice(alphabet) + rng.choice(("", " ")) for _ in range(12))
+        texts += [(text, False), (text, True)]
+    bad, fallbacks = _fidelity(texts)
+    assert bad == [], bad[:10]
+    assert fallbacks == 0
+
+
+def test_the_url_step_labels_what_main_drops() -> None:
+    """Main's `ValueError` arm (a port it cannot parse) keeps the first 400
+    characters of the URL, fragment dropped; the floor labels both, and the
+    redactor drops them as main did."""
+    text = "see https://h.example:99999/" + "a" * 420 + "?q=MYSECRETVAL#frag x"
+    _, spans = F.replay(text)
+    rules = {s.rule for s in spans}
+    assert {"url.overflow", "url.fragment"} <= rules, rules
+    out = _engine(text)
+    assert "MYSECRETVAL" not in out and "frag" not in out and out.endswith(" x")
+    userinfo = "https://user:pw123456@h.example/p?token=abc&x=1#f"
+    assert {s.rule for s in F.replay(userinfo)[1]} == {
+        "url.userinfo",
+        "url.query",
+        "url.fragment",
+    }
+
+
+# ========================================================= construction ==== #
+
+
+def _board_rows() -> list[G.Row]:
+    """The board's multi-pair and wide-separator grids (rev 10's B1, B2, B4,
+    B5), which the grammar's one-pair rows cannot produce."""
+    rows: list[G.Row] = []
+
+    def add(text: str, *secrets: str) -> None:
+        rows.append({"t": text, "pieces": list(secrets), "f": None, "value": "x"})
+
+    values = ("hunter2x", "q7Zp2Lk9Wx4R", "s3cr3tvalue")
+    for k1 in (
+        "password",
+        "api_key",
+        "token",
+        "secret",
+        "code",
+        "sid",
+        "Authorization",
+        "cookie",
+    ):
+        for op in ("=", ":", ": "):
+            for brk in ("\n", "\t", "\r\n", "\n\n", "\xe9", " \n"):
+                for k2 in ("password", "token", "cookie", "api_key", "session"):
+                    for v in values:
+                        add(f"{k1}{op}{brk}{k2}: {v}", v)
+                        add(f"{k1}{op}{brk}{k2}={v} rest", v)
+    for pre in ("--", "-", "x-", "X-", "x_", "proxy_", "access_", "auth-", "api.", ""):
+        for v in (
+            "hunter2x",
+            "s3cr3tvalue",
+            "abc-def_1.2",
+            "tok_12345",
+            "q7Zp2Lk9Wx4R",
+            "Zm9vOmJhcg==",
+        ):
+            add(f"mycli {pre}bearer {v} --verbose", v)
+            add(f"mycli {pre}Bearer {v}", v)
+    for head in ("abcdef", "q7Zp2Lk9Wx4R"):
+        for ch in "\"'()[]{}<>=|\\!@#$%^&*`?/:":
+            add(f"x Bearer {head}{ch}SECRETPART end", "SECRETPART")
+            add(f"Authorization: {head}{ch}SECRETPART end", "SECRETPART")
+            add(f"Authorization: Bearer {head}{ch}SECRETPART end", "SECRETPART")
+    add("x Bearer 'hunter2x null", "hunter2x")
+    # URLs: userinfo, fragments, a port main cannot parse (its `ValueError`
+    # arm keeps the first 400 characters), every query key main redacts
+    add("see https://user:pa@ssw0rdXq@host.example/p ok", "ssw0rdXq")
+    add("https://tok3nABCDEF@github.com/x", "tok3nABCDEF")
+    add(
+        "https://h.example/cb#state=xyzQ12&access_token=abcDEF123",
+        "abcDEF123",
+        "xyzQ12",
+    )
+    add("https://h.example:99999/p?q=1#frag_secretXYZ", "frag_secretXYZ")
+    add("https://h.example:99999/" + "a" * 420 + "?q=MYSECRETVAL", "MYSECRETVAL")
+    add("https://[::1/p?token=hunter2&x=y#zz_secret", "zz_secret", "hunter2")
+    for key in sorted(F.MAIN_AUTH_SECRET_QUERY_KEYS):
+        add(f"https://h.example/p?{key}=plainword&n=1", "plainword")
+        add(f"https://h.example/p?{key.upper()}=12345", "12345")
+    add("https://h.example/p?to%6Ben=hunter2", "hunter2")
+    add("https://h.example/p?token=a+b+cdef", "cdef")
+    # multi-pair configuration files (.env, YAML)
+    add(
+        "DB_HOST=db.internal\nDB_PASSWORD=\nSESSION_SECRET=s3cr3tvalue\n", "s3cr3tvalue"
+    )
+    add("DB_PASSWORD=\nAPI_KEY=s3cr3tvalue\n", "s3cr3tvalue")
+    add("password:\n  session: s3cr3tvalue\n", "s3cr3tvalue")
+    add("smtp_password=\ncookie=s3cr3tvalue\n", "s3cr3tvalue")
+    add("password=\tsecret=hunter2", "hunter2")
+    add("db:\n  host: x\n  password:\n  client_secret: s3cr3tvalue\n", "s3cr3tvalue")
+    add("db:\n  password:\n  session: q7Zp2Lk9Wx4R\n", "q7Zp2Lk9Wx4R")
+    alphabet = (" ", ":", "=", "\n", "\t")
+    rng = random.Random(55)
+    for _ in range(3000):
+        sep = "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 5)))
+        add(f"k {rng.choice(('password', 'token'))}{sep}hunter2x e", "hunter2x")
+    return rows  # fmt: skip
+
+
+def _main_observe(row: G.Row) -> str:
+    """Main's observation code of a row, from main's vendored code."""
+
+    def process(obj: object) -> object:
+        if isinstance(obj, str):
+            return M.redact_secrets(obj, MAIN_PATTERNS)
+        out = M.redact_secrets(json.dumps(obj, indent=2), MAIN_PATTERNS)
+        try:
+            return json.loads(out)
+        except json.JSONDecodeError:
+            return out
+
+    return G.observe(
+        row,
+        lambda t: M.sanitize_auth_diagnostic(t, max_length=None),
+        lambda t: M.redact_secrets(t, MAIN_PATTERNS),
+        process,
+    )
+
+
+def _construction(rows: list[G.Row]) -> tuple[Counter[str], list[str]]:
+    """Parts A and B for ``rows``: the per-predicate count of suppressed floor
+    spans (A) and of worse pieces each explained (B), and every violation."""
+    policy = PolicyManager()
+    counts: Counter[str] = Counter()
+    problems: list[str] = []
+    for row in rows:
+        floors: dict[str, tuple[str, list[F.FloorSpan]]] = {}
+        for surface, text, is_policy in _surface_texts(row):
+            spans, _ = F.floor_spans(
+                text, policy._redaction_regexes if is_policy else None
+            )
+            floors[surface] = (text, spans)
+            applied = (
+                policy.redaction_spans(text)
+                if is_policy
+                else collect_redaction_spans(text)
+            )
+            covered = bytearray(len(text))
+            for start, end, _ in merge_redaction_spans(text, applied):
+                covered[start:end] = b"\x01" * (end - start)
+            for span in spans:
+                if span.suppressed_by is not None:
+                    counts["A:" + span.suppressed_by] += 1
+                    if span.suppressed_by not in F.SUPPRESSIONS:
+                        problems.append(f"unnamed {span.suppressed_by}")
+                    continue
+                kept = set()
+                for a, b in span.json_kept:
+                    kept.update(range(a, b))
+                    if any(c not in F.JSON_SYNTAX and c != "\\" for c in text[a:b]):
+                        problems.append(
+                            f"{surface} json kept content {text[a:b]!r} in {text!r}"
+                        )
+                missing = [
+                    p
+                    for p in range(span.start, span.end)
+                    if p not in kept and not covered[p]
+                ]
+                if missing:
+                    problems.append(
+                        f"{surface} {span.rule}/{span.part} {text[span.start : span.end]!r} "
+                        f"not removed from {text!r}"
+                    )
+        # B: the differential, explained by the report alone
+        main = _main_observe(row)
+        here = G.observe(row, _engine, policy.redact_secrets, _process_with(policy))
+        pieces = G.pieces(row)
+        for surface in G.worse_surfaces(row, main, here):
+            if surface.endswith(".type"):
+                problems.append(
+                    f"{surface}: {row.get('t', row.get('o'))!r} lost its dict"
+                )
+                continue
+            names = ("POo",) if "o" in row else G.TEXT_SURFACES
+            index = names.index(surface)
+            text, spans = floors[surface]
+            for i, piece in enumerate(pieces):
+                if (
+                    not (int(here[index], 32) >> i) & 1
+                    or (int(main[index], 32) >> i) & 1
+                ):
+                    continue
+                explained = {
+                    s.suppressed_by
+                    for s in spans
+                    if s.suppressed_by is not None
+                    for m in re.finditer(re.escape(piece), text)
+                    if s.start < m.end() and m.start() < s.end
+                }
+                if not explained and _kept_with_insertions(
+                    piece, _main_output(surface, text)
+                ):
+                    counts["R:main kept it, re-encoded"] += 1
+                    continue
+                if not explained:
+                    problems.append(
+                        f"{surface} {piece!r} unexplained in {row.get('t', row.get('o'))!r}"
+                    )
+                counts.update("B:" + name for name in explained)
+    return counts, problems
+
+
+def _main_output(surface: str, text: str) -> str:
+    """Main's output for the text a surface's redactor reads (no truncation
+    in these corpora, so `process_output` is `redact_secrets`)."""
+    if surface.startswith("E"):
+        return M.sanitize_auth_diagnostic(text, max_length=None)
+    return M.redact_secrets(text, MAIN_PATTERNS)
+
+
+def _kept_with_insertions(piece: str, out: str) -> bool:
+    """Is ``piece`` in main's output with a few characters inserted into it
+    (as written or percent-decoded)? Main's URL rewrite gives a bare query
+    key an `=` (`?urn:x:tok.` becomes `?urn%3Ax%3Atok=.`): the piece's
+    characters are all still there, which the piece metric reads as a
+    removal. Decided from main's own output, not from the floor."""
+    pattern = re.compile(
+        re.escape(piece[0])
+        + "".join(r"[^\s]{0,3}?" + re.escape(char) for char in piece[1:])
+    )
+    return any(pattern.search(form) for form in (out, unquote(out)))
+
+
+def _process_with(policy: PolicyManager) -> Callable[[object], object]:
+    return lambda obj: policy.process_output(obj, redact=True, max_bytes=G.BIG)[
+        "result"
+    ]
+
+
+def test_the_vendored_main_is_the_recorded_oracle() -> None:
+    """`_main_observe` (main's vendored code) reproduces the recorded oracle on
+    tier 1, so part B's `main` is main."""
+    oracle = json.loads(
+        __import__("gzip").decompress(
+            __import__("base64").b64decode(
+                (
+                    __import__("pathlib").Path(__file__).parent
+                    / "fixtures"
+                    / "redaction_main_oracle.b64"
+                ).read_text()
+            )
+        )
+    )
+    rows = G.corpus(1)
+    assert [_main_observe(row) for row in rows] == oracle["grammar"][: len(rows)]
+
+
+@pytest.mark.parametrize("rows", ["tier1", "board"])
+def test_construction_holds_on_the_grammar_tier_1_and_the_board_rows(rows: str) -> None:
+    """A: every unsuppressed floor span is removed, on every surface; B:
+    every piece main removes and this keeps is explained by a named
+    predicate. Both over tier 1 and the board's grids, with no hand
+    classification anywhere."""
+    counts, problems = _construction(G.corpus(1) if rows == "tier1" else _board_rows())
+    assert problems == [], "\n".join(problems[:25])
+    assert {
+        name.split(":", 1)[1] for name in counts if not name.startswith("R:")
+    } <= set(F.SUPPRESSIONS)
+    assert sum(v for k, v in counts.items() if k.startswith("A:")) > 100, counts
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("block", G.BLOCKS)
+def test_construction_holds_on_the_grammar_tier_2(block: str) -> None:
+    rows = [row for row in G.corpus(2) if row["block"] == block]
+    counts, problems = _construction(rows)
+    assert problems == [], "\n".join(problems[:25])
+    assert {
+        name.split(":", 1)[1] for name in counts if not name.startswith("R:")
+    } <= set(F.SUPPRESSIONS)
+
+
+def test_construction_holds_on_the_json_fuzz() -> None:
+    rows = [
+        {"o": obj, "pieces": [], "f": None, "value": ""} for obj in _json_fuzz_corpus()
+    ]
+    _, problems = _construction(rows)
+    assert problems == [], "\n".join(problems[:25])
+    for obj in _json_fuzz_corpus():
+        for is_policy in (False, True):
+            text = json.dumps(obj, indent=2)
+            out = _policy(text) if is_policy else _engine(text)
+            json.loads(out)
+        assert isinstance(_process(obj), dict)
+
+
+# =========================================================== predicates ==== #
+
+#: The named suppressions, frozen: adding one is a reviewed change.
+NAMED = (
+    "separator_syntax",
+    "policy_keyword_word",
+    "scheme_word",
+    "wrapper_syntax",
+    "N3",
+    "N10",
+    "C12",
+    "C3a",
+    "C3",
+    "N11",
+    "N4",
+    "C4",
+    "C5",
+    "C6",
+    "C7",
+    "C8",
+    "C10",
+    "C11",
+)
+
+
+def test_the_suppressions_are_the_named_set() -> None:
+    assert tuple(F.SUPPRESSIONS) == NAMED
+    stated = set(G.CLASSES)
+    syntax = {
+        "separator_syntax",
+        "policy_keyword_word",
+        "scheme_word",
+        "wrapper_syntax",
+    }
+    assert set(NAMED) - syntax <= stated, set(NAMED) - syntax - stated
+
+
+def _fired(text: str, policy: bool) -> dict[str, list[str]]:
+    spans, _ = F.floor_spans(text, MAIN_PATTERNS if policy else None)
+    fired: dict[str, list[str]] = {}
+    for span in spans:
+        if span.suppressed_by is not None:
+            fired.setdefault(span.suppressed_by, []).append(text[span.start : span.end])
+    return fired
+
+
+#: name -> (its false positive, the surface, what it keeps; a credential-
+#: shaped neighbour it must not fire on, and the credential). For the classes
+#: that are not about the value (N3 N10 C12 C3a N4 N11: an identifier, a
+#: resource name, a diagnostic qualifier, a whitespace `code`, a pair), the
+#: neighbour is the same credential under a real key.
+PREDICATE_CASES: dict[str, tuple[str, bool, str, str, str]] = {
+    "separator_syntax": ('password: "abc"', True, ' "', 'password: "hunter22x"', "hunter22x"),
+    "policy_keyword_word": ("token v2", True, "token ", "token abc123def456", "abc123def456"),
+    "scheme_word": ('Authorization: Bearer "abc123def456"', False, "Bearer ", "Authorization: abc123def456", "abc123def456"),
+    "wrapper_syntax": ('Bearer "hunter22x"', False, '"', 'Bearer abcdef"SECRETPART', "SECRETPART"),
+    "N3": ("a" * 26 + "password=hunter", False, "hunter", "dbpassword=hunter22x", "hunter22x"),
+    "N10": ("arn:aws:iam::1:secret:hunter22x", False, "hunter22x", "x secret:hunter22x", "hunter22x"),
+    "C12": ("error_code=E_TIMEOUT_42", False, "E_TIMEOUT_42", "otp_code=abc123def", "abc123def"),
+    "C3a": ("the code abc123def", False, "abc123def", "code=abc123def", "abc123def"),
+    "C3": ("token bucket", False, "bucket", "token abc123def456", "abc123def456"),
+    "N11": ("token expires_in=3600", False, "expires_in=3600", "token abc123def456", "abc123def456"),
+    "N4": ("gby3zPassword x9y8z7w6", False, "x9y8z7w6", "CLIENTSECRET abc123def456", "abc123def456"),
+    "C4": ("if token == expected", False, "expected", "password == hunter22x", "hunter22x"),
+    "C5": ("token_type=Bearer", False, "Bearer", "token_type=abc123def456", "abc123def456"),
+    "C6": ("unicode=input", False, "input", "dbpassword=hunter22x", "hunter22x"),
+    "C7": ('{"a": "secret: ", "b": "y"}', True, ",", "secret: hunter22x", "hunter22x"),
+    "C8": ("token:\nthe end", False, "the", "password:\nhunter22x", "hunter22x"),
+    "C10": ("code: -32601", False, "-32601", "code=abc123def", "abc123def"),
+    "C11": ("Missing bearer token", False, "token", "Bearer abc123def456", "abc123def456"),
+}  # fmt: skip
+
+
+def test_every_named_suppression_has_a_case() -> None:
+    assert set(PREDICATE_CASES) == set(NAMED)
+
+
+@pytest.mark.parametrize("name", NAMED)
+def test_each_suppression_fires_on_its_false_positive_only(name: str) -> None:
+    fp, policy, kept, credential_text, credential = PREDICATE_CASES[name]
+    fired = _fired(fp, policy)
+    assert kept in fired.get(name, []), (name, fired)
+    surface = _policy if policy else _engine
+    assert kept in surface(fp)
+    # the credential-shaped neighbour: this predicate keeps none of the
+    # credential (a syntax predicate still keeps the neighbour's syntax), and
+    # the credential is gone
+    neighbour = _fired(credential_text, policy).get(name, [])
+    assert not any(credential in kept for kept in neighbour), neighbour
+    assert credential not in surface(credential_text), surface(credential_text)
+
+
+@pytest.mark.parametrize(
+    "name", ["separator_syntax", "policy_keyword_word", "scheme_word", "wrapper_syntax"]
+)
+def test_a_syntax_suppression_never_keeps_content(name: str) -> None:
+    """The four syntax predicates decide from the removed text alone, which
+    must be the pattern's own vocabulary or quote/bracket/whitespace: a
+    credential in that part is never kept."""
+    part = {"separator_syntax": "syntax", "policy_keyword_word": "keyword",
+            "scheme_word": "scheme", "wrapper_syntax": "wrap_close"}[name]  # fmt: skip
+    for removed in ("hunter22x", '"abc123"', "Bearer x", "token1", "(q7Zp2Lk9Wx4R)"):
+        span = F.FloorSpan(0, len(removed), "bearer", part=part, removed=removed)
+        assert not F.SUPPRESSIONS[name](span), (name, removed)
+
+
+# ================================================== the board's B1-B5 ==== #
+#
+# Each is red on rev 10 (`e79d75f`) and green here; each mutant disables the
+# mechanism that closes it and turns it red again.
+
+
+def _b1() -> None:
+    """B1: a key with an empty value, a line break, then another key -- on
+    the dict-leaf path the second pair's value survived rev 10."""
+    for leaf in (
+        "[db]\npassword =\nsecret = hunter2\n",
+        "[smtp]\npassword =\ncookie = hunter2",
+        "password:\npassword: hunter2",
+        "secret:\n\npassword: hunter2",
+    ):
+        out = json.dumps(_process({"t": leaf}))
+        assert "hunter2" not in out, (leaf, out)
+        for spelling in (json.dumps({"t": leaf}), json.dumps({"t": leaf}, indent=2)):
+            assert "hunter2" not in _engine(spelling), spelling
+            assert "hunter2" not in _policy(spelling), spelling
+
+
+def _b2() -> None:
+    """B2: `bearer` after `-` or `_` (a CLI flag, an `X-` header)."""
+    for text, secret in (
+        ("mycli --bearer s3cr3tvalue --verbose", "s3cr3tvalue"),
+        ("X-Bearer Zm9vOmJhcg==", "Zm9vOmJhcg=="),
+        ("access_bearer tok_12345", "tok_12345"),
+        ("-----END PRIVATE KEY-----Bearer hunter2x", "hunter2x"),
+    ):
+        assert secret not in _policy(text), text
+        if not text.startswith("access_"):
+            assert secret not in _engine(text), text
+
+
+def _b3_split(split: Callable[[str], int]) -> float:
+    started = time.perf_counter()
+    split("password" + ":=" * 33_000)
+    return time.perf_counter() - started
+
+
+def _b3() -> None:
+    """B3: the policy split loop is linear (rev 10: seconds on 66 KB). The
+    split alone, on 66 KB of `:=`: a linear scan takes milliseconds, the
+    quadratic one seconds -- two orders of magnitude either side of 0.5 s."""
+    assert _b3_split(policy_module._value_separator) < 0.5
+
+
+def _b4() -> None:
+    """B4: a Bearer/Authorization value runs past a quote or bracket, as
+    main's `[^\\s,;]+` did."""
+    for text in (
+        'x Bearer q7Zp2Lk9Wx4R"SECRETPART end',
+        "x Bearer abcdef(SECRETPART end",
+        'Authorization: q7Zp2Lk9Wx4R"SECRETPART end',
+        "Authorization: Bearer abcdef'SECRETPART end",
+        'x Bearer abcdef"SECRETPART end',
+    ):
+        for surface in (_engine, _policy):
+            assert "SECRETPART" not in surface(text), text
+    assert "hunter2x" not in _engine("x Bearer 'hunter2x null")
+
+
+def _b5() -> None:
+    """B5: separators of 4-5 characters mixing operators and whitespace."""
+    for text in (
+        "k password: : hunter2x e",
+        "password = = hunter2x",
+        "token\t= : hunter2x",
+    ):
+        assert "hunter2x" not in _engine(text), text
+        assert "hunter2x" not in _policy(text), text
+
+
+@pytest.mark.parametrize("check", [_b1, _b2, _b3, _b4, _b5], ids=lambda f: f.__name__)
+def test_rev_10_board_findings_are_closed(check: Callable[[], None]) -> None:
+    check()
+
+
+def _quadratic_split(full_match: str) -> int:
+    for i, char in enumerate(full_match):
+        if char in ":=" and full_match[i + 1 :].strip(" \t:="):
+            return i
+    return -1
+
+
+MUTANTS: dict[str, tuple[Callable[[], None], Callable[[pytest.MonkeyPatch], None]]] = {
+    # B1: no floor at all (rev 10's rules alone)
+    "B1": (
+        _b1,
+        lambda mp: mp.setattr(
+            "pmcp.auth.floor_spans", lambda text, patterns=None: ([], [])
+        ),
+    ),
+    # B2: main's bearer step gated as rev 10 gated it
+    "B2": (
+        _b2,
+        lambda mp: mp.setattr(
+            F,
+            "_MAIN_BEARER_RE",
+            re.compile(r"(?i)((?<![A-Za-z0-9_-])bearer\s+)[^\s,;]+"),
+        ),
+    ),
+    # B3: the quadratic split
+    "B3": (
+        _b3,
+        lambda mp: mp.setattr(policy_module, "_value_separator", _quadratic_split),
+    ),
+    # B4: main's Bearer and Authorization values cut at a quote or bracket,
+    # as rev 10 cut them
+    "B4": (
+        _b4,
+        lambda mp: (
+            mp.setattr(
+                F,
+                "_MAIN_BEARER_RE",
+                re.compile(r"(?i)(\bbearer\s+)[^\s,;\"'()\[\]{}<>]+"),
+            ),
+            mp.setattr(
+                F,
+                "_MAIN_AUTHORIZATION_RE",
+                re.compile(
+                    r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;\"'()\[\]{}<>]+"
+                ),
+            ),
+        ),
+    ),
+    # B5: the floor's separator capped at three characters
+    "B5": (
+        _b5,
+        lambda mp: mp.setattr(
+            keyword_matcher, "_SEP_RUN_RE", re.compile(r"[\s:=]{1,3}")
+        ),
+    ),
+}
+
+
+@pytest.mark.parametrize("finding", sorted(MUTANTS))
+def test_each_board_finding_has_a_killing_mutant(
+    finding: str, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    check, mutate = MUTANTS[finding]
+    mutate(monkeypatch)
+    with pytest.raises(AssertionError):
+        check()
+
+
+def _n11_after_bearer() -> None:
+    """N11 narrowed (maintainer, after rev 11's report): a `name=value` token
+    right after `Bearer`/`Authorization` is the credential, and a pair whose
+    value is credential-shaped is not prose -- both redacted, as on main;
+    `token expires_in=3600` stays prose."""
+    for text, secret in (
+        ("x Bearer abcdef=SECRETPART end", "SECRETPART"),
+        ("Authorization: Bearer abcdef=SECRETPART", "SECRETPART"),
+        ("token code=abcdefg1234x", "abcdefg1234x"),
+    ):
+        for surface in (_engine, _policy):
+            assert secret not in surface(text), (text, surface(text))
+    for prose in ("token expires_in=3600", "token code=404"):
+        assert _engine(prose) == prose and _policy(prose) == prose
+
+
+def _old_n11(span: F.FloorSpan) -> bool:
+    return (
+        span.rule in ("keyword", "policy:2", "bearer")
+        and span.part == "value"
+        and F._whitespace_sep(span)
+        and re.match(r"[A-Za-z_-]+=[^=]", F._sep_and_value(span)[1] + span.after)
+        is not None
+    )
+
+
+def test_n11_never_fires_after_a_scheme_or_on_a_credential() -> None:
+    _n11_after_bearer()
+
+
+def test_the_old_n11_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setitem(F.SUPPRESSIONS, "N11", _old_n11)
+    with pytest.raises(AssertionError):
+        _n11_after_bearer()
+
+
+# ============================================================== timing ==== #
+#
+# Linear time is asserted on a DETERMINISTIC work count, never on the clock
+# (rev 12's ratio tests flaked on a loaded host, T-1 of its board). Every
+# loop and string build of the redactor adds what it iterates over or copies
+# to `pmcp.redaction_floor.WORK` while a test holds it; a regex pass adds the
+# text it scans. The plan's complexity table says, per function, what each
+# count stands for and why it is bounded. What a count cannot see is a
+# regex's own backtracking: that is argued per pattern in the plan and
+# swept on the clock in the slow tier, with margins of an order of
+# magnitude. The wall-clock tests left in the default tier are smoke caps.
+
+
+def _entry_points() -> dict[str, Callable[[str], object]]:
+    policy = PolicyManager()
+    return {
+        "E": lambda t: sanitize_auth_diagnostic(t, max_length=None),
+        "P": policy.redact_secrets,
+        "POs": lambda t: policy.process_output(t, redact=True, max_bytes=G.BIG),
+        "POd": lambda t: policy.process_output({"t": t}, redact=True, max_bytes=G.BIG),
+    }
+
+
+def _work(run: Callable[[str], object], text: str) -> int:
+    F.WORK = [0]
+    try:
+        run(text)
+        return F.WORK[0]
+    finally:
+        F.WORK = None
+
+
+#: Allowed growth of the work count per 4x the input: 4x for linear work,
+#: plus the n log n of sorting spans (log2 of the size grows by 2 per 4x:
+#: at 16 KB -> 64 KB that is 16/14), plus 5%. A quadratic term gives 16x.
+_PER_4X = 4 * (16 / 14) * 1.05
+
+
+def _superlinear(shape: Callable[[int], str], sizes: tuple[int, ...]) -> list[str]:
+    """Entry points on which the work count of ``shape`` grows faster than
+    ``_PER_4X`` per step between consecutive ``sizes`` (each 4x the last)."""
+    assert all(n1 == 4 * n0 for n0, n1 in zip(sizes, sizes[1:])), sizes
+    found = []
+    for name, run in _entry_points().items():
+        counts = [_work(run, shape(n)) for n in sizes]
+        for n0, c0, c1 in zip(sizes, counts, counts[1:]):
+            if c1 > _PER_4X * max(c0, 1):
+                found.append(f"{name}: {n0}->{4 * n0} work {c0}->{c1}")
+    return found
+
+
+_ADVERSARIAL = {
+    "a-": "a-" * 33_000,
+    "a=": "a=" * 33_000,
+    "a-b=": "a-b=" * 16_500,
+    "token-=": "token-" * 11_000 + "=abc",
+    "password=b": "password=b " * 6000,
+    "a_": "a_" * 33_000,
+    "token-": "token-" * 11_000,
+    "password:=": "password" + ":=" * 33_000,
+    "pw_": ("password_" * 7334)[:66_000],
+    "escaped": "\\u00a0password" * 4400,
+    "url": "https://h.example/?" + "token=%41&" * 6600,
+}
+
+
+@pytest.mark.parametrize("name", sorted(_ADVERSARIAL))
+def test_timing_smoke_on_every_surface(name: str) -> None:
+    """A smoke cap, not the linearity proof: 66 KB of each adversarial shape
+    on every entry point in under 10 s (they take 0.1-1.3 s on dev0)."""
+    text = _ADVERSARIAL[name]
+    for label, run in _entry_points().items():
+        started = time.perf_counter()
+        run(text)
+        assert time.perf_counter() - started < 10.0, (name, label)
+
+
+def test_the_floor_itself_is_linear() -> None:
+    """The replay alone, on the work count: 16 KB -> 64 KB -> 256 KB."""
+    for shape in (
+        lambda n: "a-" * (n // 2),
+        lambda n: "https://h/?" + "password=x&" * (n // 11),
+    ):
+        counts = []
+        for n in (16_384, 65_536, 262_144):
+            F.WORK = [0]
+            try:
+                F.floor_spans(shape(n), MAIN_PATTERNS)
+                counts.append(F.WORK[0])
+            finally:
+                F.WORK = None
+        assert counts[1] <= _PER_4X * counts[0] and counts[2] <= _PER_4X * counts[1], (
+            counts
+        )
+
+
+def _never_returns(text: str) -> list[tuple[int, int, int, int]]:
+    """A stand-in reference that outlives any budget."""
+    time.sleep(3600)
+    return []
+
+
+def test_the_regex_comparison_reports_a_timeout_instead_of_hanging() -> None:
+    """A reference past the budget is reported as a TIMEOUT within it, and
+    its worker is terminated."""
+    started = time.perf_counter()
+    problems = _compare_with_timeout(["x"], timeout=2, reference=_never_returns)
+    assert problems and problems[0].startswith("TIMEOUT"), problems
+    assert time.perf_counter() - started < 30
+
+
+# ================================================ rev 11's board, round 1 ==== #
+#
+# Each finding: a check that is red on rev 11 (`3e49b95`) and green here, and
+# a mutant that restores rev 11's mechanism and turns the check red.
+
+
+def _time_ratio(
+    run: Callable[[str], object],
+    shape: Callable[[int], str],
+    small: int = 1_024,
+    large: int = 16_384,
+) -> float:
+    """Wall-clock growth over 16x the input, best of three at each size: a
+    linear path gives ~16x, a quadratic one ~256x. Used only where the work
+    count cannot see the cost (a regex's own backtracking) and only with a
+    threshold of 64x -- a factor of four from either."""
+
+    def best(text: str) -> float:
+        times = []
+        for _ in range(3):
+            started = time.perf_counter()
+            run(text)
+            times.append(time.perf_counter() - started)
+        return min(times)
+
+    return best(shape(large)) / max(best(shape(small)), 1e-4)
+
+
+#: Every shape the boards and the sweeps found, both rounds, and the
+#: composition families: the default tier's linearity proof, on the work
+#: count, at 16 KB -> 64 KB -> 256 KB on every entry point.
+LINEAR_SHAPES: dict[str, Callable[[int], str]] = {
+    "B-1 closers after a Bearer value": lambda n: "Bearer x" + ")" * n + "a",
+    "B-1 escaped quotes after Authorization": lambda n: "Authorization: x" + '\\"' * (n // 2) + "a",
+    "B-1 each closer after Authorization": lambda n: "Authorization: x" + ">" * n + "=",
+    "B-1b line breaks before a Bearer value": lambda n: "x Bearer" + "\n" * n + " abc",
+    "B-1b line breaks before a keyword value": lambda n: "token" + "\n" * n + " abc12",
+    "B-1c one policy value over many markers": lambda n: "arn:" + "secret:" * (n // 7) + "=abc123",
+    "B-3 Authorization over a rewritten URL": lambda n: "Authorization: https://h/?q" + "&a+b" * (n // 4),
+    "B-3 Bearer over a rewritten URL": lambda n: "Bearer https://h/?password=x" + "&a+b" * (n // 4),
+    "B-3 secret-keyed query pairs": lambda n: "https://h/?" + "password=x&" * (n // 11),
+    "B-3 policy value over a rewritten URL": lambda n: "see https://h/?password=x" + "&a+b" * (n // 4) + " end",
+    "B-4 keyword matches after resource names": lambda n: "arn:x " * (n // 12) + "password=abc123 " * (n // 32),
+    "B-4 keyword lists after resource names": lambda n: "arn:x " * (n // 12) + 'tokens: ["a1b2c3d4"] ' * (n // 42),
+    "B-4 keys inside resource names": lambda n: "arn:x:secret=abc123 " * (n // 20),
+    "backslash run before escapes": lambda n: "\\" * (n // 2) + "\\u00e9password=x" * 8,
+    "empty query pairs": lambda n: "https://h/?" + "&" * n + "a",
+    "key words after Bearer": lambda n: "x Bearer" + "api_key" * (n // 7) + '"',
+    "joined key run": lambda n: "token-" * (n // 6) + "=abc123def",
+    "word boundaries": lambda n: "a-" * (n // 2),
+    "operator run": lambda n: "password" + ":=" * (n // 2),
+    "markers in the input": lambda n: "password=" + "[REDACTED]" * (n // 10),
+    "Bearer values in a JSON document": lambda n: json.dumps({f"k{i}": "Bearer x" for i in range(n // 20)}),
+    "B-5 JWTs inside one re-encoded query value": lambda n: "https://h/?q=+" + "aaaaaaaaaa.bbbbbbbbbb.cccccccccc/" * (n // 33),
+    "B-5 sk- tokens inside one re-encoded query value": lambda n: "https://h/?q=+" + "sk-abcdef/" * (n // 10),
+    "B-5 tokens inside one re-encoded query key": lambda n: "https://h/?%41" + "ghp_abcdefghij/" * (n // 15),
+    "S-1 long joined keys": lambda n: ("a" * 170 + "_token" * 14 + "=Hunter2abc9 ") * (n // 267),
+}  # fmt: skip
+
+
+@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
+def test_every_shape_does_linear_work_on_every_entry_point(name: str) -> None:
+    """The work count grows linearly (up to the n log n of sorting spans)
+    from 16 KB to 64 KB to 256 KB on the engine, the policy surface and
+    `process_output` (string and dict). Deterministic: no clock."""
+    assert _superlinear(LINEAR_SHAPES[name], (16_384, 65_536, 262_144)) == []
+
+
+def _peak_memory(run: Callable[[str], object], text: str) -> int:
+    import tracemalloc
+
+    tracemalloc.start()
+    try:
+        run(text)
+        return tracemalloc.get_traced_memory()[1]
+    finally:
+        tracemalloc.stop()
+
+
+def _memory_superlinear(
+    shape: Callable[[int], str], labels: tuple[str, ...], sizes: tuple[int, ...]
+) -> list[str]:
+    entry = _entry_points()
+    found = []
+    for label in labels:
+        run = entry[label]
+        run(shape(1_024))  # first-use allocations (regex caches) out of the way
+        peaks = [_peak_memory(run, shape(n)) for n in sizes]
+        for n, small, large in zip(sizes, peaks, peaks[1:]):
+            if large > _PER_4X * small:
+                found.append(f"{label}: {n}->{4 * n} peak {small}->{large}")
+    return found
+
+
+@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
+def test_every_shape_uses_linear_memory(name: str) -> None:
+    """Peak traced memory grows linearly from 8 KB to 32 KB to 128 KB on the
+    engine and on `process_output` of a dict (B-3 and B-5 were quadratic in
+    memory as well as time). Deterministic: the same allocations every run.
+    The slow tier repeats it at 16 KB -> 256 KB on three entry points."""
+    assert (
+        _memory_superlinear(LINEAR_SHAPES[name], ("E", "POd"), (8_192, 32_768, 131_072))
+        == []
+    )
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
+def test_every_shape_uses_linear_memory_at_256_kb(name: str) -> None:
+    shape = LINEAR_SHAPES[name]
+    assert (
+        _memory_superlinear(shape, ("E", "P", "POd"), (16_384, 65_536, 262_144)) == []
+    )
+
+
+def _old_wrap_close_start(text: str, start: int, end: int) -> int:
+    match = re.compile(r"(?:\\?[\"')\]}>])+[.:!?]*\Z").search(text, start, end)
+    return match.start() if match is not None else -1
+
+
+def _old_unindented_break(text: str) -> bool:
+    return re.search(r"[\r\n][^ \t\xa0]*\Z", text) is not None
+
+
+def _without_marker_ranges(original: Callable[..., object]) -> Callable[..., object]:
+    """Rev 12's URL step: every synthesised piece stands for the whole edit."""
+
+    def pieces(raw: str, base: int) -> object:
+        result = original(raw, base)
+        if result is None:
+            return None
+        found, dropped = result  # type: ignore[misc]
+        return [p[:2] if p[0] == "new" else p for p in found], dropped
+
+    return pieces
+
+
+def _rev12_source(self: F._Tracked, a: int, b: int) -> str:
+    pieces: list[str] = []
+    last = None
+    for atom in self.rep[a:b]:
+        if atom < 0 or atom == last:
+            continue
+        last = atom
+        start, end = self.atom_range(atom)
+        pieces.append(self.raw[start:end])
+    F.work(b - a)
+    return "".join(pieces)
+
+
+def _rev12_in_resource_name(text: str) -> Callable[[int], bool]:
+    import pmcp.auth as A
+
+    ranges = [(m.start(), m.end()) for m in A._RESOURCE_NAME_RE.finditer(text)]
+
+    def inside(position: int) -> bool:
+        F.work(len(ranges))  # rev 12 asked every range
+        return any(a <= position < b for a, b in ranges)
+
+    return inside
+
+
+#: Each linearity finding's mutant, restoring the earlier mechanism. `work`
+#: mutants are caught by the work count; `clock` mutants (a regex's own
+#: backtracking, which the count cannot see) by the 64x wall-clock bound.
+def _rev13_source(self: F._Tracked, a: int, b: int) -> str:
+    """Rev 13's `source`: input once per CALL, so a many-character piece is
+    copied whole again by every match inside it."""
+    pieces: list[str] = []
+    covered_to = -1
+    copied = 0
+    for atom in self.rep[a:b]:
+        if atom < 0:
+            continue
+        start, end = self.atom_range(atom)
+        if end <= covered_to:
+            continue
+        pieces.append(self.raw[max(start, covered_to) : end])
+        copied += len(pieces[-1])
+        covered_to = end
+    F.work(b - a + copied)
+    return "".join(pieces)
+
+
+LINEARITY_MUTANTS: dict[str, tuple[str, str, Callable[[pytest.MonkeyPatch], None]]] = {
+    "B-1": (
+        "B-1 closers after a Bearer value",
+        "clock",
+        lambda mp: mp.setattr(F, "_wrap_close_start", _old_wrap_close_start),
+    ),
+    "B-1b": (
+        "B-1b line breaks before a Bearer value",
+        "clock",
+        lambda mp: (
+            mp.setattr(F, "unindented_break", _old_unindented_break),
+            mp.setattr("pmcp.auth.unindented_break", _old_unindented_break),
+        ),
+    ),
+    "B-1c": (
+        "B-1c one policy value over many markers",
+        "work",
+        lambda mp: mp.setattr(F, "_decision_key", id),
+    ),
+    "B-3": (
+        "B-3 secret-keyed query pairs",
+        "work",
+        lambda mp: (
+            mp.setattr(F, "_url_pieces", _without_marker_ranges(F._url_pieces)),
+            mp.setattr(F._Tracked, "source", _rev12_source),
+        ),
+    ),
+    "B-4": (
+        "B-4 keyword lists after resource names",
+        "work",
+        lambda mp: mp.setattr("pmcp.auth._in_resource_name", _rev12_in_resource_name),
+    ),
+    "B-5": (
+        "B-5 sk- tokens inside one re-encoded query value",
+        "work",
+        lambda mp: mp.setattr(F._Tracked, "source", _rev13_source),
+    ),
+}
+
+
+@pytest.mark.parametrize("finding", sorted(LINEARITY_MUTANTS))
+def test_each_linearity_finding_has_a_killing_mutant(
+    finding: str, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    """The earlier mechanism restored, the shape grows quadratically again
+    (small sizes, where the quadratic path is still quick)."""
+    shape_name, measure, mutate = LINEARITY_MUTANTS[finding]
+    shape = LINEAR_SHAPES[shape_name]
+    run = _entry_points()["P"]
+    if measure == "work":
+        assert _superlinear(shape, (2_048, 8_192)) == []
+        mutate(monkeypatch)
+        assert _superlinear(shape, (2_048, 8_192)) != []
+    else:
+        assert _time_ratio(run, shape) < 64
+        mutate(monkeypatch)
+        assert _time_ratio(run, shape) > 64
+
+
+#: B-2: a key AFTER a resource name in the same run is a key. Each delimiter
+#: that ends a resource name, between a URN/ARN and a keyed credential.
+_RESOURCE_NAMES = (
+    "urn:ietf:params:oauth:grant-type:token-exchange",
+    "urn:ietf:params:oauth:grant-type:jwt-bearer",
+    "arn:aws:rds:us-east-1:1:db:prod",
+    "urn:db",
+)
+_NAME_DELIMITERS = ("&", ",", ";", "(", ")", "?", "=", " ", "&x=1&")
+
+
+def _b2_cases() -> list[tuple[str, str]]:
+    cases = [
+        (
+            "grant_type=urn:ietf:params:oauth:grant-type:token-exchange"
+            "&client_secret=Hunter2abcX9",
+            "Hunter2abcX9",
+        ),
+        ("connect urn:db;user=admin;password=Hunter2abcX9", "Hunter2abcX9"),
+        ("(arn:aws:x)token=Hunter2abcX9", "Hunter2abcX9"),
+    ]
+    for name in _RESOURCE_NAMES:
+        for delimiter in _NAME_DELIMITERS:
+            for key in ("client_secret", "password", "token", "api_key"):
+                cases.append((f"x={name}{delimiter}{key}=Hunter2abcX9", "Hunter2abcX9"))
+    return cases
+
+
+def _b2_n10() -> None:
+    for text, secret in _b2_cases():
+        for surface in (_engine, _policy):
+            assert secret not in surface(text), (text, surface(text))
+        assert secret not in json.dumps(_process({"detail": text})), text
+
+
+def _old_n10(span: F.FloorSpan) -> bool:
+    """Rev 11's N10: the input before the VALUE, read back to the last
+    whitespace or quote, across `& , ; ( ) ? =`, ends inside a resource
+    name."""
+    before = (span.raw_key_before or "") + span.key + span.sep
+    return (
+        span.rule in F._KEYED_RULES
+        and span.part == "value"
+        and span.raw_key_before is not None
+        and re.search(r"(?i)\b[au]rn:[^\s\"'<>]*\Z", before) is not None
+    )
+
+
+def test_b2_n10_fires_only_on_a_segment_of_a_resource_name() -> None:
+    _b2_n10()
+    # the approved class still holds: a key that IS a segment of the name
+    for text in (
+        "arn:aws:secretsmanager:us-east-1:123456789012:secret:MySecret-a1b2c3",
+        "arn:aws:iam::1:secret:hunter22x",
+    ):
+        assert _engine(text) == text and _policy(text) == text, text
+
+
+def test_b2_mutant_rev_11_n10(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setitem(F.SUPPRESSIONS, "N10", _old_n10)
+    with pytest.raises(AssertionError):
+        _b2_n10()
+
+
+_N1_CASES = (
+    "DATABASE_PASSWORD_FOR_REPLICATION_USER_ACCOUNT=Hunter2abcX9",
+    "api_key_for_the_production_database_server: Hunter2abcX9",
+    "client_secret_for_the_staging_environment_x=Hunter2abcX9",
+)
+
+
+def _n1() -> None:
+    for text in _N1_CASES:
+        for surface in (_engine, _policy):
+            assert "Hunter2abcX9" not in surface(text), (text, surface(text))
+
+
+def _old_n3(span: F.FloorSpan) -> bool:
+    return span.rule in F._KEYED_RULES and F._for_every_reading(
+        span,
+        lambda r: len(r.glued) > 24 or sum(c.isalnum() for c in r.suffix) > 24,
+    )
+
+
+def test_n1_n3_counts_only_a_glued_run() -> None:
+    _n1()
+    glued = "password" + "x" * 25 + "=hunter"
+    assert _fired(glued, False).get("N3"), _fired(glued, False)
+
+
+def test_n1_mutant_rev_11_n3(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setitem(F.SUPPRESSIONS, "N3", _old_n3)
+    with pytest.raises(AssertionError):
+        _n1()
+
+
+_N5_CASES = (
+    "passwordForTheProductionDatabaseServer=Hunter2abcX9",
+    "dbPasswordForTheProductionReplicaServer=Hunter2abcX9",
+    "theProductionElasticsearchClusterPassword=Hunter2abcX9",
+    "secretAccessKeyForTheProductionAwsAccount=Hunter2abcX9",
+    "apikeyForTheProductionPaymentsGatewayX=Hunter2abcX9",
+    "PRODUCTIONDATABASEREPLICATIONpassword=Hunter2abcX9",
+    "tokenForTheProductionDeploymentPipelineX Hunter2abcX9",
+)
+
+
+def _n5() -> None:
+    for text in _N5_CASES:
+        for surface in (_engine, _policy):
+            assert "Hunter2abcX9" not in surface(text), (text, surface(text))
+
+
+def _rev12_n3(span: F.FloorSpan) -> bool:
+    return span.rule in F._KEYED_RULES and F._for_every_reading(
+        span,
+        lambda r: len(r.glued) > 24
+        or len(re.match(r"[A-Za-z0-9]*", r.suffix).group(0)) > 24,  # type: ignore[union-attr]
+    )
+
+
+def test_n5_a_case_change_joins_words_like_a_joiner() -> None:
+    """N-5 of rev 12's board: camelCase and SHOUTING multi-word keys are
+    keys, as `_`-joined ones are (N-1); a long single-case glued run is
+    still an identifier (N3)."""
+    _n5()
+    for glued in ("password" + "x" * 25 + "=hunter", "x" * 26 + "password=hunter"):
+        assert _fired(glued, False).get("N3"), (glued, _fired(glued, False))
+
+
+def test_n5_mutant_rev_12_n3(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setitem(F.SUPPRESSIONS, "N3", _rev12_n3)
+    with pytest.raises(AssertionError):
+        _n5()
+
+
+_N2_CASES = (
+    ("Bearer " + "1" * 24, "1" * 24),
+    ("Authorization: " + "7" * 24, "7" * 24),
+    ("Bearer 0x" + "ab" * 32, "ab" * 32),
+    ("password " + "9" * 24, "9" * 24),
+    ("secret 0x" + "cd" * 32, "cd" * 32),
+    ("secret_hex=0x" + "ef" * 32, "ef" * 32),
+    ("auth_code=" + "3" * 24, "3" * 24),
+)
+
+
+def _n2() -> None:
+    for text, secret in _N2_CASES:
+        for surface in (_engine, _policy):
+            assert secret not in surface(text), (text, surface(text))
+
+
+def test_n2_a_plain_number_is_short_and_decimal() -> None:
+    _n2()
+    for prose in ("code=401", "exit code 137", '{"code": -32601}', "Bearer 2"):
+        assert _engine(prose) == prose and _policy(prose) == prose, prose
+    assert F.is_plain("1234567890") and not F.is_plain("12345678901")
+    assert not F.is_plain("0x1F")
+
+
+def test_n2_mutant_rev_11_plain_number(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setattr(
+        F, "_PLAIN_NUMBER_RE", re.compile(r"[+-]?[0-9]+|0[xX][0-9a-fA-F]+")
+    )
+    with pytest.raises(AssertionError):
+        _n2()
+
+
+_N4_URLS = (
+    "https://h/?a=1&",
+    "https://h/?a=1&&b=2",
+    "https://h/?&a=1",
+    "https://h/?&&&",
+    "https://h/?a=1&&",
+    "https://u:p@h/p;q?token=x&&next=y&#frag",
+)
+
+
+def _n4() -> None:
+    for url in _N4_URLS:
+        result = F._url_pieces(url, 0)
+        assert result is not None, url
+        pieces, dropped = result
+        covered = bytearray(len(url))
+        for piece in pieces:
+            if piece[0] == "keep":
+                covered[piece[1] : piece[2]] = b"\x01" * (piece[2] - piece[1])
+            elif piece[0] == "atom":
+                covered[piece[2] : piece[3]] = b"\x01" * (piece[3] - piece[2])
+        for start, stop, _ in dropped:
+            covered[start:stop] = b"\x01" * (stop - start)
+        assert all(covered), (url, [i for i, c in enumerate(covered) if not c])
+
+
+def test_n4_every_url_position_is_kept_or_labelled() -> None:
+    _n4()
+
+
+def test_n4_mutant_no_complement(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setattr(F, "_label_uncovered", lambda *args: None)
+    with pytest.raises(AssertionError):
+        _n4()
+
+
+_SWEEP_CHUNKS = 32
+
+
+def _all_shapes() -> dict[str, Callable[[int], str]]:
+    from tests import _redaction_shapes as S
+
+    return {**S.shapes(S.redactor_patterns()), **S.compositions(), **S.atom_repeats()}
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("chunk", range(_SWEEP_CHUNKS))
+def test_generated_shapes_do_linear_work(chunk: int) -> None:
+    """The full generated sweep (`tests/_redaction_shapes.py`): every shape
+    derived from the redactor's regular expressions and every composition
+    derived from its loops, in 32 chunks. Screened on the work count at 4 KB
+    -> 16 KB on every entry point in worker processes; any shape over the
+    bound is re-measured at 16 KB -> 64 KB -> 256 KB here and must be
+    linear. Deterministic."""
+    shapes = _all_shapes()
+    assert len(shapes) > 70_000
+    names = sorted(shapes)[chunk::_SWEEP_CHUNKS]
+    ctx = multiprocessing.get_context("spawn")
+    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
+        flagged = [
+            name
+            for found in pool.imap_unordered(_screen_work, names, chunksize=25)
+            for name in found
+        ]
+    confirmed = {
+        name: _superlinear(shapes[name], (16_384, 65_536, 262_144)) for name in flagged
+    }
+    assert {k: v for k, v in confirmed.items() if v} == {}, confirmed
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("chunk", range(8))
+def test_generated_regex_shapes_do_not_backtrack(chunk: int) -> None:
+    """What the work count cannot see: a regex's own backtracking. Every
+    regex-derived shape is screened on the clock at 2 KB -> 8 KB (flagged
+    over 10x; linear is 4x, quadratic 16x); a flagged shape is re-measured
+    at 1 KB -> 16 KB, best of three, and must stay under 64x (linear 16x,
+    quadratic 256x) on every entry point."""
+    from tests import _redaction_shapes as S
+
+    shapes = S.shapes(S.redactor_patterns())
+    names = sorted(shapes)[chunk::8]
+    ctx = multiprocessing.get_context("spawn")
+    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
+        flagged = [
+            name
+            for found in pool.imap_unordered(_screen_clock, names, chunksize=25)
+            for name in found
+        ]
+    slow = {
+        name: [
+            label
+            for label, run in _entry_points().items()
+            if _time_ratio(run, shapes[name]) > 64
+        ]
+        for name in flagged
+    }
+    assert {k: v for k, v in slow.items() if v} == {}, slow
+
+
+_SCREEN_SHAPES: dict[str, Callable[[int], str]] = {}
+
+
+def _screen_work(name: str) -> list[str]:
+    if not _SCREEN_SHAPES:
+        _SCREEN_SHAPES.update(_all_shapes())
+    shape = _SCREEN_SHAPES[name]
+    bound = 4 * (14 / 12) * 1.05  # 4 KB -> 16 KB, with the sort's log
+    for run in _entry_points().values():
+        if _work(run, shape(16_384)) > bound * max(_work(run, shape(4_096)), 1):
+            return [name]
+    return []
+
+
+def _screen_clock(name: str) -> list[str]:
+    if not _SCREEN_SHAPES:
+        _SCREEN_SHAPES.update(_all_shapes())
+    shape = _SCREEN_SHAPES[name]
+    for run in _entry_points().values():
+        small = min(_clock(run, shape(2_048)) for _ in range(2))
+        large = min(_clock(run, shape(8_192)) for _ in range(2))
+        if large > 0.02 and large / max(small, 1e-4) > 10:
+            return [name]
+    return []
+
+
+def _clock(run: Callable[[str], object], text: str) -> float:
+    started = time.perf_counter()
+    run(text)
+    return time.perf_counter() - started
```
<!-- PATCH-END -->
