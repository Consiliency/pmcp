# Detailed plan: shape-based secret redaction — separator-anchored keywords, opaque-run scoring, and a prose corpus (Consiliency/pmcp#234)

> **Revision 10 (2026-09-26) — never worse than `main`, by `main`'s
> grammar.** **The method changed, and this is why.** Three board rounds in
> a row (rev 7 → 8: grok's CRLF/no-break-space rows; rev 8 → 9: B1-B5,
> N1-N4, G1, C2-C7; rev 9 → 10: the claude seat's F1-F10, DISAGREE) ended
> the same way: rev N passed its own never-worse differential with 0
> unaccepted rows, then a fresh seat found inputs worse than `main` on an
> axis the generator had never produced, because every axis had been added
> *after* a finding. With the maintainer's approval the differential is no
> longer built from findings. It is **derived from the oracle's grammar**:
> `main`'s keyword, `Authorization`, `Bearer` and URL rules and its policy
> defaults, plus everything `json.dumps` emits (escapes, scalars) on the
> structured path. A read-only audit set every construct of that grammar
> against rev 9 (table A, one row per construct of `main`'s regexes and
> policy defaults; table B, the serialiser's escapes and literals); each
> row now names rev 10's equivalent and says **⊇** or names the stated
> class that accepts the difference. **Those two tables, in
> "Rev 10: never worse than main, by main's grammar" below, are what the
> board reviews.** The generator (`tests/_redaction_grammar.py`) samples
> each construct: every 1- and 2-character separator over `\s` + `:` +
> `=`, every character class before a key, every escape and scalar the
> serialiser emits, `main`'s URL grammar, and one-axis and all-axes rows.
> Its tier 1 (12 535 rows × 12 surfaces) runs in the default suite; tier 2
> (166 183 rows) is marked `slow` and is not run by CI. Frozen code:
> `origin/wip/234-redactor-rev10-code` @ `e79d75f`
> (`60ca193..e79d75f`: the audit's fixes, the grammar-derived
> differential, and the maintainer's decisions), embedded below verbatim
> with the new test module, the committed oracle regenerator and the
> `pyproject.toml` marker. **Fixed** (each red on rev 9, green here,
> pinned by a mutant): F1 colon-qualified keys, F2 `Bearer` after
> `key:`/`key=`, F3 a JSON escape before a key, F4 any line-break run and a
> break before the operator, F5 `NaN`/`±Infinity` (decision 1), F8 a
> backslash before a key, F9 the quadratic PEM rule, and the audit's new
> gaps N1 operator runs, N2 joiner runs in a suffix, N4b acronym-glued
> keys, N5 an unterminated opening quote, N6/N7 wrapped `Bearer` and
> `Authorization` values, N8 `code` under a non-status qualifier, N9
> single-dash flags; also a URL that ended on the backslash of an escaped
> quote. **The maintainer's decisions**, each a stated class or a rule: (1)
> a bare JSON scalar after a quoted key is left unchanged, as on `main`
> (this reverses rev 9's number → `"[REDACTED]"`; numeric secrets under a
> quoted key are Consiliency/pmcp#290's); (2) a glued prefix or suffix
> longer than 24 characters is an identifier (class N3); (3) a quoted
> `Authorization` value in a JSON text leaf is Consiliency/pmcp#290's
> (class N6b); (4) random-case glued keys before whitespace are prose
> (class N4); (5) `key_code` is redacted (a weak key); (6) diagnostic
> `code` qualifiers are class C12, listed explicitly (37), with `promo`,
> `coupon` and `discount` removed because they name redeemable values;
> (7) the slow tier is not run in CI (how to run it is under
> Verification). **Measured** (all re-run for this plan on `e79d75f`):
> tier 1 12 535 rows, **0 unaccepted**; tier 2 166 183 rows, **0
> unaccepted**, and 0 on a second seed pair; `main`'s URL rows: `main`
> removes a secret in 8 897 of 10 000, rev 10 is worse on none and better
> on 1 687; 21 of 21 mutants killed; red/green 157 of 472 default-tier
> nodes red on rev 9; slow tier 15 passed in 195 s; full suite 4 536
> passed, 3 skipped, 40 deselected (details under Unverified). **Rev 9's accepted-class counts (8 000 rows, ten classes)
> are superseded:** its hand-listed string differential is removed and the
> classifier replaced. **The plan holds no literal control or separator
> character** (a literal U+2028 in rev 9's embedded test file made every
> external seat refuse the bundle): the final file was scanned for Unicode
> categories Cc, Cf, Zl and Zp (tab and newline aside) and U+0085, and a
> test in the embedded file guards the sources.
>
> **Revision 9 (2026-09-26) — the differential was only as wide as its
> generator.** The board round on rev 8 (`fd7adb8`) came back **DISAGREE
> from all four seats** (claude, grok, codex, gemini; the claude seat was
> filled natively, `review.md`). Every blocking finding lay *outside* the
> corpus the never-worse test generated — in the claude seat's words, "0
> unaccepted measures the corpus, not the redactor". Each was reproduced on
> `main` vs rev 8 before it was fixed, and rev 9 fixes the class, then widens
> the generator along the axis that hid it. The code is frozen at
> `origin/wip/234-redactor-rev9-code` @ `8e7d98c` (one commit per finding
> class, `56d7f80..8e7d98c`) and embedded below verbatim, with the oracle
> regenerator. Classes, the rule change, and the measured proof (three-tree
> probes in "Rev 9 board findings" below; red/green and mutants in
> "Mutation evidence"):
> - **B1** (claude; codex re-found it) — a JSON literal after a *quoted* key
>   was redacted unquoted (`{"token": 42}` → `{"token": [REDACTED]}`, invalid
>   JSON, dict → str). Now `null`/`true`/`false` are kept and a number
>   becomes the string `"[REDACTED]"`; `process_output({"token": 42})` is a
>   dict again.
> - **B2** — `Authorization` ate a list's `[` or an object's `{`. Its bare
>   value never starts on `[`/`{`, and a JSON literal after a quoted
>   `Authorization` key gets B1's rule.
> - **B3** (codex, gemini re-found it) — `.` blocked a key (`db.password=`,
>   `self.password =` leaked on both surfaces). `.` may now precede a key;
>   `:` still may not (ARNs).
> - **B4** — PascalCase keys (`AccessToken=`, `ClientSecret=`) leaked. The
>   camelCase qualifier may start with a capital.
> - **B5** (codex re-found it) — rev 8 restored only `\xa0` of `main`'s
>   Unicode `\s`. Horizontal whitespace is now `[^\S\r\n]` in every engine
>   rule **and** every policy default (U+3000, U+2003, `\v`, `\f` …).
> - **N1** — suffixed keys (`password2`, `passwordHash`, `secret_value`) are
>   secrets when the value is credential-shaped and not a URL/ARN; declared
>   compound keys (`secret_access_key`) are exempt; the whitespace rule gets
>   the `_id`/`_key`, camelCase and suffixed key shapes (`SECRET_KEY
>   abc123def456`, `--clientSecret …`).
> - **N2** — the list pass redacted the keys and values of a list of
>   objects. It now matches only a literal array (below).
> - **N3** — after an *unquoted* key, a quote whose content starts on
>   `,:]}` closes the enclosing string and is not a value (`{"msg": "missing
>   token: ", "code": 401}` stays valid).
> - **N4** (codex re-found it) — `"a_" * n` was quadratic (3.3 s at 8 KB).
>   The key qualifier is bounded to 8 segments: 0.029 s here, `main` 0.001 s.
> - **G1** (grok) — a spaced `==` (`password == hunter2`, `password==
>   hunter2`, `if token == hunter2:`) leaked where `main` redacted it. `==`
>   followed by whitespace is a separator on the engine when the value is
>   credential-shaped; `if token == expected:` stays a comparison.
> - **C2** (codex) — a percent-encoded query *key* (`?api%2Dkey=hunter2`) is
>   decoded and the `key=value` pair asked of the engine.
> - **C4** — `Bearer\r\nhunter2` continues onto an unindented line when the
>   value is credential-shaped and not a flag or bullet; `token …` is B5.
> - **C6** — the whitespace-rule and `Authorization` bare values never end on
>   a backslash (a serialised leaf's `\"` stays escaped; the dict stays a dict).
> - **C7** — the list body is quote-aware (`["first]", "hunter2"]`, which
>   leaked on `main` too) and honours the `code` qualifier exemption
>   (`error_codes: [...]` keep their values).
> - **gemini's nit** — the classifier's `&` arm read the output, not the
>   row; it could not fire and is removed.
>
> The widened differential then found six more classes, all present in rev
> 8 and each fixed and pinned by a mutant:
> - **list straddle** — after an unquoted key inside a string leaf (`{"a":
>   "token: [x", "b": "]"}`) the list body ran past the string's closing
>   quote; the body is now a literal array (quoted strings or JSON scalars,
>   comma-separated, nothing bare).
> - **A, JSON `\uXXXX` escape** — `json.dumps` spells non-ASCII whitespace
>   `　`; the shape pass began a span at the `u` and left a lone
>   backslash (invalid JSON, dict → str). `widen_over_escapes` starts no
>   span inside a backslash escape, on both surfaces (commit `977fd49`'s
  sweep: 0 of 117 break; `test_no_span_starts_inside_a_json_escape`, 24
  cases, red on rev 8, green here).
> - **B, whitespace suffix** — the whitespace rule takes the suffixed-key
>   shape (`password2 abc123def456`) and excludes URL/ARN values.
> - **C, case-folded keys** — `CLIENTSECRET=`, `dbpassword=`,
>   `mypassword=` leaked (`main` redacts them); a key may carry a glued
>   prefix (≤ 24 chars) when the value is credential-shaped and not a
>   URL/ARN, and on the whitespace rule only a single-case word counts
>   (`Ed25519PrivateKey X509Cert` is prose).
> - **dash-led value** — the flag/bullet guard skips only a `--` flag or a
>   lone `-*#>` marker followed by whitespace; `token -abc123def` and
>   `password:\n  -hunter22` are redacted, as on `main`.
> - **other-quote straddle** — a quoted value after an unquoted key that
>   contains the *other* quote character (`password='x"], "k": "y'`) runs
>   across a JSON string boundary and is skipped.
>
> Also fixed by B5's policy change: rev 8's stated residual (the policy
> defaults ate a plain-text value's opening quote) is gone — `password="x"`
> → `password="[REDACTED]"` on both surfaces. **Measured:** differential
> 8 000 string rows, **0 unaccepted**, 1 792 worse (row, surface) pairs all in
> 10 accepted classes, 1 902 rows better; 800 dict rows, 0 dict → str; JSON
> fuzz 6 000 documents, 0 broken in scope; 306 tests; 144 of them red on rev 8;
> full suite 4 370 passed.
>
> **Revision 8 (2026-09-26) — the core text redactor only.** The maintainer
> split the issue on 2026-09-23: Consiliency/pmcp#234 is the *text* redactor,
> and redacting a structured result before `json.dumps` moved to
> Consiliency/pmcp#290 (whose body lists six measured defects as acceptance
> criteria). Rev 7 was boarded by four seats (claude, grok, codex DISAGREE;
> gemini AGREE); the core held, and rev 8 answers each finding. The code is
> frozen at `origin/wip/234-redactor-rev8-code` @ `56d7f80` and embedded
> below verbatim (`56d7f80` = `d316364` plus comment/docstring wording and
> the removal of a no-op `not inner` check; behaviour identical). **(1) Structured layer removed** — `_redact_leaves` is gone
> and the serialisation decision is now **(c), scoped out to
> Consiliency/pmcp#290**: a dict result is serialised, then redacted as text,
> exactly as on `main`. `{"password": "hunter2"}` is fixed in text **and** in
> a dict's serialised JSON, and a dict now round-trips as a dict because a
> keyed value in quotes is redacted *inside* its quotes (`{"password":
> "[REDACTED]"}`); JSON text inside a string leaf (escaped quotes) is still
> Consiliency/pmcp#290's. The differential now also runs over 400 dict
> results through `process_output(dict, redact=True)` and asserts the result
> type is never worse than `main`'s (oracle key `dict_types`: `main` returns
> 370 dicts and 30 strings; rev 8 returns 14 strings, a strict subset of
> `main`'s 30). **(2) CRLF / no-break-space regressions (grok) fixed** —
> every engine rule takes `\r?\n` where a continuation is allowed and `\xa0`
> as horizontal whitespace (the policy defaults keep `[ \t]*`; the engine's
> spans cover that surface, measured on both); `_DIFF_SEPS` gains `":\r\n  "`, `"\r\n  "`, `":\xa0"`
> and `":\r\n"`, and the oracle was regenerated from `main`. **(3) Recursion
> bounded** — `_MAX_DECODE_DEPTH = 3`; past it a decoded query value is
> treated as covered and redacted (fails closed;
> `test_url_decode_depth_bound_fails_closed`, mutant D1). **(4) Policy
> patterns run on decoded query values** via `covers=`
> (`test_operator_pattern_applies_to_a_percent_encoded_query_value`, mutant
> D2). **(5) `if token == expected:` is a comparison on both surfaces** — the
> policy defaults take the engine's separator grammar
> (`test_a_comparison_survives_on_both_surfaces`). **(6) New:
> quote-preserving keyed replacement.** Before it, the pre-fix rev-8 spike
> turned 120 of 400 dict results into strings and broke 16 of 258 JSON rows
> per surface (spike measurements, `main`: 7); after it, 14 of 400 and 0 of
> 258 (`test_redaction_never_breaks_a_json_document`,
> `test_structured_result_round_trips_as_a_dict`; mutant Q1). **(7) New:
> keyed-list pass** — `_keyword_list_spans` redacts each quoted element of a
> flat list under a secret key, so `"password": [\n  "hunter2"\n]` is fixed
> (rev 7 ate the `[`; `main` left the value) rather than stated as a
> residual (`test_pretty_printed_list_value_is_redacted_inside_its_quotes`;
> mutants Q2, Q3). **(8) New stated residual**, pre-existing: the policy
> default patterns eat the *opening* quote of a quoted value in plain text
> (`password="x"` → rev 8 `password=[REDACTED]"`, `main` `password=
> [REDACTED]"`); JSON is not affected. Stale text fixed (`path_transform`,
> `%5BREDACTED%5D`, "does not redact before truncate", `_TRAILING_*`), the
> `|`/`->` accepted class relabelled as `main`'s re-encoding collateral.
> Differential: **0 unaccepted; 294 row-surfaces in 6 accepted classes;
> rev 8 removes more than `main` on 1 737 rows** (a different corpus from
> rev 7's: the four new separators reshuffle the draws, so 226 → 294 is not
> a regression count). 149 tests; mutation rows Q1-Q3, D1, D2 (Q4
> equivalent, its guard deleted); M37 retired.
>
> **Revision 7 (2026-09-23) — the differential against `main` is THE
> never-worse test.** Round 5 (quorum: codex, gemini, native claude) found
> that rev 6's two regressions came from narrowing rules for non-blocking
> false positives and then adjusting the generator to match, while the
> hand-picked 42-row never-worse corpus reported 0 failures. Rev 7 adopts the
> board seat's differential (its generator, byte-for-byte — keys × values ×
> separators × wraps, 4 000 inputs, seed 20260923 — and `main` @ `860636a`'s
> recorded removals as a committed oracle fixture, `tests/fixtures/
> redaction_main_oracle.b64`, gzip+base64 of the per-row removed pieces).
> The test fails on any row where rev 7 keeps a ≥ 4-char piece `main` removed
> on the same surface unless the row falls into an **accepted-regression
> class decided from the row's own key, separator and value**; every class is
> tabled below with its count. Result: **0 unaccepted; 226 row-surfaces in 5
> accepted classes; rev 7 removes more than `main` on 1 852 rows.** Rule from
> here: no lookahead, lookbehind or rule narrowing lands without re-running
> it. The four blocking regressions are fixed (quoted `Bearer`; httpie `:=`
> and `==`; percent-encoded query values; Ruby `=>`), the serialisation layer
> is **in scope — decision (a)**: a structured result is redacted *as
> structure* before it is serialised (each string leaf as the server wrote
> it, then as the one-pair document `{key: leaf}` so the dict key gives
> context, dict keys as bare text; leaves past the window dropped; the
> serialised form is only capped, never re-scanned), and M28 is rebuilt as a
> two-rule mutant that is red for its stated reason. Non-blocking items are
> each addressed or tabled. 136 tests; mutation rows M34-M38.
>
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
> from `main` and stated). The CLI seats' supplement (grok DISAGREE, codex
> DISAGREE, gemini AGREE — the same three classes, plus userinfo/`?q=`,
> operator patterns in a path, and a window shrink from ordinary `token=`
> values) is covered by the same fixes; each case is a named test. The merge
> rule is stated as a property: a containing span may drop an inner span
> only if its replacement redacts that region. 131 tests.
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
> **How to apply.** **Patch:** lines 2046-3306 of this file (the content between the ```diff fences; `sed -n '2046,3306p' <plan> > 234.patch && git apply --check 234.patch && git apply 234.patch` on `main`; it is `git diff 860636a origin/wip/234-redactor-rev10-code -- src/` verbatim). **pyproject.toml:** lines 7863-7883 (`sed -n '7863,7883p' <plan> > pyproject.patch && git apply pyproject.patch`). **Test file:** lines 3319-6569 (`sed -n '3319,6569p' <plan> > tests/test_redaction.py`). **Grammar module:** lines 6716-7853 (`sed -n '6716,7853p' <plan> > tests/_redaction_grammar.py`). **Regenerator:** lines 6580-6705 (`sed -n '6580,6705p' <plan> > tests/fixtures/regen_redaction_main_oracle.py`). **Fixture:** copy
> `.consiliency/plans/detailed-234-redactor-main-oracle.b64` (committed beside
> this plan) to `tests/fixtures/redaction_main_oracle.b64`; the differential
> test reads it from there. **Regenerating it (rev 10):** the committed
> regenerator is `tests/fixtures/regen_redaction_main_oracle.py` (embedded
> under `## Oracle regenerator`; it reads `tests/_redaction_grammar.py` and
> `tests/test_redaction.py` from its own checkout). With the rev-10 test
> files in place, run it against a **main** tree:
> `PYTHONPATH=<main>/src python tests/fixtures/regen_redaction_main_oracle.py
> <main>/src <out.b64>` (it asserts that `pmcp` was imported from `<main>/src`).
> It records, over `corpus(2)` (166 183 rows; tier 1 is its prefix), `main`'s
> observation code per row (`grammar`), the fingerprint of each tier
> (`grammar_fingerprint`), and for the kept structured corpora `dict`,
> `dict_types` and `fuzz_types`; `json.dumps(sort_keys=True,
> separators=(",", ":"))`, gzip with `mtime=0`, `base64.encodebytes`
> (76-column lines). Copy the output to both
> `tests/fixtures/redaction_main_oracle.b64` and
> `.consiliency/plans/detailed-234-redactor-main-oracle.b64`. Measured: run
> against `main` @ `1fb36f2` (redactor sources unchanged since `860636a`) in
> the embedding proof, the output is `cmp`-identical to the fixture.
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

### Rev 9 board findings — before/after, measured (both surfaces)

The coordinator's probes (`scratchpad/234/repro_b.py`, `repro_c.py`) plus a
round-3 probe, each run on three trees: `main` @ `9ca081e`, rev 8
(`56d7f80`'s `src/` on `PYTHONPATH`), rev 9 (`8e7d98c`); `pmcp.__file__`
printed each time. "leaks" means the secret is still in the output of
`sanitize_auth_diagnostic` **and** of `redact_secrets`, unless one is named.

| class | input | main | rev 8 | rev 9 |
|---|---|---|---|---|
| B1 | `process_output({"access_token": None, "error": "expired"})`, `{"token": 42}`, `{"sid": 12345678}` | dict ×3 (unredacted) | **str ×3** (`"token": [REDACTED]`, invalid JSON) | dict ×3: `null` kept, `{"token": "[REDACTED]"}`, `{"sid": "[REDACTED]"}` |
| B2 | `process_output({"Authorization": {"scheme": "Bearer", "value": "abc123def456"}})`; `'{"Authorization": ["9"]}'` | dict; unchanged | **str** (`[REDACTED]` ate the `{`); `{"Authorization": [REDACTED]"9"]}` | dict; unchanged |
| B3 | `spring.datasource.password=hunter22`, `self.password = hunter22` | redacted | **leaks** | redacted |
| B4 | `AccessToken=abc123def456`, `ClientSecret=abc123def456` | redacted | **leaks** | redacted |
| B5 | `password:　hunter22`, `bearer　abc123def456` | redacted | **leaks** | redacted |
| G1 | `password == hunter2`, `password== hunter2`, `if token == hunter2:` | redacted | **leaks** | redacted |
| G1 | `if token == expected:` | `if token == [REDACTED]:` | unchanged | unchanged |
| C2 | `https://h.example/?api%2Dkey=hunter2` | redacted | **leaks** | redacted |
| C4 | `Bearer\r\nhunter2`, `token abc123def456` | redacted | **leaks** | redacted |
| C6 | `process_output({"text": 'token hunter2value" hi'})`, `{"text": 'Authorization: hunter2value" hi'}` | dict ×2 | **str ×2** (the `\` before `"` eaten) | dict ×2, `\"` kept |
| C7 | `{"password":["first]","hunter2"]}` | **leaks** | **leaks** | redacted (better than `main`) |
| C7 | `process_output({"session": [{"id": "alice", "status": "expired"}]})`, `{"error_codes": ["AADSTS50011"]}` | unchanged | keys and values `[REDACTED]` | unchanged |
| N2 | `process_output({"secrets": [{"id": "db", "name": "prod"}]})` | unchanged | `[{"[REDACTED]": "[REDACTED]"}]` | unchanged |
| N3 | `{"msg": "missing token: ", "code": 401}` (text and dict) | valid; dict | `"missing token: "[REDACTED]"code": 401` (invalid); **str** | valid; dict |
| N4 | `sanitize_auth_diagnostic("a_" * 4000)` | 0.001 s | **3.264 s** | 0.029 s |
| list straddle | `process_output({"a": "token: [x", "b": "]"})` | dict, unchanged | dict `{"a": "token: [REDACTED]", "[REDACTED]": "]"}` (a key redacted) | dict `{"a": "token: [REDACTED]", "b": "]"}` |
| A | `process_output({"password": "x", "t": "token　abc123def456"})` | dict, `abc123def456` kept | **str** (`token\[REDACTED]`, lone backslash) | dict `{"t": "token[REDACTED]"}` (the escape is inside the span: residual below) |
| B | `password2 abc123def456`, `passwordHash hunter2` | redacted | **leaks** | redacted |
| C | `CLIENTSECRET=abc123def456`, `dbpassword=hunter22`, `mypassword=hunter22` | redacted | **leaks** | redacted |
| C | `Ed25519PrivateKey X509Cert` | unchanged | unchanged | unchanged |
| dash | `token -abc123def`, `password:\n  -hunter22` | redacted | **leaks** | redacted |
| dash | `secret-bearer failed` | `secret-bearer [REDACTED]` | unchanged | unchanged |
| other quote | `password='x"], "k": "y'` (text); `process_output({"a": "password='x\"", "k": "y'"})` | E unchanged, P `password= [REDACTED]"…`; **str** | `password='[REDACTED]'` (ate `"], "k": "y`); **str** | unchanged; dict |
| residual | `password="x"` on the policy surface | `password= [REDACTED]"` | `password=[REDACTED]"` | `password="[REDACTED]"` |
| — | JSON rows of the rev-9 string corpus (810) broken, engine/policy | 8/8 | 34/34 | **0/0** |
| — | JSON fuzz, 6 000 documents: broken among the 3 272 with no `\"` (engine/policy); among all | 358/398; 1 026/1 311 | 800/868; 1 479/1 774 | **0/0**; 4/4 (all carry `\"`: Consiliency/pmcp#290) |

### Rev 8 board findings — before/after, measured (both surfaces)

Probed with one script on three trees: `main` @ `9ca081e` (its `auth.py`/`policy.py`
equal `860636a`'s), rev 7 (this plan's rev-7 patch applied to `main`), and
rev 8 (`origin/wip/234-redactor-rev8-code` @ `d316364`, behaviourally
identical to `56d7f80`); `pmcp.__file__` was
printed each time to prove which tree answered. E = engine, P = policy.

| # | input | main | rev 7 | rev 8 |
|---|---|---|---|---|
| 2 | `password:\r\nhunter2`, `token\r\nabc123def456`, `Authorization:\r\n  s3cr3tvalue`, `password:\xa0hunter2` | redacted (E and P) | **all four unchanged** on both surfaces | value `[REDACTED]`, separator kept, both surfaces |
| 3 | `"https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"` | 11 597 chars back, unredacted | **`RecursionError`** on both surfaces | `https://h.example/?q=[REDACTED]` (fails closed at depth 3) |
| 4 | `https://h.example/?q=%67%68%70%5Fabcdefghijklmnop` (the C-13 probe, encoded) | E decodes it to `?q=ghp_abcdefghijklmnop`; P `?q=[REDACTED]` | unchanged on both | E unchanged (the C-13 invariant); P `?q=[REDACTED]` |
| 5 | `if token == expected:` | `if token == [REDACTED]:` (E and P) | E unchanged; P **`if [REDACTED] expected:`** | unchanged on both |
| list | `{\n  "password": [\n    "hunter2"\n  ]\n}` as a string | unchanged (leaks) | `"password": [REDACTED]\n    "hunter2"\n  ]` (ate the `[`, leaks) | `"password": [\n    "[REDACTED]"\n  ]` — valid JSON |
| 1 | `process_output({"password": "hunter2"}, redact=True)` | dict, `hunter2` kept | dict, `[REDACTED]` (structured layer) | dict `{"password": "[REDACTED]"}` (text layer; quotes kept) |
| 1 | `process_output({"password": ["hunter2"]}, redact=True)` | dict, kept | dict, kept | dict `{"password": ["[REDACTED]"]}` |
| 1 | `process_output({"content": [{"type": "text", "text": json.dumps({"password": "hunter2", "api_key": "abc123def456", "Authorization": "Bearer hunter2tok"})}]}, redact=True)` | **str** (the Bearer rule ate `\"}`: broken JSON); `hunter2`, `abc123def456` kept | dict, all three `[REDACTED]` | dict; `Bearer [REDACTED]`; **`hunter2` and `abc123def456` kept** — JSON inside a leaf is Consiliency/pmcp#290 (the split accepts this regression against rev 7; it is not a regression against `main`) |
| res | `password="x"` (plain text, quoted) | E unchanged; P `password= [REDACTED]"` | `password=[REDACTED]` | E `password="[REDACTED]"`; P `password=[REDACTED]"` — the stray `"` is the policy default's, as on `main` (residual below) |
| — | string differential, 4 000 rows (rev 8 corpus) | oracle | — | **0 unaccepted**, 294 accepted row-surfaces in 6 classes, 1 737 rows better |
| — | dict differential, 400 results | oracle; removes something on 111; 30 come back as str | — | **0 unaccepted**, 2 accepted; 14 str, all among `main`'s 30 |
| — | JSON validity, the 258 corpus rows that parse as JSON | 7 broken on each surface | — | 0 broken on either surface |

### Rev 7 board findings — before/after, measured (both surfaces)

| # | input | main | rev 6 | rev 7 |
|---|---|---|---|---|
| 1 | `{"text": "Bearer test-token"}`, `'Bearer abc123def456'`, `"Bearer abc123def456"` | redacted | **unchanged** | `Bearer [REDACTED]` inside the quotes |
| 2 | `password:=hunter2`, `token:=abc123def456`, `password==hunter2` | redacted | **engine unchanged** | `password:=[REDACTED]`, `token:=[REDACTED]`, `password==[REDACTED]` (`if token == expected:` still unchanged) |
| 3 | `https://h.example/?q=%67%68%70%5F16C7e…` | policy redacts | `?q=%67%68%70%[REDACTED]` (encoded prefix kept) | `?q=[REDACTED]` |
| 4 | `{"password"=>"hunter2"}` | unchanged | `{"password"=[REDACTED]"hunter2"}` (worse both ways) | `{"password"=>[REDACTED]}` |
| S | `process_output({"content": [{"type": "text", "text": json.dumps({"password": "hunter2", "api_key": "abc123def456", "Authorization": "Bearer hunter2tok"})}], "isError": true}, redact=True)` | leaks `hunter2`, `abc123def456` | leaks all three | `{"password": [REDACTED], "api_key": [REDACTED], "Authorization": "[REDACTED]"}` inside the leaf |
| nb | `"password":\n    "hunter2"`, `password:\n  hunter2`, `[x-api-key\n  abc123def456]` | redacted | unchanged | value `[REDACTED]`; `token:\nthe bearer of` and `token:\n  - a bullet` unchanged |
| — | differential, 4 000 rows | oracle | 508 worse row-surfaces (seat), 553 by class here | **0 unaccepted**, 226 accepted in 5 classes, 1 852 rows better |

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
| 1 (grok/codex) | `https://alice:hunter2@example.test/cb?q=ghp_16C7e…` | — | `https://example.test/cb?q=ghp_16C7e…` (userinfo stripped, token kept) | `https://example.test/cb?q=[REDACTED]` |
| 1 (codex) | operator pattern `abcdef` on `https://example.test/abcdef` | — | `abcdef` kept (built-in rewrite suppressed the operator span) | `https://example.test/[REDACTED]` |
| 1 (grok) | `https://example.test/dl?file=sk-live-abc123def456` | — | unredacted | `?file=[REDACTED]` |
| 2 (codex) | `("token=" + "a"*8300 + "\n")*2 + '{"password": "hunter2 tail tail…"}'` at `max_bytes=300` | — | `hunter2` emitted (window shrank to ~100 chars, returned whole) | `token=[REDACTED]` + marker, 70 bytes |

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

## Rev 10: never worse than main, by main's grammar

This section is what the board reviews. It states the never-worse claim as a
correspondence between `main`'s grammar and rev 10's, row by row (tables A
and B), then gives the generator that samples every row, the accepted
classes, the red/green run, the mutants and a cross-reference from every
rev-9 finding and every audit gap to its fix or class.

**Trees.** `main` is `~/code/pmcp` @ `1fb36f2` (one docs-only commit past
`9ca081e`; `src/pmcp/auth.py` and `src/pmcp/policy/policy.py` are unchanged
from `860636a`, checked with `git diff --stat`). Rev 10 is a detached
scratch worktree of `origin/wip/234-redactor-rev10-code` @ `e79d75f`. Rev 9
is `60ca193`'s `src/` (identical to `8e7d98c`'s; `60ca193` changed tests
only) on `PYTHONPATH`. Every run printed `pmcp.__file__`. Line numbers
below are `e79d75f`'s.

**Surfaces.** Every generated text row is observed on 12 surfaces: E =
`sanitize_auth_diagnostic(t, max_length=None)` and P =
`PolicyManager().redact_secrets(t)` on the raw text; E and P on each of the
four spellings `json.dumps({"t": t}, ensure_ascii ∈ {True, False}, indent ∈
{None, 2})` (`EjAC EjAI EjUC EjUI PjAC PjAI PjUC PjUI`); `process_output(t)`
(POs); and `process_output({"t": t})` (POd, the dict-leaf path), whose result
type is recorded too. A scalar row is observed on `process_output(obj)`
(POo) with its type. A (row, surface) pair is **worse** when rev 10 keeps a
piece of the planted value that `main` removed on that surface (a piece
counts as present as written or percent-decoded, because `main`'s URL rule
re-encodes what it keeps), or when `main` returned a dict and rev 10 does
not. A worse pair is accepted only by a class decided from the row **as
written** (its `pre`, `qual`, `name`, `suffix`, `sep`, `value`, wrap) and
the surface, never from the output (`accepted()` in
`tests/_redaction_grammar.py`); anything else fails the test.

**How the evidence column was measured.** The audit's own counts
(`scratchpad/234-audit/audit.md`, `n/ctl/worse/GAP`) are **rev 9's** probes
and were not re-run. The rev-10 evidence is the grammar corpus itself: tier
2 (166 183 rows) observed on both trees for this plan, grouped by the
generator's bucket. For every bucket: rows, rows where `main` removes a
piece (the positive control), worse (row, surface) pairs by class, and
unaccepted pairs, which are **0 in every bucket**. "⊇" means no worse pair
in the buckets that sample the construct; "⊇ except X" means every worse
pair there is in class X.

### Table A: main's grammar vs rev 10

`main`'s engine (`main:src/pmcp/auth.py:576-608`) runs, in order: the URL
rule `https?://[^\s"'<>]+` → `redact_auth_url`;
`(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+`;
`(?i)(\bbearer\s+)[^\s,;]+`; the keyword rule **K** =
`(?i)\b([A-Za-z0-9_-]*(?:KEYS|api[_-]?key)[A-Za-z0-9_-]*)([\s:=]+)([A-Za-z0-9._~+/=-]{3,})`;
then `_JWT_RE`. `main`'s P runs the engine and then the seven
`DEFAULT_REDACTION_PATTERNS` over the rewritten text.

| row | main construct | rev 10 equivalent (`e79d75f`) | verdict | evidence (tier 2 bucket: rows / main removes / worse pairs by class) |
|---|---|---|---|---|
| A1a | K `\b`: ASCII punctuation before the key | `(?<![A-Za-z0-9])(?<!::)` in `_KEYWORD_KEY_SEP` (auth.py:529); `(?<![A-Za-z0-9_-])` in `_KEYWORD_WS_RE` (:597) | ⊇ except C3 | `pre:ascii-punct` 2 973 / 2 973 / C3 3 360 (whitespace-separator rows, D2) |
| A1b | K `\b` after each of the 29 `str.isspace()` characters (25 serialise as `\u00XX`, `\f`, `\u2028` …) | the same, or `_JSON_ESCAPE_BOUNDARY` (:430): a key may start right after any JSON escape; also in the P defaults (policy.py:74, :78) | ⊇ except C3 (was GAP F3) | `pre:isspace` 2 993 / 2 993 / C3 3 480 |
| A1c | K `\b` after a non-ASCII character (serialised `\uXXXX`) | as A1b | ⊇ except C3 (was GAP F3); raw text is better than `main` (its Unicode `\b` misses `épassword=`) | `pre:non-ascii` 1 841 / 1 841 / C3 890; 922 rows better |
| A1c' | K `\b` after a control character (`\u0000`, `\b`, `\u001b`, `\u007f`) | as A1b | ⊇ except C3 (was GAP F3) | `pre:control` 1 512 / 1 512 / C3 600 |
| A1d | K `\b` + prefix across `:` (`Database:Password=`) | a single `:` is a boundary; `::` is not (`(?<!::)`); nothing inside an `arn:`/`urn:` name starts a key (`_RESOURCE_NAME_RE`, :557) | ⊇ except C3, N10 (was GAP F1) | `pre:colon` 1 368 / 1 368 / C3 120; `pre:resource` 1 286 / 959 / N10 3 070 |
| A1e | K `\b` after `\` (`C:\secret=`, `DOMAIN\password=`, `\password x`) | a backslash is a boundary; only a JSON escape's tail may not start a qualifier or glued prefix (`_NOT_ON_AN_ESCAPE_TAIL`, :438) | ⊇ except C3 (was GAP F8) | `pre:backslash` 1 275 / 1 275 / C3 120 |
| A1f | K at the start of the text | the lookbehinds | ⊇ except C3, C6 | `pre:start` 1 372 / 1 372 / C3 120, C6 106 |
| A2a | K prefix `[A-Za-z0-9_-]*`, joined (`db_`, `X-`, 2-10 segments, a leading `_`/`-`/`--` run) | keyed rule `(?:[A-Za-z0-9]+[_-]){1,8}` (:529; the match restarts at a later segment); whitespace rule `[_-]*(?:[A-Za-z0-9]+[_-]+)+` unbounded but anchored (:597; N9) | ⊇ | `qual:joined` 1 956, `qual:leading-joiner` 2 022, `qual:multi-segment` 2 023: 0 worse |
| A2b | K prefix, glued (`dbpassword`, `PGPASSWORD`, `DBpassword`, > 24 characters) | `(?P<glued>…[A-Za-z0-9]{1,24}?)` + the glued gate (credential-shaped, not a URL/ARN); on the whitespace rule a single-case word or an acronym + Titlecase key (`_ACRONYM_TITLE_RE`, :802; N4b) | ⊇ except C6, N3, N4 | `qual:glued` 1 995: 0 worse; `qual:glued-long` 2 004 / 2 004 / N3 4 004; N4 97 in the mix rows; C6 1 190 across `pre:start`, `authorization:*` and the mix rows |
| A3 | K key alternation: every `main` key + `api[_-]?key` | `_secret_key_alternation()` (a superset) | ⊇ | `name:case` 7 014: 0 worse |
| A3b | K `code` is a key under any qualifier | bare/OAuth-qualified `code` (`_CODE_QUALIFIERS`); under a diagnostic qualifier (`_STATUS_CODE_QUALIFIERS`, :374, 37 words) its value is left to the shape rules; under any other qualifier a weak key gated on a credential-shaped value (N8, fails closed) | ⊇ except C12, C10 | `name:code-diagnostic` 1 002 / 1 002 / C12 3 360; `name:code-credential` 992: 0 worse; `code:diagnostic` 148 / C12 1 164; `code:credential` 52 / C10 312 |
| A4 | K `(?i)` | `re.IGNORECASE`; the camel boundary is case-sensitive | ⊇ | `name:case` (above) |
| A5 | K suffix `[A-Za-z0-9_-]*` | `(?:[_-]?(?:id\|key)\|s)?(?P<extra>(?:[_-]*[A-Za-z0-9]){0,24}[_-]*)` (:537; joiner runs and a trailing joiner, N2) + the suffixed gate | ⊇ except C5, N3 | `suffix:declared` 2 033, `suffix:descriptive` 2 023, `suffix:random` 1 983, `suffix:trailing-joiner` 2 015: 0 worse; `suffix:long` 1 946 / 1 702 / N3 5 856; C5 6 007 in the mix rows |
| A6a | K `[\s:=]+`: operator runs, line-break runs, a break before the operator | `[\"']?\s*(?:=>\|:=(?![:=])\|[:=]{2,4}(?![:=])\|[:=](?!=))` then `_BREAK` = `[^\S\r\n]*[\r\n]\s*` (:527, :539; N1, F4) | ⊇ except C4, C8, C3, C3a | `sep:operator-run` 1 830 / 1 828 / C4 351; `sep:line-break` 7 401 / 7 373 / C3 8 805, C8 945, C4 15, C3a 12; `sep:mixed` 11 531: 0 worse |
| A6b | K `[\s:=]+` with whitespace only | `_KEYWORD_WS_RE`: `[^\S\r\n]+` or `_BREAK`, gated on a credential-shaped value (D2) | ⊇ except C3, C3a | `sep:whitespace-only` 29 158 / 28 918 / C3 64 410, C3a 121 |
| A7 | K value `[A-Za-z0-9._~+/=-]{3,}` | `_BARE_RUN` (:543, wider), a quoted value, an unterminated-quote arm (N5); weak keys keep a plain word or number | ⊇ except C10, C4 | `value:main-class` 1 263 / C10 72, C4 48; `value:plain` C10 624; `value:number` C10 648; `value:json-literal` C10 300; `value:quoted` 1 299, `value:unterminated-quote` 1 223, `value:bracketed` 1 186, `value:punctuated` 1 278: 0 worse |
| A8 | `(?i)authorization\s*[:=]\s*(bearer\s+)?[^\s,;]+` | `_AUTHORIZATION_RE` (:653): whitespace, line breaks and escaped spaces on both sides of the operator, a scheme word, a wrapped value's inside (N6), a quoted value, a bare value never starting on `[`/`{` | ⊇ except C11, N6b, C8, C6 | `authorization:scheme` 1 819: 0 worse; `authorization:plain` 962 / 962 / C11 1 409, C6 317; `authorization:line-break` 2 398 / 2 119 / C11 609, N6b 582, C8 561, C6 155; `authorization:wrapped-value` 4 821 / 3 284 / N6b 1 757, C6 177 |
| A9 | `(?i)\bbearer\s+[^\s,;]+` | `_BEARER_RE` (:630): no `=`/`:` lookbehind (F2), a JSON escape is a boundary, escaped spaces separate, a wrapped token's inside (N7), a line-break run | ⊇ except N6b | `bearer:after-:/=` 6 463 / 6 463 / N6b 538; `bearer:plain-context` 1 038: 0 worse; `bearer:line-break` 467 / N6b 20; `bearer:wrapped-value` 2 032 / N6b 304 |
| A10 | `_JWT_RE` | identical (checked with `diff`) | ⊇ | — |
| A11 | the URL rule + `redact_auth_url` (userinfo, query keys and values, fragment) | `_url_spans` / `_url_component_spans` (:877-969), percent-decoded keys and values (C2), a trailing backslash handed back like trailing punctuation | ⊇ | `url` 10 000 / 8 897 / 0 worse, 1 687 rows better; `wrap:url-path` 1 286, `wrap:url-query-pair` 1 230, `wrap:url-query-value` 1 269, `wrap:url-fragment` 1 249: 0 worse |
| A12 | P1 `(api[_-]?key\|apikey)[\s]*[:=][\s]*["']?([^\s"']+)` | policy.py:70 plus the engine's spans on the P surface | ⊇ except the engine classes | every bucket above is observed on P and on the four P spellings; 0 unaccepted |
| A13 | P2 `(secret\|password\|passwd\|pwd)[\s]*[:=]…` (no `\b`) | policy.py:74 (a JSON escape is a boundary) plus the engine | ⊇ except N10 and the engine classes | as A12; `pre:resource` (N10) |
| A14 | P3 `(bearer\|token)[\s]+[a-zA-Z0-9._-]+` | dropped; the engine's whitespace rule (D2 gate) and `token` with a separator (policy.py:78) | ⊇ except C3 | `sep:whitespace-only` (above) |
| A15 | P4 `(aws_secret\|aws_access)[\s]*[:=]…` | policy.py:79 plus the engine keys | ⊇ except the engine classes | as A12 |
| A16 | P5-P7 `sk-`, `ghp_`, `github_pat_` | identical (checked) | ⊇ | — |
| — | P ran on the engine's rewritten text | every span is collected on the original text | not a narrowing | no row found either way |
| F7 | (no `main` construct: a rev-only over-match) | unchanged from rev 9 (measured: `{"a":"'password':'","b":"'"}` still loses member `b` on E and P; the output parses) | residual, not a leak | stated below |
| F9 | (no `main` construct: cost) | the PEM body is tempered, `(?:(?!-----BEGIN ).)*?` (:193) | fixed | `test_f9_the_pem_rule_is_linear` (2 s guard); mutant F9 |
| F10 | the `Authorization` scheme arm on a Digest header | unchanged from rev 9 (`Authorization: [REDACTED]"bob", response="…"`; `main`: `Authorization: [REDACTED] username="bob", response="…"`) | residual, cosmetic | the `response=` hash is kept on both trees |

### Table B: the serialiser's grammar vs rev 10

`process_output` serialises a dict with `json.dumps(obj, indent=2)`
(`ensure_ascii=True`). Python emits the escapes `\"`, `\\`, `\b`, `\f`,
`\n`, `\r`, `\t` and `\uXXXX` (U+0000-U+001F except `bfnrt`, U+007F, every
non-ASCII character, surrogate pairs), never `\/`. The grammar corpus places
the characters these escapes spell in each position (the `pre`, `sep` and
`value` axes); the POd and `Ej*`/`Pj*` surfaces see them escaped.
`test_the_grammar_corpus_covers_every_axis` asserts that `\"`, `\\`, `\b`,
`\f`, `\n`, `\r`, `\t`, `\u0000`, `\u001b`, `\u007f`, `\u0085`, `\u00a0`,
`\u3000`, `\u00e9` and the pair `\ud83d\ude42` all occur in the serialised
corpus.

| escape | before the key | as the separator | after `:`/`=` | inside the value |
|---|---|---|---|---|
| `\"` | ⊇ | ⊇ | ⊇ except N6b: an unterminated opening quote now has a value arm (N5); a wrapped `Authorization`/`Bearer` value's inside is redacted (N6/N7); a quoted `Authorization` value in a leaf is decision 3 | ⊇ except N6b (`authorization=ab"V` in a leaf) |
| `\\` | ⊇ except C3 (was GAP F8): `_NOT_ON_AN_ESCAPE_TAIL` | ⊇ | ⊇ | ⊇ |
| `\b`, `\f` | ⊇ except C3 (was GAP F3): `_JSON_ESCAPE_BOUNDARY` | ⊇ | ⊇ | ⊇ |
| `\n`, `\r` | ⊇ | ⊇ except C3, C8, C4, C3a (was GAP F4): `_BREAK` | ⊇ except C8 (was GAP F4) | ⊇ |
| `\t` | ⊇ | ⊇ except C3 | ⊇ | ⊇ |
| `\u0000`, `\u0001`, `\u000b`, `\u001b`, `\u001f`, `\u0085`, `\u007f` | ⊇ except C3 (was GAP F3) | ⊇ except C3 | ⊇ | ⊇ |
| `\u00a0`, `\u2028`, `\u3000` | ⊇ except C3 (was GAP F3) | ⊇ except C3; after `Bearer`/`Authorization:` an escaped space is separator, not value (`_JSON_SPACE_ESCAPE`, :444) | ⊇ | ⊇ |
| `\u00e9`, `\u5bc6`, `\u2022`, `\ud83d\ude42` | ⊇ except C3 (was GAP F3) | ⊇ | ⊇ | ⊇ |
| `/` (never escaped) | ⊇ | ⊇ | ⊇ | ⊇ |

Evidence: the `pre:*` buckets of table A (every worse pair there is C3,
C6 or N10); `sep:line-break`; `value:quoted` and
`value:unterminated-quote` (0 worse); `authorization:*` and `bearer:*`
(N6b is decided from the surface: it applies only on a serialised surface);
and the named tests `test_f3_a_json_escape_before_a_key_is_a_boundary`
(64 cases), `test_f3_the_policy_defaults_read_a_json_escape_as_a_boundary`,
`test_f3_an_escaped_space_after_bearer_or_authorization_separates`,
`test_f4_any_line_break_run_separates`, `test_f8_a_key_after_a_backslash`,
`test_n5_an_unterminated_opening_quote` and
`test_n6_n7_a_wrapped_bearer_or_authorization_value`.

**Bare scalars after a quoted key** (`process_output(dict)` only): every
scalar `json.dumps` emits — `null`, `true`, `false`, `0`, `-1`, `2**100`,
`1.5`, `-0.0`, `1e+20`, `1e-07`, `NaN`, `Infinity`, `-Infinity` — under 37
keys in three shapes (top level, nested, a list of objects): 13 × 111 =
1 443 rows. `main` returns a dict and removes nothing on every one (the
positive control is 0: these rows test the result **type**, not a leak);
rev 10 returns the same dict on every one (0 worse, `(dict, dict)` 1 443 of
1 443). This is decision 1 (`_JSON_SCALAR_RE`, :520): rev 9 turned
`NaN`/`±Infinity` into invalid JSON (dict → str, F5) and numbers into
`"[REDACTED]"` (F6). The text surfaces agree: `'{"max_tokens": 1024, "t":
NaN}'` is unchanged on E and P (measured); rev 9 wrote `"max_tokens":
"[REDACTED]"`.

### The generator: axes and sampling density

`tests/_redaction_grammar.py` (embedded below; its docstring is the
specification). A text row is `pre + qual + name + suffix + sep + value`,
optionally wrapped:

| axis | drawn from |
|---|---|
| `pre` | the character before the key: none; every ASCII punctuation character but the joiners; `:`; `\`; all 29 `str.isspace()` characters; control characters (`\u0000 \u0001 \b \u001b \u007f` once serialised); non-ASCII letters, punctuation and an astral character; an `arn:`/`urn:` context; each optionally after the word `abc` |
| `qual` | `main`'s `[A-Za-z0-9_-]*` prefix: joined, glued in random case (1-8), glued past the bound (25-32), a leading joiner run, 2-10 joined segments |
| `name` | `main`'s 20 keys and the policy keys, in lower, UPPER, Title and rAnDoM case; 30 % of name-focus rows are `code` under a random, a diagnostic (C12) or a credential qualifier (`key otp mfa sms verification recovery invite promo coupon discount api access secret`) |
| `suffix` | declared (`s _id _key Key -id id key`), a trailing joiner or joiner run, descriptive (`_new 2 Hash _type _length ized …`), random, and past the bound |
| `sep` | **every** 1- and 2-character string over the 31 characters `str.isspace()` + `:` + `=` (992), plus sampled 3-character ones; focus rows draw 1-3 characters, half operator, half space |
| `value` | `main`'s value class `[A-Za-z0-9._~+/=-]{3,14}`, plain words, integers, quoted, an unterminated opening quote, bracketed, JSON literals, punctuated |
| `bearer` | `bearer\s+[^\s,;]+` after 13 contexts (`X-Auth: `, `x=`, `token: `, …) or a random punctuation, non-ASCII or space character; a 1-3-character `\s` run; a credential, optionally wrapped in `"" '' () [] {} <>` |
| `authz` | `authorization\s*[:=]\s*(bearer\s+)?[^\s,;]+`: a prefix, 0-2 `\s` characters each side of the operator, a scheme (or none, or one followed by a blank line), a credential or plain value, optionally wrapped |
| `wrap` | bare; in prose (a word and one punctuation or space character each side); or in each position `main`'s URL rule reads: path, query pair, query value, fragment |
| `url` | `main`'s URL grammar: userinfo (`user`, `user:password`, `:password`), hosts (including a port that does not parse, which makes `main` keep the URL as written), 0-3 query pairs whose key is or is not in `AUTH_SECRET_QUERY_KEYS` (any case, optionally percent-encoded) with a credential, plain, percent-encoded or empty value, and a fragment |
| `scalar` | every scalar `json.dumps` emits under 37 keys, top level, nested and in a list of objects |

Sampling density (`corpus(tier)`; tier 2 is tier 1 followed by the rest;
seeds 20260926 and 20260927):

| block | tier 1 (default suite) | tier 2 adds (`slow`) |
|---|---|---|
| `sep` | the 992 1- and 2-character separators × `password`, `token` × {credential, plain}: 3 968 | the 992 × `secret`, `api_key`, `session` × 2, and 3 000 sampled 3-character ones × 5 keys × 2: 35 952 |
| `pre` | each of the 77 pre characters × `password`, `token` × (`=`, `: `, ` `) × {credential, plain}: 924 | lead `abc` × 5 keys, no lead × 3 more keys: 3 696 |
| `code` | `code` under each of the 37 diagnostic and 13 credential qualifiers × (`=`, `: `) × {credential, plain}: 200 | — |
| `focus:<axis>` | 500 per axis (`pre qual name suffix sep value wrap bearer authz url`): 5 000 | 9 500 per axis: 95 000 |
| `mix` | 1 000 (every axis at once, the wrap included) | 19 000 |
| `scalar` | 13 scalars × 37 keys × 3 shapes: 1 443 | — |
| **total** | **12 535** | **166 183** |

A focus row varies one axis and keeps the others benign (no pre, qualifier,
suffix or wrap; `=` or `: `; a credential value), so a failure is
attributed to one axis; mix rows vary all of them (the realistic
`java -jar x.jar -password hunter22x`, N9, was found only there).
`test_the_grammar_corpus_covers_every_axis` asserts that every claimed
axis, separator, pre character, escape, qualifier, URL part and surface is
produced (it would fail were one missing). The oracle fixture records
`main`'s observation code for every tier-2 row and a fingerprint of each
tier's corpus, so a corpus change without a new fixture fails.

**Runtimes** (24-core host, measured for this plan): tier 1 is
`test_grammar_differential_never_worse_than_main_except_by_stated_class`,
9.2 s, inside a default-tier `tests/test_redaction.py` of 472 tests in
21 s. Tier 2 is `test_grammar_differential_full_set`, 15 cases (one per
block), **15 passed in 195 s** single-process (the implementation agent
measured 171 s); it is marked `slow`, excluded by `addopts`, and **not run
by CI**. Observing all 166 183 rows on one tree takes about 274 s of CPU
(18 s on 20 processes).

### Accepted classes

Labels are `CLASSES` in `tests/_redaction_grammar.py`, verbatim; counts are
worse (row, surface) pairs, measured for this plan. Seed B is the same
generator with seeds 7 and 77 (tier 1 is its first 12 535 rows), `main`
and rev 10 both observed; the recorded second-seed run in the
implementation agent's scratch predates `e79d75f` and used the previous
generator (145 983 rows, no code, URL or wrap axes), so it is superseded by
this one.

| class | stated rule (decided from the row as written) | tier 1 | tier 2 | seed B tier 2 | decision |
|---|---|---|---|---|---|
| N3 | a glued prefix or a suffix of more than 24 alphanumerics: an identifier, not a key (a cost bound) | 1 308 | 24 955 | 26 204 | 2 |
| N10 | a key inside an `arn:`/`urn:` resource name names a resource | 387 | 5 912 | 6 132 | |
| C3a | `code` never fires on a whitespace-only separator | 96 | 2 086 | 2 174 | |
| C3 | a whitespace-only separator with a non-credential-shaped value (D2) | 10 448 | 83 874 | 83 873 | |
| N11 | a `name=value` token after a keyword and whitespace is its own pair | 0 | 24 | 20 | |
| N4 | a glued mixed-case key before whitespace is an identifier, not a key (`Ed25519PrivateKey X509Cert`, `gby3zPassword x`) | 0 | 97 | 133 | 4 |
| N4c | the key as written holds another key word starting inside the written name (`aws_accessid` holds `sid`) | 0 | 3 | 10 | |
| C4 | an operator run holding `==` or `::` with a non-credential-shaped value is a comparison or a path | 175 | 2 663 | 2 367 | |
| C5 | a suffixed key with a non-credential-shaped value names metadata | 257 | 6 007 | 5 803 | |
| C6 | a glued key (no joiner before the name, any case) counts only with a credential-shaped value that is not a URL or ARN | 39 | 1 190 | 1 022 | |
| C7 | after an unquoted key a value starting on `,`/`}` is JSON structure | **0** | **0** | **0** | |
| C8 | a separator whose last line break is unindented, with a non-credential-shaped unquoted value, ends a sentence | 69 | 1 542 | 1 605 | |
| N6b | a quoted value on a serialised surface is JSON inside a leaf (Consiliency/pmcp#290) | 188 | 3 201 | 2 902 | 3 |
| C10 | a weak key keeps a plain word or number; `code` under a non-OAuth qualifier keeps any non-credential-shaped value (N8's gate) | 408 | 2 583 | 2 540 | 5 |
| C11 | `Authorization`/`Bearer` followed by a plain word, bare or wrapped in quotes or brackets, is prose | 130 | 2 018 | 2 141 | |
| C12 | a `code` key qualified by a diagnostic or descriptive word (`DIAGNOSTIC_CODE_QUALIFIERS`, the qualifier's last segment) keeps its value (`error_code=E_TIMEOUT_42`, `sqlstate_code=42P01`) | 1 272 | 4 531 | 4 431 | 6 |
| **unaccepted** | | **0** | **0** | **0** | |

Totals: tier 1, 12 535 rows (11 070 with a value; `main` removes a piece on
10 070), 14 777 accepted pairs, 0 unaccepted; tier 2, 166 183 rows (144 641
positive), 140 686 accepted pairs, 0 unaccepted; seed B tier 1, 14 789
accepted, 0 unaccepted; seed B tier 2, 141 357 accepted, 0 unaccepted. Rows
better than `main` on some surface: 4 790 (tier 1), 72 285 (tier 2). Result
type (main, rev 10) over every dict-path row: tier 2 `(dict, dict)` 159 360,
`(str, dict)` 6 823, `(dict, str)` **0**.

**Class C7 was never used** on either tier or either seed; **N11, N4 and
N4c are 0 in tier 1** and occur only in tier 2. A class with a count of 0 is kept
because it is decided from the row, not from a count.

**Decision 1** (bare scalars unchanged) needs no class: rev 10 does what
`main` does on every scalar row. **Decision 5** (`key_code`) removes `key`
from the diagnostic list, so `key_code=abc123def456` is redacted as on
`main` (`test_n8_code_under_a_non_status_qualifier_is_a_weak_key`, mutant
N8b). **Decision 6**: the 37 diagnostic qualifiers of class C12, exactly
`_STATUS_CODE_QUALIFIERS` (auth.py:374; `test_n8_the_diagnostic_qualifiers_are_the_stated_class`
pins the two sets equal): `area byte char color colour country currency err
errno error event exception exit fault http iso item lang language locale
op opcode postal product rc reason region response result ret return sku
source sqlstate state status zip`. `promo`, `coupon` and `discount` are not
among them (they name redeemable values) and are sampled as credential
qualifiers. **Decision 7**: see Verification.

**Other measurements on rev 10.** `main`'s URL rows (`focus:url`, 10 000
tier-2 rows): `main` removes a secret in 8 897, rev 10 is worse on 0 and
better on 1 687 (seed B: 8 913 / 0 / 1 681); with the URL-wrap rows added
(15 034 rows), 11 402 / 0 / 4 450. The implementation agent reported 8 895
and 1 686 for the same block; the difference of 2 and 1 rows is a
definition difference not resolved here, and never-worse holds either way.
The structured-result differential (800 dicts, seed 20260924) and the JSON
fuzz (1 500 objects × 4 serialisations) are unchanged corpora, now
classified by the grammar classifier: result types (main, rev 10) `(dict,
dict)` 786, `(str, dict)` 14 and `(dict, dict)` 1 181, `(str, dict)` 319;
fuzz documents without `\"` 3 272 checked, 0 broken. The 810 JSON rows of
rev 9's string corpus (kept for `test_redaction_never_breaks_a_json_document`)
stay valid on both surfaces.

### Red/green: the rev-10 test file on rev 9 and on rev 10

The embedded `tests/test_redaction.py` (default tier) run with rev 9's
`src/` on `PYTHONPATH`, then on rev 10: **rev 9: 157 failed, 315 passed, 15
deselected; rev 10: 472 passed, 15 deselected.** Rev 10 has 96 test
functions (rev 9: 74): 23 new, and rev 9's hand-listed
`test_differential_against_main_never_worse_except_by_stated_class` is
removed. Red nodes on rev 9, by function:

| test function | red on rev 9 / nodes |
|---|---|
| `test_grammar_differential_never_worse_than_main_except_by_stated_class` | 1/1 |
| `test_b1_a_json_literal_after_a_quoted_key_keeps_the_document` (the two number rows: decision 1 reverses rev 9's rule) | 2/5 |
| `test_f5_f6_a_bare_json_scalar_after_a_quoted_key_is_unchanged` | 7/7 |
| `test_f3_a_json_escape_before_a_key_is_a_boundary` | 55/64 |
| `test_f3_the_policy_defaults_read_a_json_escape_as_a_boundary` | 4/4 |
| `test_f3_an_escaped_space_after_bearer_or_authorization_separates` | 5/5 |
| `test_f4_any_line_break_run_separates` | 13/13 |
| `test_f2_bearer_after_a_key_and_separator` | 8/8 |
| `test_f9_the_pem_rule_is_linear` | 1/1 |
| `test_f1_a_colon_qualified_key_is_a_key` | 7/7 |
| `test_n1_an_operator_run_separates` | 5/6 |
| `test_n2_a_trailing_joiner_or_joiner_run_in_the_suffix` | 6/6 |
| `test_n5_an_unterminated_opening_quote` | 4/4 |
| `test_n6_n7_a_wrapped_bearer_or_authorization_value` | 8/8 |
| `test_n8_code_under_a_non_status_qualifier_is_a_weak_key` | 10/10 |
| `test_f8_a_key_after_a_backslash` | 6/6 |
| `test_n9_a_single_dash_flag_before_whitespace` | 7/7 |
| `test_n4b_an_acronym_glued_to_a_titlecase_key` | 3/3 |
| `test_n8_the_diagnostic_qualifiers_are_the_stated_class` | 1/1 |
| `test_a_url_never_ends_on_the_backslash_of_an_escaped_quote` | 3/3 |
| `test_the_false_positive_guard_list_holds` (71 strings pinned on E, P and the dict leaf) | 1/1 |
| **total** | **157** |

Green on both by design: `test_the_grammar_corpus_covers_every_axis` (it
pins the generator, not the redactor) and
`test_sources_hold_no_literal_control_or_separator_characters` (it pins the
sources). `test_grammar_differential_full_set` is `slow` and deselected in
this run. The non-red nodes of the partly red functions are rows rev 9
already handled (9 of the 64 escape cases, 1 of the 6 operator runs, 3 of
the 5 B1 literals).

### Mutants

Each of the 21 mutants (`scratchpad/234-r10/mutants.py`, the implementation
agent's definitions) was applied to a copy of rev 10's `src/` (every anchor
asserted to occur exactly once), and the **whole** default-tier
`tests/test_redaction.py` (472 nodes, no `-k`) was run against it with
`pmcp.__file__` checked; the frozen tree's sha256 was checked unchanged at
the end. Three mutants edit two sites (F4b, N6/N7, F3c: the rule lives in
two regexes). **All 21 killed.**

| mutant | diff lines | whole file | red functions |
|---|---|---|---|
| F56: scalars → only `null`/`true`/`false` | 2 | 13 failed | `test_f5_f6_…`, `test_b1_…`, the structured differential, the grammar differential, the JSON fuzz, `test_redaction_never_breaks_a_json_document` |
| F3: escape boundary → rev 9's `\\[nrt]` | 2 | 53 failed | `test_f3_a_json_escape_…`, the grammar differential |
| F3b: an escaped space never separates | 3 | 6 failed | `test_f3_an_escaped_space_…`, the grammar differential |
| F3c: policy defaults without the escape boundary | 4 | 4 failed | `test_f3_the_policy_defaults_…` |
| F4a: a break → rev 9's `\r?\n` | 2 | 11 failed | `test_f4_…`, the grammar differential |
| F4b: no break before the operator | 4 | 4 failed | `test_f4_…`, the grammar differential |
| F2: the `Bearer` lookbehinds restored | 2 | 11 failed | `test_f2_…`, `test_f3_an_escaped_space_…`, the grammar differential |
| F9: PEM body `.*?` | 2 | 1 failed | `test_f9_the_pem_rule_is_linear` |
| F1: `:` is not a boundary | 2 | 9 failed | `test_f1_…`, `test_n1_…`, the grammar differential |
| F1b: no resource-name filter | 2 | 1 failed | `test_the_false_positive_guard_list_holds` |
| N1: operator runs → rev 9 | 2 | 12 failed | `test_n1_…`, `test_f8_…`, the guard list, the grammar differential |
| N1b: `::` not gated | 2 | 1 failed | the guard list |
| N2: suffix → rev 9 | 2 | 6 failed | `test_n2_…`, the grammar differential |
| N5: no unterminated-quote arm | 1 | 5 failed | `test_n5_…`, the grammar differential |
| N6/N7: no wrapped values | 4 | 14 failed | `test_n6_n7_…`, `test_f3_an_escaped_space_…`, the guard list, the grammar differential |
| N8: `code` under any other qualifier skipped | 5 | 11 failed | `test_n8_…weak_key`, the grammar differential |
| N8b: `key` back in the diagnostic list | 1 | 3 failed | both `test_n8_…`, the grammar differential |
| F8: no glued prefix after any backslash | 2 | 2 failed | `test_f8_…`, the grammar differential |
| N9: flag → rev 9's `(--)?` and `{0,8}` | 4 | 7 failed | `test_n9_…` |
| N4b: no acronym clause | 2 | 4 failed | `test_n4b_…`, the grammar differential |
| URL may end on a backslash | 2 | 4 failed | `test_a_url_never_ends_…`, the grammar differential |

### Residuals new or restated in rev 10

| residual | why | why acceptable |
|---|---|---|
| A numeric secret under a quoted key (`{"pin_code": 482913}`) is kept | decision 1: every bare scalar after a quoted key is left unchanged, as on `main` | `main` keeps it too; numeric secrets in structured results are Consiliency/pmcp#290 |
| `code` under a credential qualifier with a non-credential value (`otp_code=123456`) is kept; `main` redacts it | N8's gate: `code` under a non-OAuth, non-diagnostic qualifier is a weak key (class C10) | one-time codes are short-lived; a credential-shaped value (`otp_code=abc123def456`) is redacted |
| `code` under a diagnostic qualifier with a credential-shaped value (`status_code=abc123def456`) is kept; `main` redacts it | class C12 (decision 6): the value is left to the shape rules, which still catch an opaque run | status and error codes are diagnostics; the list is explicit and pinned |
| F7: a single-quote straddle in compact JSON text deletes a following member (`{"a":"'password':'","b":"'"}` → member `b` gone, E and P) | the other-quote guard runs only after an unquoted key | an over-match, not a leak; the output parses; the rev-9 seat noted it cannot occur through `process_output`'s `indent=2` dump (the straddle would cross a newline) |
| F10: `Authorization: Digest username="bob", …` → `Authorization: [REDACTED]"bob", …` | the scheme arm takes `Digest username=` as the value | cosmetic; the `response=` hash is kept on `main` too |
| The rev-9 test files are not in the plan any more | the embedded files are rev 10's | the rev-9 embedding is at `da97753` |

### Cross-reference: every rev-9 finding and every audit gap

| id | what | fix or class | evidence |
|---|---|---|---|
| F1 | colon-qualified keys (`Database:Password=`) leaked | a single `:` is a boundary, `::` and `arn:`/`urn:` names are not; class N10 for resource names | `test_f1_a_colon_qualified_key_is_a_key` 7/7 red on rev 9; mutants F1, F1b; `pre:colon`, `pre:resource` |
| F2 | `X-Auth: Bearer x`, `token: Bearer x` leaked | the `Bearer` `=`/`:` lookbehinds removed | `test_f2_…` 8/8; mutant F2; `bearer:after-:/=` (N6b only) |
| F3 | a JSON escape before a key (dict path) made it glued | `_JSON_ESCAPE_BOUNDARY` in every keyword rule, `Bearer` and the P defaults; `_JSON_SPACE_ESCAPE` after `Bearer`/`Authorization:` | `test_f3_…` ×3 (55/64, 4/4, 5/5); mutants F3, F3b, F3c; `pre:isspace`, `pre:non-ascii`, `pre:control` |
| F4 | blank line, bare CR, `\n\r`, `\r\r\n`, a break before the operator | `_BREAK` = any `\r`/`\n` run, on either side of the operator; class C8 for a plain value | `test_f4_…` 13/13; mutants F4a, F4b; `sep:line-break` |
| F5 | `NaN`/`±Infinity` under a key: dict → str | decision 1, `_JSON_SCALAR_RE` | `test_f5_f6_…` 7/7; mutant F56; the 1 443 scalar rows, 0 worse |
| F6 | numbers under a key became `"[REDACTED]"` | decision 1 (reverses rev 9's B1 number rule) | as F5; `test_b1_…` number rows now unchanged |
| F7 | single-quote straddle deletes a member | **not changed**: residual (over-match, not a leak) | measured on rev 10 |
| F8 | a key after a backslash (`C:\secret=`) | `_NOT_ON_AN_ESCAPE_TAIL`: only an escape's tail may not start a prefix | `test_f8_…` 6/6; mutant F8; `pre:backslash` |
| F9 | quadratic PEM rule | tempered body | `test_f9_…`; mutant F9 |
| F10 | Digest header cosmetic | **not changed**: residual | measured on rev 10 |
| N1 | operator runs (`===`, `::`, `=:`) | `[:=]{2,4}` operator run; `==`/`::` gated on a credential-shaped value (class C4) | `test_n1_…` 5/6; mutants N1, N1b; `sep:operator-run` |
| N2 | a trailing joiner or joiner run in the suffix | `extra` takes joiner runs | `test_n2_…` 6/6; mutant N2; `suffix:trailing-joiner` 0 worse |
| N3 | a glued prefix or suffix > 24 | **class N3** (decision 2) | `qual:glued-long`, `suffix:long` |
| N4a | glued mixed-case key, plain value | class C6 (any case) | mix rows |
| N4b | acronym + Titlecase key before whitespace (`PGPassword x`) | `_ACRONYM_TITLE_RE` on the whitespace rule; random-case rest is **class N4** (decision 4) | `test_n4b_…` 3/3; mutant N4b |
| N4c | an inner key word inside a mixed-case compound | **class N4c** | tier 2: 3 pairs |
| N5 | unterminated opening quote (`password="x`) | a value arm for it | `test_n5_…` 4/4; mutant N5; `value:unterminated-quote` 0 worse |
| N6 | `Authorization` value in brackets or quotes | the inside is redacted, the wrapping kept (not after a quoted key) | `test_n6_n7_…` 8/8; mutant N6/N7; `authorization:wrapped-value` |
| N6b | a quoted `Authorization` value in a JSON text leaf | **class N6b** (decision 3, Consiliency/pmcp#290) | `authorization:*`, `bearer:*` |
| N7 | `Bearer "x"`, `Bearer (x)` | the inside is redacted | as N6; `bearer:wrapped-value` |
| N8 | `code` under a non-OAuth qualifier (`otp_code=`) | a weak key gated on a credential-shaped value, except the diagnostic list (**class C12**, decision 6); `key_code` a weak key (decision 5) | `test_n8_…` ×2; mutants N8, N8b; `name:code-*`, `code:*` |
| N9 | single-dash or `_` flag (`-password x`) | the whitespace rule takes any `-`/`_` flag run | `test_n9_…` 7/7; mutant N9 |
| N10 | `arn:…:secret:Name` | **class N10** | `pre:resource` |
| N11 | `name=value` after a keyword and whitespace | **class N11** | tier 2: 24 pairs |
| URL | a URL ending on the backslash of `\"` in a leaf (found by the URL axis) | a trailing backslash is handed back | `test_a_url_never_ends_…` 3/3; mutant URL |


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
  "[REDACTED]"}` (rev 8: the quotes stay); a marker nothing touches is replaced by itself, which is what
  keeps `password=[REDACTED]`, `password= [REDACTED]` and `{"password":
  [REDACTED]}` fixed points on both surfaces. `REV 5 WAS WRONG`: it dropped
  any span touching an existing marker, so a downstream server could shield
  a value by writing the literal next to it (338/600 random values leaked in
  the board's sweep; `main` redacted them).
- **No span starts inside a backslash escape** (rev 9, class A):
  `widen_over_escapes`, applied to the engine's spans and to the policy
  surface's, gives a span that begins right after an escaping `\` that
  backslash too. `json.dumps` spells non-ASCII whitespace `\u3000`; rev 8's
  shape pass began a span at the `u`, left a lone `\`, and the serialised
  dict failed to re-parse.
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
   A **percent-encoded** query value under any key (`?q=%67%68%70%5F…`, an
   encoded `ghp_` token) is decoded and the whole engine asked about the
   decoded text; if anything in it would be redacted, the encoded value is
   redacted whole (`REV 6 WAS WRONG`: only keys were decoded, so the encoded
   prefix survived). **Rev 8:** the question is bounded and asked of both
   surfaces. `_covers_anything(text, depth, covers)` decodes at most
   `_MAX_DECODE_DEPTH = 3` levels (a redirect inside a redirect is two); at
   the bound it answers *yes*, so the value is redacted — failing closed
   (`REV 7 WAS WRONG`: unbounded, `"https://h.example/?q=" * 400 + "%" +
   "25" * 400 + "20"` raised `RecursionError` on both surfaces; grok and
   codex). `collect_redaction_spans(text, *, _depth=0, covers=None)` threads
   the depth, and the policy surface passes `covers=self._pattern_matches`
   so an operator or default pattern written for the decoded shape also
   decides (`REV 7 WAS WRONG`: `?q=%67%68%70%5Fabcdefghijklmnop` — the C-13
   probe, encoded — passed the policy surface, which `main` redacted; the
   engine still leaves it, which is the C-13 invariant). **Rev 9 (C2):** a
   percent-encoded query *key* is decoded too and the decoded
   `key=value` pair asked the same question (`?api%2Dkey=hunter2` leaked on
   rev 8; `main` redacted it). Diagnostic output for a redacted query value is now `?token=[REDACTED]`
   rather than `main`'s re-quoted `?token=%5BREDACTED%5D`; the rest of the
   URL is left exactly as written (no re-quoting, no IPv6 re-bracketing).
2. **`<key><sep><value>`** — `_keyword_sep_spans` (D1): the span is the
   value — for a **quoted** value, the text *inside* the quotes (rev 8), so
   `{"password": "hunter2"}` → `{"password": "[REDACTED]"}` is still JSON
   and a structured result that `process_output` serialised round-trips as
   a dict. `REV 7 WAS WRONG` in that respect only once the structured layer
   was removed: its span took the quotes too (`{"password": [REDACTED]}`),
   the re-parse failed and `process_output(dict)` silently returned a
   string — on the pre-fix rev-8 spike 120 of the 400 dict results (`main`:
   30) and 16 of 258 JSON rows per surface (`main`: 7); now 14 (all among
   `main`'s 30) and 0. `sep` is `:` or `=` on the same line, quotes allowed around key and
   value (JSON). The key starts at a non-identifier character — `.` counts
   as one since rev 9 (B3: `db.password=`, `self.password =`), and since
   rev 10 a single `:` does too (F1: `Database:Password=`), while `::`
   (`std::secret::Holder`) and anything inside an `arn:`/`urn:` resource
   name (`_RESOURCE_NAME_RE`; class N10) do not — or right after **any** JSON
   escape (rev 10, F3: `_JSON_ESCAPE_BOUNDARY`, `\n \r \t \b \f \" \\ \/`
   and `\uXXXX`; rev 9 had only `\n`/`\r`/`\t`), and a backslash is a
   boundary too (F8: `C:\secret=`), except that an escape's own tail may not
   start a qualifier or glued prefix (`_NOT_ON_AN_ESCAPE_TAIL`). **Rev 10
   (decision 1, F5/F6):** after a *quoted* key every bare scalar
   `json.dumps` emits (`null`, `true`, `false`, numbers, `NaN`,
   `±Infinity`; `_JSON_SCALAR_RE`) is left unchanged, as on `main`, so the
   document stays valid and the leaf keeps its type. This reverses rev 9's
   B1 rule, which turned a number into `"[REDACTED]"` (`{"max_tokens":
   1024}`, F6) and made `NaN` invalid JSON (F5); numeric secrets under a
   quoted key are Consiliency/pmcp#290's. **Rev 9 (N3,
   other-quote straddle):** after an *unquoted* key, a quoted "value" whose
   content starts on `,:]}` (`_STRADDLE_RE`) or contains the other quote
   character closes the string the key sits in and is skipped
   (`{"msg": "missing token: ", "code": 401}` stays valid). The key and
   separator are one shared regex fragment, `_KEYWORD_KEY_SEP`, which the
   list pass (2a) reuses. The key's **last** segment (`_`/`-` joined or camelCase)
   must be a secret key from `AUTH_DIAGNOSTIC_SECRET_KEYS` (or `api[_-]?key`),
   optionally suffixed `_id`/`_key`/`s`: `access_token=`, `X-Auth-Token:`,
   `accessToken=`, `"password": "…"`, `session_id=`, `Set-Cookie:` fire.
   **Rev 9:** the qualifier is 1-8 `_`/`-` segments (N4: `"a_" * n`
   was quadratic) and may start with a capital (B4: `AccessToken=`,
   `ClientSecret=`); a key may carry a `glued` prefix of ≤ 24 characters
   (C: `CLIENTSECRET=`, `dbpassword=`) or an `extra` suffix of ≤ 24
   alphanumerics (N1: `password2=`, `passwordHash=`, `secret_value=`;
   rev 10, N2: with joiner runs and a trailing joiner, `password_=`,
   `id_token__Xv=`) — longer is class N3 (decision 2) — and either one counts
   **only with a credential-shaped value that is not a URL or ARN**
   (declared compounds such as `secret_access_key` are exempt, via
   `_DECLARED_KEY_RE`). Measured on rev 9: `token_type=Bearer`,
   `token_endpoint=https://auth.example/oauth/token`, `secret_arn=arn:…`,
   `password_length=12`, `tokenizer=bert`, `errorcode=AADSTS50011` are
   kept; `password_hash=abc123def456`, `tokenizer=abc123def456` and
   `password_hash=sha256` (digit-bearing, 6 chars) are redacted, as on
   `main`. The key set (`AUTH_DIAGNOSTIC_SECRET_KEYS`) is the
   union of everything either surface ever named: rev 5 adds `passwd`,
   `pwd`, `private_key` (also `private-key`/`privateKey`),
   `secret_access_key`, `aws_secret`, `aws_access`, `credential`,
   `credentials`, `auth`. **Strong** keys redact **any** value. **Weak** keys
   (`WEAK_SECRET_KEYS` = `auth`, `code`, `credential`, `credentials`) keep a
   plain word or number — `credentials: include` is a fetch mode, `auth=basic`
   a scheme, `{"code": -32601}` a JSON-RPC error — and redact everything else
   (`auth=user:s3cret`, `credentials="…"`). `code` additionally needs to be
   bare or OAuth-qualified (pass 5). **The separator** is `:` or `=`, Ruby's
   `=>`, httpie's `:=`, or `==` (`password==hunter2` is httpie's query
   syntax). **Rev 9 (G1):** `==` may be followed by whitespace, and any `==`
   separator needs a credential-shaped value — `password == hunter2`,
   `if token == hunter2:` are redacted as on `main`, while
   `if token == expected:` is a comparison and survives. `REV 6 WAS WRONG`: `[:=](?!=)`, added for the
   comparison, rejected `:=` and `==`, and turned `{"password"=>"hunter2"}`
   into `"password"=[REDACTED]"hunter2"` — `main` redacted all three.
   Horizontal whitespace around the separator is `[^\S\r\n]` — every
   character `\s` matched on `main` except the line breaks (rev 8 had only
   space, tab and `\xa0`; `REV 8 WAS WRONG`, B5: U+3000, U+2003, `\v`, `\f`
   … leaked) — and a line break is `\n` or
   `\r\n` (HTTP header folding, Windows dumps) — `REV 7 WAS WRONG`: it
   dropped `\r` and `\xa0`, and `password:\r\nhunter2`, `password:\xa0hunter2`
   leaked where `main` redacted them (grok). **The value may sit on the next
   line** when that line is indented (YAML block style, pretty-printed JSON,
   a folded header), starts with a quote, or — unindented — is
   credential-shaped (`password:\r\nhunter2`, as `main` redacted it; the
   `_UNINDENTED_BREAK_RE` gate applies `_value_could_be_a_credential`);
   `token:\nthe bearer of` and `token:\n  - a bullet` are prose. **Rev 9
   (dash):** the continuation skips only a `--` flag or a lone `-*#>`
   marker followed by whitespace; a marker glued to a value
   (`password:\n  -hunter22`) is part of it and is redacted, as on `main`. A bare value
   ends at whitespace, a quote, a list separator (`,` `;`) or a query
   separator (`&`) and at nothing else, never *starts* on `[` or `{` (a list
   or an object is not one value; the one `[` allowed is the marker's own, so
   `token=[REDACTED]abc123def456` is still one value), and never *ends* on a closing bracket or
   a backslash (the `\` escaping a quote in a serialised string): `password=(Xk9mQ2vL)` is one value, punctuation and all, while the
   `}` of `{"password": [REDACTED]}` stays with the object. A quoted value runs
   to its **closing** quote, past escaped ones (`"(?:[^"\\\n]|\\.)*"` and
   the single-quote twin), never across a newline (a JSON string cannot hold
   one). **Rev 10 (N5):** an *unterminated* opening quote
   (`password="hunter22x`) is followed by a bare run, which is the value
   (`password="[REDACTED]`); when that run ends at a double quote it must be
   credential-shaped. (Rev 3's "unterminated value runs to end of line"
   rule stays dropped: every surface redacts before it cuts, so our own
   truncation never produces an unterminated value; this arm is for the
   server's.) **Rev 10 (F4, N1):** the separator is `[\"']?\s*` then an
   operator — `=>`, `:=`, a run of 2-4 `:`/`=`, or one `:`/`=` — so a line
   break may stand before the operator (`password\n: x`), and a line break
   after it is any `\r`/`\n` run (`_BREAK`: `password:\n\nx`,
   `token\r\r\nx`); a run holding `==` or `::` needs a credential-shaped
   value (class C4), and the unindented-break gate still applies (class
   C8).
   2a. **`<key><sep>[ … ]`** (rev 8) — `_keyword_list_spans`:
   `_KEYWORD_LIST_RE` is `_KEYWORD_KEY_SEP` followed by a **literal array**,
   `_LIST_BODY` (rev 9): quoted strings or JSON scalars, comma-separated,
   pretty-printed or not, nothing bare. That excludes a list of objects
   (N2), keeps a `]` inside a quoted element inside the list (C7:
   `["first]", "hunter2"]`, which leaked on `main` too), and refuses text
   that only looks like a list after an unquoted key in a string leaf
   (list straddle: `{"a": "token: [x", "b": "]"}`). Rev 8's body was
   `\[[^\[\]]*\]`. Each quoted element
   (`_QUOTED_RE`) is a value of the key and is redacted inside its quotes;
   an empty element yields a zero-length span, which `apply_redaction_spans`
   drops, and under a weak key a plain word or number
   is kept (`{"code": ["red", "x9Kq2mZ7"]}` → `["red", "[REDACTED]"]`); a
   `code` key needs the OAuth qualifier here too (C7: `error_codes: [...]`
   keep their values). No nested brackets: a list of objects carries its
   own keys. `REV 7 WAS
   WRONG`: `"password": [\n  "hunter2"\n]` lost its `[` and kept `hunter2`
   (`main` kept it too); rev 7 stated a keyed list as a residual, and it no
   longer is (`test_pretty_printed_list_value_is_redacted_inside_its_quotes`).
3. **`Authorization: …`** — `_authorization_spans`: the key may be
   JSON-quoted (`"authorization": "Bearer x"`), the separator is on the same
   line or followed by a continuation, whitespace and line breaks as in pass 2 (`Set the
   Authorization:\nheader first` is a sentence — `header` is a plain word;
   `Authorization:\n  s3cr3t` and `Authorization:\r\n  s3cr3tvalue` are
   folded headers). A **quoted**
   value is redacted whole between its quotes (`"Authorization": "Xk9 mQ2vL"`
   → `"Authorization": "[REDACTED]"`); a bare value is an optional HTTP auth
   scheme word (`Bearer`, `Basic`, `Digest`, `Negotiate`, `NTLM`, `Token`) then
   a run to whitespace, a quote or a list separator, never ending on a
   closing bracket or (rev 9, C6) a backslash, and never starting on `[`/`{`
   (rev 9, B2) — unless it is a plain word or number (`authorization:
   none`), which is prose (rev 5 had no such gate). After a quoted
   `Authorization` key a bare JSON scalar is left unchanged (decision 1).
   **Rev 10:** whitespace, line-break runs and escaped spaces
   (`_JSON_SPACE_ESCAPE`) may stand on either side of the operator (F4,
   F3), and a value wrapped in a bracket or quote after the optional scheme
   has its inside redacted and the wrapping kept (N6: `Authorization: [x]`
   → `Authorization: [[REDACTED]]`, `Authorization: Bearer "x"`), except
   after a quoted key, where `"Authorization": [1.5]` is JSON structure. A
   quoted `Authorization` value inside a JSON text leaf reaches the rule as
   `\"x\"` and is class N6b (decision 3, Consiliency/pmcp#290).
4. **`Bearer <value>`** — `_bearer_spans` (D3): the HTTP scheme, so the value
   is a token unless it is a plain word or number (`bearer token`, `bearer
   of`, `Bearer Token`) or a challenge parameter (`Bearer realm="…"`); not
   when `Bearer` is itself a value (`token_type=Bearer`). Boundary
   `(?<![A-Za-z0-9_-])`, never `\b`; the value stops at `"'()[]{}`, never
   ends on a backslash (rev 8: in a serialised leaf the token reads `Bearer
   hunter2tok\"`, and eating the `\` un-escapes the quote), and may
   follow a folded newline (whitespace and line breaks as in pass 2; rev 9,
   C4: onto an *unindented* line only when the value is credential-shaped
   and not a flag or bullet — `Bearer\r\nhunter2`). `REV 6 WAS WRONG`: the bearer-as-value lookbehind
   also excluded a *quote* before `Bearer`, so `{"text": "Bearer test-token"}`
   — how every JSON-serialised string arrives — passed through on both
   surfaces; `main` redacted it. Rev 7-9's lookbehind excluding `=`/`:`
   is **gone in rev 10** (F2: `X-Auth: Bearer x`, `token: Bearer x` leaked):
   the lookahead and the plain-word gate already keep `token_type=Bearer
   expires_in=3600`. Rev 10 also redacts the inside of a wrapped token
   (N7: `Bearer "x"` → `Bearer "[REDACTED]"`) and reads an escaped space
   after `Bearer` as separator (`_JSON_SPACE_ESCAPE`).
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
   never fires on whitespace. Rev 8: the whitespace may include one
   `\r?\n` with or without indentation (`token\r\nabc123def456`, as `main`
   redacted it); the credential-shape gate is what keeps
   `token\nthe bearer of` prose. **Rev 9:** the whitespace is `[^\S\r\n]`
   (B5); the key takes the same shapes as pass 2 — `_id`/`_key`, camelCase,
   suffixed and glued (N1, B, C: `SECRET_KEY abc123def456`,
   `--clientSecret …`, `password2 abc123def456`), a glued key only when it
   is a single-case word (`Ed25519PrivateKey X509Cert` is prose); a URL or
   ARN value is not redacted; the value never ends on a backslash (C6); and
   the flag/bullet guard skips only `--` or a lone `-*#>` marker followed
   by whitespace (`token -abc123def` is redacted, as on `main`). **Rev 10:**
   the flag may be `--`, a single `-`, `_` or any run of them, and the
   qualifier any number of joined segments (N9: `java -jar x.jar -password
   hunter22x` leaked); the match can only start where an identifier starts,
   so the unbounded qualifier stays linear here. An acronym glued to a
   Titlecase key counts (`_ACRONYM_TITLE_RE`, N4b: `PGPassword
   abc123def456`); a random-case glued key before whitespace stays prose
   (class N4, decision 4). A JSON escape before the key is a boundary (F3),
   and the separator may be a line-break run (`_BREAK`, F4).
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

**Serialisation — decision (c), scoped out to Consiliency/pmcp#290 (rev 8).**
`gateway.invoke` (`handlers.py` ~2068) and `gateway.tasks_result` (~6523,
`redact=True` by default) hand `process_output` the downstream result
**dict**. Rev 8 does what `main` does with it: `json.dumps(obj, indent=2)`,
then the same windowed text redaction as any string, then `json.loads` of
the result when it still parses (otherwise the string is returned — `main`'s
behaviour, unchanged). What that fixes, measured: `{"password": "hunter2"}`
is redacted in text **and** in a dict's serialised JSON, and because a keyed
value in quotes is redacted *inside* its quotes the dict comes back as a
dict (`{"password": "[REDACTED]"}`; a flat list under a secret key likewise,
pass 2a). Over the 400-result dict corpus, `main` returns 30 strings and rev
8 returns 14, every one of them among `main`'s 30
(`test_differential_on_structured_results_never_worse_than_main` asserts the
type is never worse, row by row, from the oracle's `dict_types`); over the
258 corpus rows that parse as JSON, rev 8 breaks none on either surface
(`main`: 7 per surface; `test_redaction_never_breaks_a_json_document`);
`test_structured_result_round_trips_as_a_dict` pins five shapes. **What it
does not fix:** JSON text *inside* a string leaf reaches the rules as
`\"password\": \"hunter2\"` and no keyed rule accepts the backslashes —
`{"content": [{"type": "text", "text": json.dumps({"password": "hunter2",
"api_key": "abc123def456", "Authorization": "Bearer hunter2tok"})}]}` keeps
`hunter2` and `abc123def456` (only `Bearer [REDACTED]`), exactly the two
`main` kept. That is Consiliency/pmcp#290's. The rev-7 decision (a) —
`PolicyManager._redact_leaves`, redacting each leaf as structure before the
dump — caught all three but is removed with the split: its six measured
defects are Consiliency/pmcp#290's acceptance criteria, and the bar for this issue is never
worse than `main`, which the dict differential measures.

**Tracebacks — the claim, narrowed.** Traceback *frames* (`File "…", line N,
in f`, reprs, `0x…` addresses, exception messages, pip/git/TLS lines) survive
byte-identically (the prose corpus and the prose property). A *source line*
that assigns to or annotates a secret-named variable does not:
`token = await self._get_token()` → `token = [REDACTED] self._get_token()`,
`def login(username: str, password: str)` → `password: [REDACTED])`,
`auth = aiohttp.BasicAuth(user, pw)` → `auth = [REDACTED], pw)`. That is by
design: in a log, what follows `token =` is a secret far more often than a
keyword, and exempting plain words after a *strong* key would leave
`password=hunter` visible everywhere. `if token == expected:` does survive,
on both surfaces since rev 8 (rev 9: an `==` separator needs a
credential-shaped value, so `if token == hunter2:` is redacted as on
`main` while a plain-word comparison survives; the policy defaults do not
take a spaced `==` at all — rev 7's policy surface turned it into
`if [REDACTED] expected:`, and `main`'s into `if token == [REDACTED]:`). Consiliency/pmcp#225 routes tracebacks through
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
- Rev 7 → 8: narrowing the continuation to `\n` + indent dropped `\r\n` and
  `\xa0` (worse than `main`); the decode question recursed without a bound;
  the decoded value was asked of the engine only; a quoted value's span
  took its quotes, which broke every JSON document it touched once the
  structured layer that hid it was gone. The last was found only by adding
  the result *type* to the dict differential — a never-worse test must
  compare everything the caller can observe, not only the removed pieces.
- Rev 8 → 9: every blocking finding of four seats sat outside the
  generator (JSON literals, dotted/PascalCase/suffixed/glued keys, Unicode
  whitespace, spaced `==`, encoded query keys, lists of objects). A
  never-worse differential proves never-worse *on its corpus*; its breadth
  is part of the claim, so rev 9 states the axes and an axis test asserts
  each one is generated.

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
| Numeric one-time codes under `code` (`code=123456`, and since rev 10 `otp_code=123456`; a credential-shaped `otp_code=abc123def456` is redacted, N8) | indistinguishable from JSON-RPC and HTTP codes; class C10 | single-use, minutes-lived, and a diagnostic from an untrusted server that quotes one gives the attacker nothing they did not already have |
| `Set-Cookie: a=b; c=d` — only the first pair | the value class stops at `;` (HEAD behaviour, unchanged) | the session pair is conventionally first; the residual is HEAD's |
| Low-entropy values under a non-OAuth `*_code=` (`error_code=super-secret`) | by design (pass 6) | the fail-closed direction: an opaque value is still caught by pass 10 |
| **False positive:** prefixed opaque *identifiers* — `req_011CfKTgoiRuc27pR2Po`, `cus_J1x2Yz3AbCd4Ef`, an Okta `aus…` id inside a *diagnostic* | shape-identical to `sk_live_…`; a denylist of secret prefixes fails open | a correlation id lost from a diagnostic is a support inconvenience; a token leaked is a compromise. The elicitation URL itself is untouched (defect 1) |
| **False positive:** MIME-wrapped base64 (76-column lines) and `data:` URIs under 256 chars | each line is a ≤ 256-char opaque run | one-run payloads (the common MCP `ImageContent` shape) survive; document as known |
| An **identifier-shaped** value after a **weak** key — letters and underscores in any case: `auth=secret`, `auth=my_secret`, `auth=EMBByY_b`, `{"code": "AccessDenied"}` — is kept (grok) | `WEAK_SECRET_KEYS` keep `[A-Za-z_]+` or a number by design; narrowing to lower-case snake would drop AWS-style `AccessDenied` codes, and the transition score cannot see `EMBByY_b` (no digits) | those keys name a mode, a scheme or an error code far more often than a secret; an identifier-shaped password under `auth=`/`credentials=` is the price, and `password=`/`secret=`/`token=` remain strong |
| ~~An **unterminated** quote in a bare form~~ — **fixed in rev 10** (N5): `password="hunter22x` → `password="[REDACTED]` on both surfaces | an arm for the bare run after the quote | — |
| A bare value that contains `&` (`password=a&b`) loses its tail; a bare value that begins with `=` (`key==value`) is not a value | `&` is a query separator and `==` a comparison, and the bare syntax cannot say otherwise | quoted forms carry both; `main` cut at `&` too |
| Hex signatures in signed URLs (`X-Amz-Signature=<64 hex>`) under keys not in `AUTH_SECRET_QUERY_KEYS` | uniform hex is never opaque; the key set is `main`'s | the same key set governed `main`; widening it is a one-line follow-up the implementer may take |
| Nested URL with userinfo inside a query value (`?next=https://u:p@h/`), bracketed keys (`user[password]=x`), PHP `[password] => x`, XML `<password>x</password>` | no rule reads these syntaxes | all leak on `main` too; the differential's accepted classes do not cover them because `main` does not redact them either — listed so the next revision knows |
| **False positive:** an 8+ char punctuated non-word after a bare keyword (`session re-issued-twice`) | the price of catching `--password correct-horse-battery-staple` | rare in diagnostics; the corpus holds `session re-use` (6 chars) as the boundary |
| **JSON text inside a string leaf** of a structured result (`\"password\": \"hunter2\"` in the serialised dict) | the escaped quotes defeat every keyed rule; redacting structure before the dump is Consiliency/pmcp#290 (decision (c), rev 8) | `main` leaks the same pieces (the dict differential proves never-worse row by row). The `Bearer`, shape and URL rules still reach inside the leaf (measured: `Bearer [REDACTED]`, a `ghp_…` token and a `?token=` value inside a `json.dumps` leaf are redacted); a URL that ends the escaped string takes the `\` before its closing `\"`, so that result comes back as a string — on `main` too (`?token=%5BREDACTED%5D"}`), and also Consiliency/pmcp#290's |
| ~~Plain-text quoted value on the policy surface loses its opening quote~~ — **fixed in rev 9** (rev 8 statement): `password="x"` → `password="[REDACTED]"` on both surfaces (rev 8 `password=[REDACTED]"`, `main` `password= [REDACTED]"`) | the policy defaults no longer start a value on a quote or end it on a backslash (`(?![\"'])([^\s\"']*[^\s\"'\\])`); the engine redacts the quoted value inside its quotes | — |
| A separator spelled as a JSON `\uXXXX` escape falls inside the span: `{"t": "token\u3000abc123def456"}` → `{"t": "token[REDACTED]"}` | `widen_over_escapes` extends a span back over the escape so no lone `\` is left | cosmetic (the separator character is lost); the document stays valid and the dict a dict; `main` left the value unredacted |
| A suffixed or glued key with a non-credential-shaped value is kept: `password_length=12`, `token_type=Bearer`, `CLIENTSECRET=letters`; and a spaced `==` before a plain word (`if token == expected:`) | the key names metadata, or the line is a comparison (N1, C, G1) | accepted classes in the differential (216, 95 and 99 row-surfaces); a credential-shaped value under the same keys is redacted |
| JSON text inside a string leaf still breaks 4 of the 6 000 fuzz documents per surface | every one carries `\"` (JSON inside a leaf) | Consiliency/pmcp#290; 0 of the 3 272 in scope break |
| **False positive:** a query value that is still percent-encoded after three decodes | `_MAX_DECODE_DEPTH = 3` fails closed | no real URL nests that deep; failing open would let downstream text recurse the diagnostic path to death (rev 7's `RecursionError`) |

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
- **`_secret_key_alternation`, `_KEYWORD_KEY_SEP`, `_KEYWORD_SEP_RE`,
  `_KEYWORD_LIST_RE`, `_QUOTED_RE`, `_KEYWORD_WS_RE`, `_BEARER_RE`,
  `_AUTHORIZATION_RE`, `_URL_RE`, `_UNINDENTED_BREAK_RE`** — add — the
  keyword, keyed-list, Bearer, Authorization and URL patterns.
  `_KEYWORD_KEY_SEP` is the key-and-separator fragment shared by
  `_KEYWORD_SEP_RE` and `_KEYWORD_LIST_RE` (rev 8; a key may also start
  after a JSON-escaped `\n`/`\r`/`\t`; separators take `\xa0` and `\r?\n`).
  `_KEYWORD_SEP_RE`'s quoted-value branches close at the quote
  (escape-aware, never across a newline) and a bare value never starts on
  `[`/`{` except the marker's own `[`; the `Bearer` and `Authorization`
  value classes stop at `"'()[]{}`, and a `Bearer` value never ends on `\`.
- **`_keyword_sep_spans`, `_keyword_list_spans`, `_keyword_ws_spans`,
  `_bearer_spans`, `_authorization_spans`, `_url_component_spans`,
  `_url_spans`, `_covers_anything`** — add — one span producer per pass,
  each reading the text it is given and nothing else; the URL producer
  yields component spans (userinfo → empty, secret query values →
  `[REDACTED]`, fragment → empty), never a rewritten URL.
  `_keyword_sep_spans` redacts a quoted value inside its quotes and skips an
  unindented next-line value that is neither quoted nor credential-shaped;
  `_keyword_list_spans` (rev 8) redacts each quoted element of a flat list
  inside its quotes. `_url_component_spans(base, raw_url, depth, covers)`,
  `_url_spans(text, depth, covers)` and `_covers_anything(text, depth,
  covers)` carry the decode depth and the policy surface's pattern test.
- **`_MAX_DECODE_DEPTH = 3`, `Covers = Callable[[str], bool]`** — add (rev
  8) — the decode bound (fails closed) and the type of `covers`;
  `from collections.abc import Callable` added.
- **Rev 10 additions and changes** (each with its finding; see the
  rev-10 section's cross-reference) — `_JSON_ESCAPE_BOUNDARY` (a key starts
  after any JSON escape: F3), `_NOT_ON_AN_ESCAPE_TAIL` (only an escape's
  tail may not start a qualifier or glued prefix: F8), `_JSON_SPACE_ESCAPE`
  (an escaped space after `Bearer`/`Authorization:` separates: F3),
  `_JSON_SCALAR_RE` (every bare scalar after a quoted key is left
  unchanged: decision 1, F5/F6; replaces rev 9's `_JSON_NUMBER_RE`),
  `_BREAK` (any line-break run: F4), `_BARE_RUN` and an unterminated-quote
  value arm (N5), `_RESOURCE_NAME_RE` and `_in_resource_name` (`arn:`/`urn:`
  names: F1, N10), `_STATUS_CODE_QUALIFIERS` and `_last_segment` (the 37
  diagnostic `code` qualifiers, class C12; any other qualifier makes `code`
  a weak key: N8, decisions 5 and 6), `_ACRONYM_TITLE_RE` (N4b). In
  `_KEYWORD_KEY_SEP`: `(?<![A-Za-z0-9])(?<!::)` (F1), a 1-8 segment
  qualifier, `extra` with joiner runs (N2), `\s*` before the operator and
  an operator run `[:=]{2,4}` (F4, N1). `_KEYWORD_WS_RE`: any `-`/`_` flag
  run and an unbounded, anchored qualifier (N9). `_BEARER_RE`: the `=`/`:`
  lookbehinds removed (F2), a wrapped token (N7). `_AUTHORIZATION_RE`: a
  wrapped value's inside (N6), escaped spaces and line breaks around the
  operator. The PEM vendor shape has a tempered body (F9). A URL hands
  back a trailing backslash.
- **Rev 9 additions** — `_STRADDLE_RE` (a quote whose content starts on
  `,:]}` closes the enclosing string: N3), `_JSON_NUMBER_RE` (B1),
  `_DECLARED_KEY_RE` (declared compound keys are exempt from the
  suffixed-key gate: N1), `_LIST_BODY` (the literal-array list body: N2, C7,
  list straddle), `_is_single_case` (a glued key on the whitespace rule: C),
  and the public **`widen_over_escapes(text, spans)`** (class A; imported by
  `policy.py`). `_KEYWORD_KEY_SEP` gains the `glued` and `extra` groups, a
  capital-led camelCase qualifier, a `{0,8}` qualifier bound, `.` before a
  key, `[^\S\r\n]` whitespace and `==` followed by whitespace;
  `_KEYWORD_WS_RE`, `_BEARER_RE` and `_AUTHORIZATION_RE` take the same
  whitespace, key shapes and flag/bullet guard, and never end a value on a
  backslash; `_url_component_spans` decodes a percent-encoded key (C2);
  `collect_redaction_spans` returns `widen_over_escapes(text, spans)`.
- **`collect_redaction_spans(text, *, _depth=0, covers=None)`,
  `apply_redaction_spans`** — add (public; used by `policy.py`) — the union
  of every pass's spans, and the single-step applier with the merge rules in
  the Design section.
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
  group). Rev 8: the four key/value defaults take the engine's separator
  grammar, so `if token == expected:` is a comparison on this surface too
  while `password==hunter2` and `password:=hunter2` are still redacted.
  **Rev 9:** each is now
  `…[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])`
  — Unicode horizontal whitespace (B5), a value that never starts on a
  quote (the engine redacts a quoted value inside its quotes, which removes
  rev 8's opening-quote residual) and never ends on a backslash (C6); the
  `secret|password|passwd|pwd` lookbehind drops `.` (B3). **Rev 10 (F3):**
  the `secret|password|passwd|pwd` and `token` defaults also start after a
  JSON escape (`(?<=\\[nrtbf/\"\\])|(?<=\\u[0-9a-fA-F]{4})`); the other
  defaults are unchanged.
- **`PolicyManager.truncate_output`** — modify — gains keyword-only
  `original_size: int | None = None`; when the original exceeded the cap the
  same `max_size - 100` cut and marker apply whatever the window's own size
  (slicing a short window is a no-op). No back-up of the cut (revs 2-3's
  `_TRAILING_*` machinery is not added).
- **`PolicyManager._truncation_marker`** — add (staticmethod) — the marker
  string, used by both paths.
- **`PolicyManager._pattern_matches`** — add (rev 8) — does any operator
  (or default) pattern match a text; passed as `covers` so a
  percent-decoded query value is asked of the patterns too.
- **`PolicyManager.redaction_spans`** — add —
  `collect_redaction_spans(output, covers=self._pattern_matches)`
  plus one span per operator-pattern match, all passed through
  `widen_over_escapes` (rev 9) (value part after the first
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
  unchanged. A non-string result is serialised with `json.dumps(indent=2)`
  and redacted as text, and re-parsed afterwards — both exactly as on
  `main` (decision (c); rev 7's `_redact_leaves` structured branch is not
  added).

### `tests/test_redaction.py` (new)

Both corpora, the composition tests, and three property tests; bodies under
`## Test bodies`. **Rev 10: 96 test functions, 487 nodes: 472 in the
default tier (472 passed in 21 s) and 15 marked `slow` (tier 2 of the
grammar differential, deselected by default).** Rev 10 adds two files: the
grammar module `tests/_redaction_grammar.py` and the oracle regenerator
`tests/fixtures/regen_redaction_main_oracle.py` (both embedded below). Rev
9 had 306 tests in 74 functions, rev 8 149. The property-test counts below
were measured on the rev-9 code (`8e7d98c`) with an instrumented scratch
copy of the test file (prints only); rev 10 did not change those
generators, and the property tests pass on rev 10:

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
  `password=…` and `X-Api-Key: …`; 10 310 strings, 0 skipped, both surfaces;
  the probe is the first four characters of the value *as encoded*, skipped
  when the surrounding template already contains it);
  `test_property_truncation_never_exposes_a_keyed_password` (300 seeded
  passwords × the same forms, `process_output(redact=True)` at every
  `max_bytes` from two before the value's first byte to two past its last:
  1 020 values, 23 035 cuts, 16 915 inside a value — asserted per value —
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
  design, for a strong key nothing is; 31 keys, 6 106 strings, 0 leaks on
  both surfaces). Per-key results (rev 6 generator: `&` is a bare
  delimiter too; rev 8 generator: a bare value is also stripped of a
  leading `[`/`{`. The table is the rev-8 run; rev 7's said 6 158 strings in
  the prose and 6 124 under the table, and neither is carried forward. Rev 9
  did not change this generator; re-measured on rev 9: 31 keys, 6 106
  strings, 0 leaks — the per-key cells are a function of the generator
  alone):

| key | weak? | json | single-quoted | key=value | header | leaks (engine/policy) |
|---|---|---|---|---|---|---|
| `access_token` | strong | 60 | 60 | 33 | 33 | 0 |
| `api-key` | strong | 60 | 60 | 38 | 38 | 0 |
| `api_key` | strong | 60 | 60 | 38 | 38 | 0 |
| `apikey` | strong | 60 | 60 | 36 | 36 | 0 |
| `assertion` | strong | 60 | 60 | 36 | 36 | 0 |
| `auth` | weak | 59 | 59 | 39 | 39 | 0 |
| `aws_access` | strong | 60 | 60 | 44 | 44 | 0 |
| `aws_secret` | strong | 60 | 60 | 44 | 44 | 0 |
| `client_secret` | strong | 60 | 60 | 40 | 40 | 0 |
| `code` | weak | 60 | 60 | 37 | 37 | 0 |
| `cookie` | strong | 60 | 60 | 31 | 31 | 0 |
| `credential` | weak | 60 | 60 | 41 | 41 | 0 |
| `credentials` | weak | 60 | 60 | 42 | 42 | 0 |
| `id_token` | strong | 60 | 60 | 37 | 37 | 0 |
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
| `secret_access_key` | strong | 60 | 60 | 41 | 41 | 0 |
| `session` | strong | 60 | 60 | 37 | 37 | 0 |
| `set-cookie` | strong | 60 | 60 | 40 | 40 | 0 |
| `sid` | strong | 60 | 60 | 35 | 35 | 0 |
| `tenant-id` | strong | 60 | 60 | 38 | 38 | 0 |
| `tenant_id` | strong | 60 | 60 | 37 | 37 | 0 |
| `token` | strong | 60 | 60 | 35 | 35 | 0 |

keys: 31; strings checked: 6106; total leaks: 0 (rev 8 code, measured with an instrumented scratch copy of the test)

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
  7 318 spans on rev 9 (7 304 on rev 7/8: the collected spans depend on the
  code), 0 leaks);
  `test_property_the_window_edge_never_emits_an_unredacted_value`
  (`max_bytes=1000`, a PEM block sized to the edge, 80 pads, 13 straddling
  the edge — asserted — 0 leaks, output ≤ cap);
  `test_never_worse_than_main[…]` (42 inputs whose main-removed pieces —
  measured by running `main` @ `860636a`'s engine and policy over them,
  minus main's documented collateral `next`/`home`/`access_token` — must
  still vanish on both surfaces: 50 secrets);
  `test_a_containing_span_may_drop_an_inner_one_only_if_it_covers_it` (the
  merge rule as a property of `apply_redaction_spans`: an outer replacement
  weaker than the inner span it would suppress — grok's framing — merges to
  `[REDACTED]`); `test_the_window_edge_is_not_emitted_when_ordinary_values_shrink_it`
  (codex's construction: two 8 300-char `token=` values fill the window and
  an unterminated JSON object sits at its far edge; every value under 16 KiB,
  so not the documented residual). The URL test also carries the CLI seats'
  round-4 cases: `https://alice:hunter2@example.test/cb?q=ghp_…` (userinfo
  stripping is a no-op rewrite for the query), `?file=sk-live-…` (a key that
  will never be on `AUTH_SECRET_QUERY_KEYS`), and an operator pattern
  matching inside a URL path (never suppressed by a built-in).
- **Rev 7:** `test_quoted_bearer_and_httpie_and_ruby_separators`,
  `test_percent_encoded_query_values_are_decoded_before_the_detectors`,
  `test_a_value_may_sit_on_the_next_indented_line` — the four blocking
  regressions and the newline formats, named (expected outputs updated in
  rev 8 to the quote-preserving form);
  **`test_differential_against_main_never_worse_except_by_stated_class`**
  (the board's corpus regenerated in-test, `main`'s removals from the oracle
  fixture, every kept piece either removed here or in an accepted class
  decided from the row's key/separator/value/wrap — see the table below —
  and more removed than `main` on > 1 000 rows). Rev 7's
  `test_property_structured_results_are_redacted_leaf_by_leaf` is **removed**
  with the structured layer (decision (c)).
- **Rev 8:** `test_crlf_and_no_break_space_are_whitespace_too` (item 2: the
  four grok cases on both surfaces, and `token:\nthe bearer of` /
  `Missing bearer token:\r\nthe bearer of` still prose);
  `test_url_decode_depth_bound_fails_closed` and
  `test_encoded_query_values_are_decoded_a_bounded_number_of_times` (item
  3: no `RecursionError`, the value `[REDACTED]`);
  `test_operator_pattern_applies_to_a_percent_encoded_query_value` and
  `test_encoded_query_values_are_matched_by_the_policy_patterns_too` (item
  4: an operator pattern and the `ghp_` default decide a decoded value; the
  engine still leaves the encoded C-13 probe alone);
  `test_a_comparison_survives_on_both_surfaces` (item 5);
  `test_differential_on_structured_results_never_worse_than_main` (400
  dict results, seed 20260924, six shapes — JSON text leaf, CRLF header
  leaf, prose leaf with a URL, top-level key, nested key, list — through
  `process_output(dict, redact=True)`; every piece `main` removed from the
  serialised result is removed here or accepted, and wherever `main`'s
  result is a dict so is this one);
  `test_redaction_never_breaks_a_json_document` (the 258 corpus rows that
  parse as JSON still parse on both surfaces; `checked == 258` asserted);
  `test_structured_result_round_trips_as_a_dict[…]` (5 shapes: a keyed
  string, a keyed list, a nested key, an empty value, a weak key's list);
  `test_pretty_printed_list_value_is_redacted_inside_its_quotes`. 13
  expected-output lines in 7 existing tests moved to the quote-preserving
  form (`{"password": [REDACTED]}` → `{"password": "[REDACTED]"}`), and
  `_keyed_forms` strips a leading `[`/`{` from a bare value. The string
  differential's `_DIFF_SEPS` gains `":\r\n  "`, `"\r\n  "`, `":\xa0"`,
  `":\r\n"`, which reshuffles the seeded draws, so its corpus and oracle are
  not rev 7's.

- **Rev 9:** 24 new test functions (157 new nodes), one per finding class:
  `test_b1_a_json_literal_after_a_quoted_key_keeps_the_document`,
  `test_b2_authorization_never_starts_on_a_bracket`,
  `test_b3_b4_dotted_and_pascal_case_keys`,
  `test_b5_every_whitespace_character_separates`,
  `test_n1_suffixed_keys_redact_a_credential_shaped_value`,
  `test_n1_suffixed_keys_keep_metadata`,
  `test_n2_a_list_of_objects_is_not_a_flat_value`,
  `test_n3_a_quote_opening_on_structure_is_not_a_value`,
  `test_n4_the_key_qualifier_is_bounded` (2 s timing guard on `"a_" * 33000`),
  `test_g1_a_spaced_double_equals_before_a_secret`,
  `test_c2_a_percent_encoded_key_is_still_the_key`,
  `test_c4_bearer_across_a_crlf`,
  `test_c6_a_quote_inside_a_leaf_keeps_the_dict`,
  `test_c7_a_bracket_inside_a_quoted_list_element`,
  `test_policy_defaults_never_start_on_a_quote_or_end_on_a_backslash`,
  `test_a_keyed_list_never_straddles_a_string_boundary`,
  `test_a_quoted_value_never_straddles_via_the_other_quote`,
  `test_no_span_starts_inside_a_json_escape`,
  `test_a_case_folded_compound_key_is_still_a_key`,
  `test_a_mixed_case_identifier_is_not_a_glued_key`,
  `test_a_dash_glued_to_a_value_is_part_of_it`,
  `test_a_flag_or_a_bullet_after_a_keyword_is_not_its_value`,
  `test_the_differential_corpus_covers_every_axis`,
  `test_property_json_fuzz_keeps_documents_and_dicts`. Two existing
  assertions changed: `test_bearer_is_a_scheme_not_a_word` now expects
  `secret-bearer hunter2` → `secret-bearer [REDACTED]` (a suffixed `secret`
  key with a credential-shaped value, as on `main`; `secret-bearer failed`
  is still kept), and `test_crlf_and_no_break_space_are_whitespace_too`
  asserts only that `hunter2` is gone on the policy surface for the `\xa0`
  case (the policy default now reads `\xa0` as whitespace and consumes it).
  No test was removed.

#### The never-worse differential, rev 9: generator axes

> **Superseded in rev 10.** Rev 9's hand-listed string differential and its
> word-list classifier are removed; the grammar-derived differential in
> "Rev 10: never worse than main, by main's grammar" replaces them. Kept as
> history. (The dict corpus and the JSON fuzz below are kept in rev 10, now
> classified by the grammar classifier.)

The claim "never worse than `main`" is exactly as broad as the corpus it is
measured on, so the axes are stated here and
`test_the_differential_corpus_covers_every_axis` asserts that each one is
actually generated (a check that fails if an axis goes missing).

- **String corpus** (`_differential_corpus`, seed 20260923, **8 000 rows**):
  one random draw per axis per row, the key upper- or title-cased 30 % of
  the time, then two rows per key holding a JSON literal as a quoted member
  of a JSON wrap (bare, `"token": null`, and quoted, `"token": "null"`).
  - *keys* — plain, snake, kebab and header names; dotted (`db.password`,
    `self.password`, `config.api_key`); PascalCase and camelCase
    (`AccessToken`, `ClientSecret`, `clientSecret`, `passwordHash`,
    `apiKey`); suffixed — credential (`password2`, `password_confirmation`,
    `secret_value`, `token_id`, `SECRET_KEY`) and descriptive (`token_type`,
    `password_length`, `token_endpoint`, `secret_arn`); `--flag` forms;
    case-folded by the 30 % casing (`CLIENTSECRET`, `Dbpassword`);
  - *separators* — `=`, `:`, `: `, ` = `, `=>`, `:=`, quoted forms
    (`="{}"`, `: "{}"`, `": "{}"`), a quoted key before a bare value
    (`": `), backslash-escaped quotes, prose non-separators (` is `, `|`,
    `->`), LF and CRLF continuations (indented and not), spaced `==`
    (` == `, `== `, ` ==`), and **every `str.isspace()` character except
    `\r`/`\n`** (27), alone and after `:` — pinned against a full Unicode
    enumeration in the axis test;
  - *values* — credential-shaped (vendor tokens, JWT, AWS, base64, random
    alnum, passphrase), plain words and numbers, punctuation, a URL,
    non-ASCII, JSON literals (`null`, `true`, `false`, `12345`, `-1.5e3`),
    and values starting on `,` or `}`;
  - *wraps* — prose, brackets, quotes, markup, URLs (query, fragment,
    userinfo), shell, repeated pairs, and JSON documents: mixed value types,
    the pair as a member, a nested object, a list of objects.
- **Dict corpus** (`_dict_corpus`, seed 20260924, **800 results**): keys and
  string values from the string axes, a quarter of the values JSON scalars
  (`None`, `True`, `False`, `42`, `-1.5`, `0`), separators `: `, `=`,
  `: "{}"`, ` == `, `:　`; nine shapes — JSON text leaf, CRLF header
  leaf, prose leaf with a URL, top-level key, nested key, list of leaves,
  mixed value types, three-deep object, list of objects.
- **JSON fuzz** (`_json_fuzz_corpus`, the claude seat's fuzz, committed:
  seed 7, **1 500 objects**, each serialised four ways — compact and
  `indent=2`, `ensure_ascii` on and off — so 6 000 documents per surface):
  17 credential and neutral keys; values random strings of delimiters,
  quotes, backslashes, `\t`/`\n`, U+3000, NBSP, `é` and trigger words
  (`password=`, `token: `, `Bearer `, a URL with `?token=`, `"password": "`,
  `[`, `]`), nested lists and objects up to three deep, JSON scalars.
  **Exclusion rule, decided from the input:** a document containing `\"`
  (JSON inside a string leaf) is Consiliency/pmcp#290's scope and is not
  checked for validity (2 728 of 6 000 excluded; 3 272 checked).

#### Measured on rev 9

> **Superseded in rev 10** (the accepted-class counts below are rev 9's; rev
> 10's are in "Accepted classes" under the rev-10 section).

String differential: rows 8000; rev 9 removes more than main on 1902 rows; 1792 (row, surface) pairs keep a piece main removed, all 1792 in accepted classes; **unaccepted: 0**.

| # | accepted regression class (label in `_accepted_regression_class`) | row-surfaces | why it is by design |
|---|---|---|---|
| 1 | `whitespace-only separator with a non-credential-shaped value (D2)` | 1161 | `token bucket`, `session expired`: a whitespace-only separator redacts only a credential-shaped value; the 27 Unicode spaces multiplied these rows |
| 2 | `a suffixed key names metadata unless the value is credential-shaped (N1: `password_length=12`)` | 216 | `token_type=Bearer`, `password_length=12` name things |
| 3 | `` `==` is a comparison unless the value is credential-shaped (G1: `if token == expected:`) `` | 99 | a comparison in a source line is not an assignment |
| 4 | `a glued, single-case key counts only with a credential-shaped value that is not a URL or ARN (C)` | 95 | `CLIENTSECRET=letters` is not a credential; a glued prefix is only trusted with a credential-shaped value |
| 5 | `` `code` never fires on a whitespace-only separator (main redacted `code<TAB>s3cr3t`) `` | 72 | `exit code 137`, JSON-RPC codes |
| 6 | `` inside a URL query main re-encoded `key<sep>value` as a key (`%3A`, `%7C`); not a redaction `` | 53 | `main`'s re-encoding collateral: `parse_qsl` re-spelled the pair as a key; the value was never redacted |
| 7 | `weak key keeps a plain word or number` | 42 | `credentials: include`, `auth=basic` |
| 8 | `unindented line-break continuation with a non-credential-shaped value (D2 applied to a line break)` | 23 | `token:\nthe bearer of` is prose |
| 9 | `` after an unquoted key a value starting on `,`/`}` is JSON structure (N3: `"missing token: ", "code"`) `` | 17 | the "value" is the next member of the enclosing object |
| 10 | `` `Authorization`/`Bearer` followed by a plain word is prose `` | 14 | `authorization: none`, `Bearer Token is required` |

The classifier has two more arms that matched **0** rows on this corpus and
stay because they are decided from the row, not from a count:
`` `is`/`|`/`->` are not separators (main: URL re-encoding of `|`, or its whitespace rule) ``
(every `|` row sits inside a URL query and class 6 catches it first) and
`backslash-escaped quotes in a plain string (JSON inside a leaf is Consiliency/pmcp#290)`.
Rev 8's classifier arm on `&` read the output (`kept`) and could not fire
(gemini); it is removed.

Dict differential: 800 results; `main` removes something on 172. Result
type (main → rev 9): **dict → dict 786, str → dict 14, dict → str 0** —
every result `main` returned as a dict is a dict here, and the 14 `main`
returned as strings now parse. 14 (row) pairs keep a piece `main` removed,
all accepted: class 3 ×11, class 2 ×2, class 7 ×1; **0 unaccepted**.

JSON validity (`test_redaction_never_breaks_a_json_document`): the 810
string rows that parse as JSON still parse on both surfaces (`main` breaks
8 per surface; rev 8 broke 34).

JSON fuzz (`test_property_json_fuzz_keeps_documents_and_dicts`): 3 272
documents checked, 0 broken on either surface (`main` breaks 358 engine /
398 policy of the same 3 272, and 1 026 / 1 311 of all 6 000); rev 9 breaks
4 / 4 of all 6 000, every one excluded by the `\"` rule. `process_output`
returns a dict for every one of the 1 181 objects `main` returned as a dict
(`main`: 1 181 dict / 319 str, recorded in the oracle's `fuzz_types`).

#### Red/green: the rev-9 test file on rev 8 and on rev 9

> History (rev 9). Rev 10's red/green is in the rev-10 section.

The embedded test file run with rev 8's `src/` (`56d7f80`) on `PYTHONPATH`,
then on rev 9: **rev 8 144 failed, 162 passed; rev 9 306 passed.** Every
red node is in a rev-9 function or in one of the four existing tests whose
oracle or corpus widened:

| test function | red on rev 8 / nodes |
|---|---|
| `test_bearer_is_a_scheme_not_a_word` (the `secret-bearer hunter2` expectation) | 1/1 |
| `test_differential_against_main_never_worse_except_by_stated_class` | 1/1 |
| `test_differential_on_structured_results_never_worse_than_main` | 1/1 |
| `test_redaction_never_breaks_a_json_document` | 1/1 |
| `test_b1_a_json_literal_after_a_quoted_key_keeps_the_document` | 5/5 |
| `test_b2_authorization_never_starts_on_a_bracket` | 2/2 |
| `test_b3_b4_dotted_and_pascal_case_keys` | 21/21 |
| `test_b5_every_whitespace_character_separates` | 24/27 |
| `test_n1_suffixed_keys_redact_a_credential_shaped_value` | 12/12 |
| `test_n2_a_list_of_objects_is_not_a_flat_value` | 1/1 |
| `test_n3_a_quote_opening_on_structure_is_not_a_value` | 1/1 |
| `test_n4_the_key_qualifier_is_bounded` | 1/1 |
| `test_g1_a_spaced_double_equals_before_a_secret` | 3/3 |
| `test_c2_a_percent_encoded_key_is_still_the_key` | 1/1 |
| `test_c4_bearer_across_a_crlf` | 1/1 |
| `test_c6_a_quote_inside_a_leaf_keeps_the_dict` | 2/2 |
| `test_c7_a_bracket_inside_a_quoted_list_element` | 1/1 |
| `test_policy_defaults_never_start_on_a_quote_or_end_on_a_backslash` | 5/5 |
| `test_a_keyed_list_never_straddles_a_string_boundary` | 2/2 |
| `test_a_quoted_value_never_straddles_via_the_other_quote` | 2/2 |
| `test_no_span_starts_inside_a_json_escape` | 24/24 |
| `test_a_case_folded_compound_key_is_still_a_key` | 24/24 |
| `test_a_dash_glued_to_a_value_is_part_of_it` | 7/7 |
| `test_property_json_fuzz_keeps_documents_and_dicts` | 1/1 |
| **total** | **144** |

Four rev-9 functions are green on rev 8 **by design**: they pin text that
must survive or the generator's breadth, not a defect —
`test_a_flag_or_a_bullet_after_a_keyword_is_not_its_value`,
`test_a_mixed_case_identifier_is_not_a_glued_key`,
`test_n1_suffixed_keys_keep_metadata`,
`test_the_differential_corpus_covers_every_axis`. The three
`test_b5_every_whitespace_character_separates` nodes green on rev 8 are the
characters rev 8 already handled (`\t`, space, `\xa0`). On rev 8
`test_n4_the_key_qualifier_is_bounded` ran for over two minutes before
failing (the quadratic scan).

Rev 8's measurements (4 000 string rows, 294 accepted in 6 classes, 1 737
better; 400 dict rows, 14 str among main's 30) are superseded: the corpus is
different.


### `tests/test_auth.py`

- **No change.** See the verdict above; 128 passed unchanged.

### Patch (measured)

`git diff 860636a origin/wip/234-redactor-rev10-code -- src/` (the frozen
rev-10 code @ `e79d75f`), verbatim (`sha256` of the revised files:
`auth.py c00faa67…4eab`, `policy.py 055c149a…6b56`). Every rev-10
measurement, probe and mutant in this plan ran against exactly this code.
`auth.py` and `policy.py` are unchanged from `860636a` to today's `main`
(`1fb36f2`), so the patch applies to either.

```diff
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index f40ccbb..7a5ff46 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -4,6 +4,7 @@ from __future__ import annotations
 
 import json
 import re
+from collections.abc import Callable
 import time
 import asyncio
 from collections.abc import Mapping
@@ -12,7 +13,7 @@ from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
 from itertools import product
 from typing import Any, Literal
 from urllib.error import HTTPError
-from urllib.parse import parse_qsl, quote, urlparse, urlunparse
+from urllib.parse import parse_qsl, quote, urlparse, urlunparse, unquote
 from urllib.request import HTTPRedirectHandler, Request, build_opener
 
 import aiohttp
@@ -70,15 +71,24 @@ AUTH_DIAGNOSTIC_SECRET_KEYS = {
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
@@ -87,12 +97,964 @@ AUTH_DIAGNOSTIC_SECRET_KEYS = {
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
+#: A separator whose (last) line break is followed by no indentation.
+_UNINDENTED_BREAK_RE = re.compile(r"[\r\n][^ \t\xa0]*$")
+
+
+def _in_resource_name(text: str) -> Callable[[int], bool]:
+    ranges = [(m.start(), m.end()) for m in _RESOURCE_NAME_RE.finditer(text)]
+    return lambda position: any(a <= position < b for a, b in ranges)
+
+
+def _keyword_sep_spans(text: str) -> list[Span]:
+    spans: list[Span] = []
+    in_resource_name = _in_resource_name(text)
+    for match in _KEYWORD_SEP_RE.finditer(text):
+        if in_resource_name(match.start()):
+            continue  # `arn:…:secret:Name` names a secret, it is not one
+        name = match.group("name").lower()
+        if name in WEAK_SECRET_KEYS and _is_plain_word_or_number(match.group("value")):
+            continue
+        if _UNINDENTED_BREAK_RE.search(match.group("sep")) and not (
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
+    spans: list[Span] = []
+    in_resource_name = _in_resource_name(text)
+    for match in _KEYWORD_LIST_RE.finditer(text):
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
+    return [
+        (match.start("value"), match.end("value"), REDACTED)
+        for match in _KEYWORD_WS_RE.finditer(text)
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
+    return [
+        (match.start("value"), match.end("value"), REDACTED)
+        for match in _BEARER_RE.finditer(text)
+        if not _is_plain_word_or_number(match.group("value"))
+        and not (
+            _UNINDENTED_BREAK_RE.search(match.group("key"))
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
+    spans: list[Span] = []
+    for match in _AUTHORIZATION_RE.finditer(text):
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
+    for match in _URL_RE.finditer(text):
+        raw_url = match.group(0)
+        # Trailing sentence punctuation is handed back, and so is a trailing
+        # backslash: in a serialised leaf it escapes the closing quote
+        # (`…?sid=x\\"`), and a query-value span that ate it broke the JSON.
+        while raw_url and raw_url[-1] in ").,;\\":
+            raw_url = raw_url[:-1]
+        spans.extend(_url_component_spans(match.start(), raw_url, depth, covers))
+    return spans
+
+
+def collect_redaction_spans(
+    text: str, *, _depth: int = 0, covers: Covers | None = None
+) -> list[Span]:
+    """Every redaction the engine would make to ``text``, as spans over it.
+
+    Each pass reads the same, unmodified ``text``. The order of the list does
+    not matter: `apply_redaction_spans` merges overlaps. ``covers`` lets the
+    policy surface add its own patterns to the question asked of a decoded
+    query value (a raw encoded value evades a pattern written for the
+    decoded shape; main decoded before matching, and so does this).
+    """
+    spans = [
+        *_url_spans(text, _depth, covers),
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
+    widened: list[Span] = []
+    for start, end, replacement in spans:
+        run = 0
+        while start - run - 1 >= 0 and text[start - run - 1] == "\\":
+            run += 1
+        if run % 2 == 1 and start < end:
+            start -= 1
+        widened.append((start, end, replacement))
+    return widened
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
@@ -576,35 +1538,10 @@ def sanitize_url_elicitation_url(
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
index cac2702..1c353a6 100644
--- a/src/pmcp/policy/policy.py
+++ b/src/pmcp/policy/policy.py
@@ -22,7 +22,13 @@ from pmcp.types import (
     ServerPolicy,
     ToolPolicy,
 )
-from pmcp.auth import sanitize_auth_diagnostic
+from pmcp.auth import (
+    REDACTED,
+    Span,
+    apply_redaction_spans,
+    collect_redaction_spans,
+    widen_over_escapes,
+)
 
 if TYPE_CHECKING:
     # Annotation only. `pmcp.manifest`'s package `__init__` imports the loader and
@@ -44,12 +50,33 @@ _ListPolicy = ServerPolicy | ToolPolicy | ResourcePolicy | PromptPolicy
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
     r"\bsk-[A-Za-z0-9_-]{6,}\b",
     r"\bghp_[A-Za-z0-9_]{10,}\b",
     r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
@@ -666,18 +693,31 @@ class PolicyManager:
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
@@ -687,29 +727,54 @@ class PolicyManager:
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
 
+        The engine's spans and the operator's pattern spans are all collected
+        over the same, unmodified ``output`` and applied in one step, so the
+        operator's patterns never see -- and never depend on -- the engine's
+        rewriting (Consiliency/pmcp#234).
+        """
+        return apply_redaction_spans(output, self.redaction_spans(output))
+
+    def _pattern_matches(self, text: str) -> bool:
+        """Does any operator (or default) pattern match ``text``? Asked of a
+        percent-decoded query value so an encoded token cannot evade a
+        pattern written for its decoded shape (main decoded before matching)."""
+        return any(regex.search(text) for regex in self._redaction_regexes)
+
+    def redaction_spans(self, output: str) -> list[Span]:
+        """Every redaction either surface would make to ``output``, as spans."""
+        spans: list[Span] = collect_redaction_spans(
+            output, covers=self._pattern_matches
+        )
         for regex in self._redaction_regexes:
-
-            def replace_match(match: re.Match[str]) -> str:
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
+        return widen_over_escapes(output, spans)
 
     def process_output(
         self,
@@ -731,11 +796,42 @@ class PolicyManager:
 
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

`tests/test_redaction.py`, verbatim from `origin/wip/234-redactor-rev10-code`
@ `e79d75f` (sha256 `247b7349…75f3`; 487 tests, 472 in the default tier;
`ruff check`, `ruff format --check` clean). It imports
`tests/_redaction_grammar.py` (under `## Grammar module`). The fixture it
reads, `tests/fixtures/redaction_main_oracle.b64`, is the plan-folder
`.b64` (sha256 `a3a9777a…39e1`, 1 009 lines):

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

The never-worse-than-main differential is grammar-derived
(`tests/_redaction_grammar.py`). Its tier 1 runs in the default suite; its
full set is marked `slow` and runs only on request (`pytest
tests/test_redaction.py -m slow`, about 2-3 minutes). `addopts` excludes
`slow`, so CI (`pytest tests/`) does not run it.
"""

from __future__ import annotations

import base64
import gzip
import json
import random
import re
import string
import uuid
from pathlib import Path
from typing import Any

import pytest

from pmcp.auth import (
    AUTH_DIAGNOSTIC_SECRET_KEYS,
    AUTH_SECRET_QUERY_KEYS,
    REDACTED,
    WEAK_SECRET_KEYS,
    apply_redaction_spans,
    collect_redaction_spans,
    redact_auth_url,
    sanitize_auth_diagnostic,
)
from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
from tests import _redaction_grammar as G

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
    # not the Bearer SCHEME: the word after a compound is not a token ...
    assert _engine("secret-bearer failed") == "secret-bearer failed"
    # ... but `secret-bearer` is a suffixed `secret` key, and a credential-shaped
    # value after it is redacted by that rule, as on main
    assert _engine("secret-bearer hunter2") == "secret-bearer [REDACTED]"
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
        ('{"password": "a\\"hunter2"}', '{"password": "[REDACTED]"}'),
        ("{'password': 'a\\'hunter2'}", "{'password': '[REDACTED]'}"),
        (
            '{"password": "hunter2", "user": "bob"}',
            '{"password": "[REDACTED]", "user": "bob"}',
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
        ('{"password": "hunter2 Bearer test-token"}', '{"password": "[REDACTED]"}'),
        ("{'password': 'hunter2 Bearer x'}", "{'password': '[REDACTED]'}"),
        (
            '{"authorization": "Bearer abc123def456", "x": 1}',
            '{"authorization": "[REDACTED]", "x": 1}',
        ),
        ('Authorization: "Bearer abc.def"', 'Authorization: "[REDACTED]"'),
        ('{"note": "see Bearer abc123def456"}', '{"note": "see Bearer [REDACTED]"}'),
        ('token="Bearer abc"', 'token="[REDACTED]"'),
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
    assert _engine(text) == '{"password": "[REDACTED]"}'
    assert _policy(text) == '{"password": "[REDACTED]"}'
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
    assert processed["result"].startswith("a" * 190 + ' {"password": "[REDACTED]')


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
    # a bare value never ends on a closing bracket or a backslash
    # a bare value never starts on `[`/`{` nor ends on a closing bracket or a backslash
    bare = bare_tokens[0].lstrip("[{").rstrip(")]}\\") if bare_tokens else ""
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
        # grok/codex, round 4: userinfo stripping is a no-op rewrite for the
        # query, and `q`/`file` will never be on `AUTH_SECRET_QUERY_KEYS`
        (
            "https://alice:hunter2@example.test/cb?q=" + TOKEN,
            "https://example.test/cb?q=[REDACTED]",
        ),
        (
            "https://example.test/dl?file=sk-live-abc123def456",
            "https://example.test/dl?file=[REDACTED]",
        ),
    ]:
        assert _engine(text) == expected, text
        assert _policy(text) == expected, text
    # an OPERATOR pattern inside a URL path is never suppressed by a built-in
    manager = PolicyManager()
    manager._redaction_regexes = [re.compile(r"abcdef")]
    assert (
        manager.redact_secrets("https://example.test/abcdef")
        == "https://example.test/[REDACTED]"
    )
    assert _engine("password=https://h.example/?a=1,b=2") == "password=[REDACTED],b=2"
    assert "h.example" not in _policy("password=https://h.example/?a=1,b=2")
    # `redact_auth_url` itself is byte-for-byte main's: the elicitation URL
    assert (
        redact_auth_url("https://u:p@auth.example/cb?ticket=secret&x=1#frag")
        == "https://auth.example/cb?ticket=%5BREDACTED%5D&x=1"
    )


def test_a_containing_span_may_drop_an_inner_one_only_if_it_covers_it() -> None:
    """The merge rule, stated as a property of `apply_redaction_spans`.

    An outer span whose replacement is `[REDACTED]` or empty covers whatever
    it contains; any other outer replacement is weaker than the inner spans
    it would suppress (grok's framing), so the two merge to `[REDACTED]`.
    Rev 5's whole-URL rewrite was exactly such a weaker outer span.
    """
    text = "xx SECRET yy"
    inner = (3, 9, REDACTED)
    assert apply_redaction_spans(text, [(0, 12, "xx SECRET yy"), inner]) == "[REDACTED]"
    assert apply_redaction_spans(text, [(0, 12, "rewritten"), inner]) == "[REDACTED]"
    assert apply_redaction_spans(text, [(0, 12, REDACTED), inner]) == "[REDACTED]"
    assert apply_redaction_spans(text, [(0, 3, ""), inner]) == "[REDACTED] yy"


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
        ('{"password": "hunter2 [REDACTED]"}', '{"password": "[REDACTED]"}'),
        ('{"password": "[REDACTED] hunter2"}', '{"password": "[REDACTED]"}'),
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


def test_the_window_edge_is_not_emitted_when_ordinary_values_shrink_it() -> None:
    """Codex's construction: no PEM needed. Two 8 300-char `token=` values fill
    the 16 684-char window with 70 chars of an unterminated JSON object at
    its far edge; redaction shrinks the window to ~100 chars, and rev 5
    returned all of it, `hunter2` included. Every value is under 16 KiB, so
    this is not the documented residual. Now only the cap, extended to the
    end of the span that straddles it, is kept.
    """
    text = ("token=" + "a" * 8300 + "\n") * 2
    text += '{"password": "hunter2 ' + "tail " * 20 + '"}'
    result = PolicyManager().process_output(text, redact=True, max_bytes=300)
    assert result["truncated"] is True
    assert "hunter2" not in result["result"], result["result"]
    assert result["result"].startswith("token=[REDACTED]\n")
    assert len(result["result"].encode("utf-8")) <= 300


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


# === rev 7: the differential against main is THE never-worse test ========= #


def test_quoted_bearer_and_httpie_and_ruby_separators() -> None:
    """Rev 6 regressions against main, each a rule narrowed for a false
    positive without re-running the differential (board round 5).

    A quote before `Bearer` is how every JSON-serialised string arrives;
    `:=`/`==` are httpie's syntax; `=>` is Ruby's. `if token == expected:`
    (the false positive the narrowing was for) still survives.
    """
    for text, expected in [
        ('{"text": "Bearer test-token"}', '{"text": "Bearer [REDACTED]"}'),
        ("'Bearer abc123def456'", "'Bearer [REDACTED]'"),
        ('"Bearer abc123def456"', '"Bearer [REDACTED]"'),
        ("password:=hunter2", "password:=[REDACTED]"),
        ("token:=abc123def456", "token:=[REDACTED]"),
        ("password==hunter2", "password==[REDACTED]"),
        ('{"password"=>"hunter2"}', '{"password"=>"[REDACTED]"}'),
        ("if token == expected:", "if token == expected:"),
        ("token_type=Bearer expires_in=3600", "token_type=Bearer expires_in=3600"),
    ]:
        assert _engine(text) == expected, text
        assert "hunter2" not in _policy(text) and "abc123def456" not in _policy(text), (
            text
        )
    assert PolicyManager().process_output({"text": "Bearer test-token"}, redact=True)[
        "result"
    ] == {"text": "Bearer [REDACTED]"}


def test_percent_encoded_query_values_are_decoded_before_the_detectors() -> None:
    encoded = "".join(f"%{ord(c):02X}" for c in "ghp_") + TOKEN[4:]
    assert (
        _engine(f"https://h.example/?q={encoded}") == "https://h.example/?q=[REDACTED]"
    )
    assert (
        _policy(f"https://h.example/?q={encoded}") == "https://h.example/?q=[REDACTED]"
    )
    assert (
        _engine("https://h.example/?q=%20hello%20world")
        == "https://h.example/?q=%20hello%20world"
    )


def test_a_value_may_sit_on_the_next_indented_line() -> None:
    """YAML block style, pretty-printed JSON and a folded header are formats;
    a keyword at the end of a sentence followed by an unindented line, or by
    a bullet, is prose."""
    assert _engine('"password":\n    "hunter2"') == '"password":\n    "[REDACTED]"'
    assert _engine("password:\n  hunter2") == "password:\n  [REDACTED]"
    assert _engine("[x-api-key\n  abc123def456]") == "[x-api-key\n  [REDACTED]]"
    assert (
        _engine("Missing bearer token:\nthe bearer of")
        == "Missing bearer token:\nthe bearer of"
    )
    assert _engine("token:\n  - a bullet") == "token:\n  - a bullet"


# --- the differential ------------------------------------------------------- #

_DIFF_KEYS = [
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "client_secret",
    "session",
    "sid",
    "cookie",
    "set-cookie",
    "authorization",
    "bearer",
    "jwt",
    "saml",
    "assertion",
    "id_token",
    "aws_secret",
    "aws_access",
    "tenant_id",
    "code",
    "auth_code",
    "x-api-key",
    "private_key",
    "credentials",
    # rev 9 (B3): dotted keys -- a config path or an attribute
    "db.password",
    "spring.datasource.password",
    "self.password",
    "config.api_key",
    # rev 9 (B4): PascalCase and camelCase keys
    "AccessToken",
    "ClientSecret",
    "SessionToken",
    "DbPassword",
    "clientSecret",
    "sessionToken",
    "passwordHash",
    "apiKey",
    # rev 9 (N1): suffixed keys -- credential suffixes and descriptive ones
    "password2",
    "password_confirmation",
    "secret_value",
    "token_id",
    "SECRET_KEY",
    "token_type",
    "password_length",
    "token_endpoint",
    "secret_arn",
    # rev 9 (N1): command-line flags
    "--token",
    "--password",
    "--clientSecret",
    "--sessionToken",
    "--token_id",
]
_DIFF_VALUES = [
    "hunter2",
    "abc123def456",
    "s3cr3t",
    "correct-horse-battery-staple",
    "4eC39HqLyjWDarjtT1zdp7dc",
    "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
    "sk-live-abc123def456",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.N2QwODhm",
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "dXNlcjpwYXNzd29yZA==",
    "Xk9#mQ2vL",
    "p@ss w0rd",
    "abc",
    "12345678",
    "letters",
    "a.b.c",
    "x-y-z",
    "https://h.example/?t=1",
    "(paren)",
    "tok[1]",
    "🙂ß",
    # rev 9 (B1): JSON literals -- after a quoted key they are JSON, not text
    "null",
    "true",
    "false",
    "12345",
    "-1.5e3",
    # rev 9 (N3): values starting on a JSON delimiter
    ",hunter22",
    "}hunter22",
    ", s3cr3tval",
]
_DIFF_SEPS = [
    "=",
    ": ",
    ":",
    " = ",
    "=>",
    " => ",
    "->",
    ":=",
    "\n  ",
    ":\n  ",
    "\t",
    " ",
    "  ",
    '="{}"',
    "='{}'",
    ': "{}"',
    '": "{}"',
    '\\": \\"{}\\"',
    " is ",
    "|",
    # rev 8 (grok): CRLF header folding and Windows dumps, a no-break space,
    # and an unindented CRLF continuation -- every whitespace class a rule touches
    ":\r\n  ",
    "\r\n  ",
    ":\xa0",
    ":\r\n",
    # rev 9 (G1): a spaced `==`, and a quoted key before a bare value (B1)
    " == ",
    "== ",
    " ==",
    '": ',
]
#: rev 9 (B1): the JSON literals among `_DIFF_VALUES`, and the wraps that
#: take the pair as an object member.
_DIFF_JSON_LITERALS = ["null", "true", "false", "12345", "-1.5e3"]
_DIFF_JSON_MEMBER_WRAPS = [
    "{{{}}}",
    '{{"id": 7, {}, "ok": true, "n": null}}',
    '{{"outer": {{"inner": {{{}}}, "n": 1.5}}}}',
    '{{"items": [{{"id": 1, {}}}, {{"id": 2, "name": "prod"}}]}}',
    '[{{{}}}, {{"id": 2, "tags": ["a", "b"]}}]',
]
#: rev 9 (B1): JSON scalars as structured values in the dict corpus.
_DIFF_JSON_SCALARS: list[object] = [None, True, False, 42, -1.5, 0]
#: rev 9 (B5): every character `str.isspace()` accepts except `\r` and `\n`
#: (pinned against a full enumeration in the axis test), as the whole
#: separator and after a colon.
_DIFF_SPACE_CHARS = (
    "\t\x0b\x0c\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003"
    "\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
_DIFF_SEPS += [
    sep for ch in _DIFF_SPACE_CHARS for sep in (ch, ":" + ch) if sep not in _DIFF_SEPS
]
_DIFF_WRAPS = [
    "{}",
    "{} tail",
    "prefix {}",
    "{{{}}}",
    "[{}]",
    "({})",
    '"{}"',
    "'{}'",
    '{{"a": "{}"}}',
    "<x>{}</x>",
    "-- {} --",
    "https://h.example/?{}",
    "https://h.example/?x=1&{}&y=2",
    "https://h.example/#{}",
    "https://u:p@h.example/{}",
    "curl -H '{}' https://h.example",
    "export {}",
    "log: {}",
    "{}\n{}",
    "{}; {}",
    # rev 9 (B1, B2, N2, N3): JSON documents -- mixed value types, the pair
    # as a member, nested objects, lists of objects
    '{{"id": 7, "ok": true, "n": null, "v": "{}"}}',
    '{{"id": 7, {}, "ok": true, "n": null}}',
    '{{"outer": {{"inner": {{{}}}, "n": 1.5}}}}',
    '{{"items": [{{"id": 1, {}}}, {{"id": 2, "name": "prod"}}]}}',
    '[{{{}}}, {{"id": 2, "tags": ["a", "b"]}}]',
]
_DIFF_TOKEN = re.compile(r"[A-Za-z0-9_.+/-]{3,}")


def _differential_corpus() -> list[tuple[str, str, str, str, str]]:
    """The board's differential corpus, widened in rev 9 (seed 20260923,
    8 000 inputs): keys × values × separators × wraps, one random draw per
    axis per row, the key upper- or title-cased 30% of the time, then two
    rows per key holding a JSON literal as a quoted member of a JSON wrap. The oracle
    fixture is recorded from `main` @ 860636a over exactly these rows.

    Axes (each asserted present by
    `test_the_differential_corpus_covers_every_axis`):

    * keys -- plain, snake, kebab and header names; dotted (`db.password`);
      PascalCase and camelCase (`AccessToken`, `clientSecret`); suffixed,
      credential (`password2`, `secret_value`) and descriptive (`token_type`,
      `secret_arn`); `--flag` forms;
    * separators -- `=`, `:`, `=>`, `:=`, quoted forms, a quoted key before a
      bare value, prose non-separators, CRLF and LF continuations, spaced
      `==`, and every `str.isspace()` character but `\\r`/`\\n`, alone and
      after `:`;
    * values -- credential-shaped, plain words and numbers, punctuation,
      URLs, non-ASCII, JSON literals (`null`, `true`, `false`, numbers) and
      values starting on `,` or `}`;
    * wraps -- prose, brackets, quotes, markup, URLs (query, fragment,
      userinfo), shell, repeated pairs, and JSON documents with mixed value
      types, the pair as a member, nested objects and lists of objects.
    """
    rng = random.Random(2026_09_23)
    out = []
    for _ in range(8000 - 2 * len(_DIFF_KEYS)):
        key = rng.choice(_DIFF_KEYS)
        if rng.random() < 0.3:
            key = key.upper() if rng.random() < 0.5 else key.title()
        value = rng.choice(_DIFF_VALUES)
        sep = rng.choice(_DIFF_SEPS)
        kv = f"{key}{sep.format(value)}" if "{}" in sep else f"{key}{sep}{value}"
        if sep.startswith('"'):
            kv = f'"{kv}'  # the key's opening quote
        wrap = rng.choice(_DIFF_WRAPS)
        text = wrap.format(kv, kv) if wrap.count("{}") == 2 else wrap.format(kv)
        out.append((text, key, sep, value, wrap))
    # B1's axis, which random draws almost never land in a valid document:
    # each key, quoted, as a JSON member holding a literal -- once bare after
    # the key (`"token": null`), once as a quoted string (`"token": "null"`)
    for key in _DIFF_KEYS:
        for sep in ('": ', '": "{}"'):
            value = rng.choice(_DIFF_JSON_LITERALS)
            wrap = rng.choice(_DIFF_JSON_MEMBER_WRAPS)
            kv = f'"{key}{sep.format(value)}' if "{}" in sep else f'"{key}{sep}{value}'
            out.append((wrap.format(kv), key, sep, value, wrap))
    return out


def _main_oracle() -> dict[str, Any]:
    """`main`'s recorded behaviour (auth.py/policy.py unchanged from 860636a to
    9ca081e): per grammar-corpus row what survives on each surface
    (`grammar`, `_redaction_grammar.observe` codes over `corpus(2)`, whose
    prefix is tier 1) and the corpus digest it was recorded over
    (`grammar_fingerprint`); per dict-corpus row the pieces `process_output`
    removed from the serialised result (`dict`) and the result's type
    (`dict_types`); per fuzz object the `process_output` result type
    (`fuzz_types`). Recorded by `tests/fixtures/regen_redaction_main_oracle.py`."""
    blob = (
        Path(__file__).parent / "fixtures" / "redaction_main_oracle.b64"
    ).read_text()
    return json.loads(gzip.decompress(base64.b64decode(blob)).decode("utf-8"))


#: Words main removed as collateral (its `[\s:=]+` rule ate the word after a
#: keyword, its URL rewrite dropped userinfo, its Bearer rule ate the scheme
#: word, its policy default ate the key name itself): never a secret, never
#: counted.
_DIFF_COLLATERAL = frozenset(
    {
        "bearer",
        "basic",
        "token",
        "tail",
        "prefix",
        "export",
        "curl",
        "example",
        "h.example",
        "https",
        "http",
    }
)


# === rev 10: the grammar-derived differential ============================= #
#
# The never-worse-than-main differential is derived from the ORACLE's grammar
# (main's rules plus what `json.dumps` emits), not from past findings:
# `tests/_redaction_grammar.py` holds the generator (its docstring has every
# axis and the sampling density), the observation of each surface and the
# accepted-regression classes. Tier 1 runs here; the full set (tier 2) is
# `-m slow`.


def _grammar_differential(
    rows: list[G.Row], main_codes: list[str]
) -> tuple[dict[str, int], list[str], int]:
    """Per (row, surface) this redactor is worse on than main: its accepted
    class (decided from the row as written and the surface, never from the
    output), or a bug. Also counts the positive control: rows on which main
    removed a piece on some surface."""
    policy = PolicyManager()

    def process(obj: object) -> object:
        return policy.process_output(obj, redact=True, max_bytes=G.BIG)["result"]

    counts = dict.fromkeys(G.CLASSES, 0)
    bugs: list[str] = []
    controls = 0
    for row, main in zip(rows, main_codes, strict=True):
        assert len(main) == (2 if "o" in row else len(G.TEXT_SURFACES) + 1), main
        here = G.observe(row, _engine, policy.redact_secrets, process)
        controls += bool(row["value"]) and G.removed_by_main(row, main)
        for surface in G.worse_surfaces(row, main, here):
            cls = (
                G.accepted(row["f"], surface)
                if row["f"] is not None and not surface.endswith(".type")
                else None
            )
            if cls is None:
                shown = row.get("t", row.get("o"))
                bugs.append(f"{surface}: {shown!r} main={main} here={here}")
            else:
                counts[cls] += 1
    return counts, bugs, controls


def test_grammar_differential_never_worse_than_main_except_by_stated_class() -> None:
    """Tier 1 (12 535 rows x 12 surfaces, plus the result type of every
    dict): every piece of the value main removed on a surface is removed here
    too, and every dict main kept stays a dict, unless the (row, surface) is
    in a stated class. The oracle is main's recorded output over exactly this
    corpus (the fingerprint)."""
    rows = G.corpus(1)
    oracle = _main_oracle()
    assert oracle["grammar_fingerprint"]["1"] == G.fingerprint(rows)
    assert len(rows) == 12_535 and len(oracle["grammar"]) == 166_183
    counts, bugs, controls = _grammar_differential(rows, oracle["grammar"][: len(rows)])
    assert bugs == [], f"{len(bugs)} unaccepted:\n" + "\n".join(bugs[:25])
    text_rows = sum(1 for row in rows if row["value"])
    # the oracle is not vacuous: main removes the value on most rows, and the
    # classes are exercised (each class's count is in the plan)
    assert controls > 0.85 * text_rows, (controls, text_rows)
    assert sum(counts.values()) > 10_000, counts


@pytest.mark.slow
@pytest.mark.parametrize("block", G.BLOCKS)
def test_grammar_differential_full_set(block: str) -> None:
    """Tier 2, the full set (166 183 rows), one block per case so a failure
    is attributed to an axis and each case stays well inside the per-test
    timeout."""
    rows = G.corpus(2)
    oracle = _main_oracle()
    assert oracle["grammar_fingerprint"]["2"] == G.fingerprint(rows)
    assert len(rows) == len(oracle["grammar"]) == 166_183
    picked = [(r, c) for r, c in zip(rows, oracle["grammar"]) if r["block"] == block]
    assert picked, block
    counts, bugs, controls = _grammar_differential(
        [r for r, _ in picked], [c for _, c in picked]
    )
    assert bugs == [], f"{len(bugs)} unaccepted:\n" + "\n".join(bugs[:25])
    if block != "scalar":
        assert controls > 0.5 * len(picked), (controls, len(picked))


def test_the_grammar_corpus_covers_every_axis() -> None:
    """Each axis the generator's docstring claims is really produced (a check
    is only a check if it would fail were the axis missing)."""
    spaces = {chr(i) for i in range(0x110000) if chr(i).isspace()}
    assert set(G.SPACES) == spaces and len(G.SPACES) == len(spaces) == 29
    rows = G.corpus(1)
    by_block: dict[str, list[G.Row]] = {}
    for row in rows:
        by_block.setdefault(row["block"], []).append(row)
    assert set(by_block) == set(G.BLOCKS)
    # every 1- and 2-character separator over `\s` + `:` + `=`
    seps = {row["f"]["sep"] for row in by_block["sep"]}
    assert len(G.PAIRS) == 31 + 31 * 31 and set(G.PAIRS) <= seps
    # every pre character, and every JSON escape the serialiser emits
    pres = {row["f"]["pre"] for row in by_block["pre"]}
    assert {ch for chars in G.PRE_CHARS.values() for ch in chars} <= pres
    dumped = "".join(json.dumps(row["t"]) for row in by_block["pre"])
    for escape in (
        '\\"',
        "\\\\",
        "\\b",
        "\\f",
        "\\n",
        "\\r",
        "\\t",
        "\\u0000",
        "\\u001b",
    ):
        assert escape in dumped, escape
    for escape in (
        "\\u007f",
        "\\u0085",
        "\\u00a0",
        "\\u3000",
        "\\u00e9",
        "\\ud83d\\ude42",
    ):
        assert escape in dumped, escape
    buckets = {row["bucket"] for row in rows}
    # `code` under every diagnostic and credential qualifier, both values
    code_quals = {row["f"]["qual"][:-1] for row in by_block["code"]}
    diagnostic = set(G.DIAGNOSTIC_CODE_QUALIFIERS)
    assert code_quals == diagnostic | set(G.CREDENTIAL_CODE_QUALIFIERS)
    assert not set(G.CREDENTIAL_CODE_QUALIFIERS) & diagnostic
    # every part of main's URL grammar
    url_kinds = {k for row in by_block["focus:url"] for k in row["kinds"]}
    for key_kind in ("secret", "secret-encoded", "other"):
        for value_kind in ("cred", "plain", "encoded", "empty"):
            assert f"query-{key_kind}-{value_kind}" in url_kinds, (key_kind, value_kind)
    assert {"userinfo", "fragment"} <= url_kinds
    urls = " ".join(row["t"] for row in by_block["focus:url"])
    assert (
        ":99999" in urls and "[::1]" in urls and "://:" in urls and "://user:" in urls
    )
    for kinds in (
        [f"pre:{c}" for c in G.PRE_CHARS],
        ["qual:joined", "qual:glued", "qual:glued-long", "qual:leading-joiner"],
        ["qual:multi-segment", "name:case", "name:code-qualified"],
        ["name:code-diagnostic", "name:code-credential", "wrap:none", "wrap:prose"],
        ["wrap:url-path", "wrap:url-query-pair", "wrap:url-query-value"],
        ["wrap:url-fragment", "code:diagnostic", "code:credential"],
        ["suffix:declared", "suffix:trailing-joiner", "suffix:descriptive"],
        ["suffix:random", "suffix:long"],
        ["sep:line-break", "sep:operator-run", "sep:whitespace-only", "sep:mixed"],
        ["value:main-class", "value:plain", "value:number", "value:quoted"],
        ["value:unterminated-quote", "value:bracketed", "value:json-literal"],
        ["value:punctuated", "bearer:after-:/=", "bearer:line-break"],
        ["bearer:wrapped-value", "bearer:plain-context"],
        ["authorization:line-break", "authorization:wrapped-value"],
        ["authorization:scheme", "authorization:plain", "mix"],
        [f"scalar:{name}" for name in G.SCALARS],
    ):
        assert set(kinds) <= buckets, set(kinds) - buckets
    # every scalar under every key in every shape; every case of the key word
    assert len(by_block["scalar"]) == len(G.SCALARS) * len(G.SCALAR_KEYS) * 3
    names = {row["f"]["name"] for row in by_block["focus:name"]}
    assert any(n.isupper() for n in names) and any(n.istitle() for n in names)
    assert any(not n.isupper() and not n.islower() and not n.istitle() for n in names)
    # the observation covers every surface, and each class is decidable
    code = G.observe(rows[0], _engine, _policy, lambda obj: obj)
    assert len(code) == len(G.TEXT_SURFACES) + 1 == 13
    assert set(G.SERIALISED) >= {"POd", "EjAC", "EjUI", "PjAC", "PjUI"}


def _dict_corpus() -> list[tuple[dict, str, str, str]]:
    """Structured results (seed 20260924, 800), widened in rev 9. Shapes: a
    JSON text leaf, a header leaf, a prose leaf with a URL, a top-level key,
    a nested key, a list of leaves, and (rev 9) mixed value types, a
    three-deep nested object and a list of objects. Keys and string values
    come from the string corpus's axes; a quarter of the values are JSON
    scalars (`None`, `True`, `False`, an int, a float) instead. The last
    element is the value as the text shapes spell it."""
    rng = random.Random(2026_09_24)
    out: list[tuple[dict, str, str, str]] = []
    for _ in range(800):
        key = rng.choice(_DIFF_KEYS)
        value: object = (
            rng.choice(_DIFF_JSON_SCALARS)
            if rng.random() < 0.25
            else rng.choice(_DIFF_VALUES)
        )
        spelled = value if isinstance(value, str) else json.dumps(value)
        sep = rng.choice([": ", "=", ': "{}"', " == ", ":\u3000"])
        kv = f"{key}{sep.format(spelled)}" if "{}" in sep else f"{key}{sep}{spelled}"
        shape = rng.choice(
            [
                "leaf-json",
                "leaf-header",
                "leaf-text",
                "top-key",
                "nested",
                "list",
                "mixed",
                "deep",
                "list-of-objects",
            ]
        )
        if shape == "leaf-json":
            obj: dict = {
                "content": [{"type": "text", "text": json.dumps({key: value})}],
                "isError": rng.random() < 0.5,
            }
        elif shape == "leaf-header":
            obj = {
                "content": [
                    {"type": "text", "text": f"HTTP/1.1 401\r\n{key}: {spelled}\r\n"}
                ]
            }
        elif shape == "leaf-text":
            obj = {
                "content": [
                    {
                        "type": "text",
                        "text": f"error: {kv} while calling https://h.example/?{key}={spelled}",
                    }
                ]
            }
        elif shape == "top-key":
            obj = {key: value, "note": "ok"}
        elif shape == "nested":
            obj = {"result": {"auth": {key: value}, "items": [1, 2]}}
        elif shape == "list":
            obj = {
                "content": [
                    {"type": "text", "text": spelled},
                    {"type": "text", "text": kv},
                ]
            }
        elif shape == "mixed":
            obj = {"id": 7, "ok": True, key: value, "n": None, "ratio": 0.5}
        elif shape == "deep":
            obj = {"outer": {"inner": {"leaf": {key: value}}, "n": 1.5}}
        else:
            obj = {
                "items": [
                    {"id": 1, key: value},
                    {"id": 2, "name": "prod", "tags": ["a", "b"]},
                ]
            }
        out.append((obj, key, sep, spelled))
    return out


#: The key words a `_DIFF_KEYS` key is made of, for the structured
#: differential's classifier (which reads a row as `qual + name + suffix`).
_KEY_WORDS = sorted(
    {*AUTH_DIAGNOSTIC_SECRET_KEYS, "api_key", "api-key", "apikey", "private_key"}
    | {"authorization", "bearer"}
)


def _grammar_fields(key: str, sep: str, value: str) -> dict[str, str]:
    """A structured-corpus row as the grammar classifier's fields: the key
    word is the one that ends last in the key (the longest on a tie)."""
    low = key.lower()
    end, length, start = max(
        (low.rfind(w) + len(w), len(w), low.rfind(w)) for w in _KEY_WORDS if w in low
    )
    before, _, after = sep.partition("{}")
    quote = before[-1:] if before[-1:] in "\"'" else ""
    return {
        "pre": "",
        "qual": key[:start],
        "name": key[start:end],
        "suffix": key[end:],
        "sep": before[: len(before) - len(quote)],
        "value": quote + value + after if quote else value,
    }


def test_differential_on_structured_results_never_worse_than_main() -> None:
    """A dict result goes through the CORE path (serialise, then redact the
    window); JSON text inside a leaf is Consiliency/pmcp#290's problem. The
    bar here is main's: every piece main's `process_output` removed from the
    serialised result is removed here too, or the row is in an accepted class
    (the same classes as the string differential). 800 structured results;
    main removes something on 172 of them.
    """
    policy = PolicyManager()
    oracle = _main_oracle()["dict"]
    corpus = _dict_corpus()
    assert len(corpus) == len(oracle) == 800
    bugs: list[str] = []
    accepted: dict[str, int] = {}
    main_types = _main_oracle()["dict_types"]
    assert len(main_types) == 800 and main_types.count("dict") == 786
    for (obj, key, sep, value), main_removed, main_type in zip(
        corpus, oracle, main_types
    ):
        dumped = json.dumps(obj, indent=2)
        result = policy.process_output(obj, redact=True)["result"]
        # A dict result stays a dict wherever main's did: gateway.invoke and
        # tasks_result put this on the wire, and a redaction that breaks the
        # serialised JSON silently turns the result into a string.
        if main_type == "dict" and not isinstance(result, dict):
            bugs.append(f"{obj!r} came back as {type(result).__name__}: {result!r}")
        out = result if isinstance(result, str) else json.dumps(result, indent=2)
        removed_here = set(_DIFF_TOKEN.findall(dumped)) - set(_DIFF_TOKEN.findall(out))
        kept = [
            p
            for p in main_removed
            if len(p) >= 4
            and p.lower() not in _DIFF_COLLATERAL
            and p not in removed_here
        ]
        if kept:
            reason = G.accepted(_grammar_fields(key, sep, value), "POd")
            if reason is None:
                bugs.append(f"{obj!r} keeps {kept}; here: {out!r}")
            else:
                accepted[reason] = accepted.get(reason, 0) + 1
    assert bugs == [], "\n".join(bugs[:10])
    # the named case: main catches the Bearer value in a dumped leaf; so does this
    result = policy.process_output(
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"password": "hunter2", "Authorization": "Bearer hunter2tok"}
                    ),
                }
            ]
        },
        redact=True,
    )["result"]
    assert "hunter2tok" not in json.dumps(result)
    assert result["content"][0]["text"].endswith(
        '"Authorization": "Bearer [REDACTED]"}'
    )  # JSON intact
    assert PolicyManager().process_output({"text": "Bearer test-token"}, redact=True)[
        "result"
    ] == {"text": "Bearer [REDACTED]"}


_DIFF_FUZZ_KEYS = [
    "password",
    "token",
    "api_key",
    "secret",
    "client_secret",
    "authorization",
    "code",
    "auth",
    "credentials",
    "Authorization",
    "pwd",
    "session",
    "cookie",
    "private_key",
    "msg",
    "text",
    "note",
]
_DIFF_FUZZ_PIECES = [
    *"abcXYZ0129 _-/+=.:;,&!@#$%^*()[]{}<>|~`'\"\\\n\t",
    "é",
    "\u3000",
    "\xa0",
    "password=",
    "token: ",
    "Bearer ",
    "https://h/?token=x&",
    '"password": "',
    "[",
    "]",
]


def _json_fuzz_corpus() -> list[dict]:
    """The claude seat's random-JSON fuzz (seed 7), committed: 1 500 random
    objects whose keys are credential and neutral names and whose values are
    random strings of delimiters, quotes, backslashes, whitespace (U+3000,
    NBSP) and redactor trigger words, nested lists and objects up to three
    deep, and JSON scalars."""
    rng = random.Random(7)

    def value(depth: int = 0) -> object:
        roll = rng.random()
        if roll < 0.6 or depth > 2:
            return "".join(
                rng.choice(_DIFF_FUZZ_PIECES) for _ in range(rng.randint(0, 14))
            )
        if roll < 0.8:
            return [value(depth + 1) for _ in range(rng.randint(0, 3))]
        if roll < 0.9:
            return {
                rng.choice(_DIFF_FUZZ_KEYS): value(depth + 1)
                for _ in range(rng.randint(1, 3))
            }
        return rng.choice([123, -32601, True, None, 1.5])

    return [
        {rng.choice(_DIFF_FUZZ_KEYS): value() for _ in range(rng.randint(1, 4))}
        for _ in range(1500)
    ]


def test_redaction_never_breaks_a_json_document() -> None:
    """Wherever the input parses as JSON, the output does too, on both surfaces.
    Main breaks 8 of the widened corpus's 810 JSON rows (it ate a closing
    quote); this redactor breaks none. A keyed value in quotes is redacted INSIDE the quotes."""
    policy = PolicyManager()
    checked = 0
    for text, *_ in _differential_corpus():
        try:
            json.loads(text)
        except ValueError:
            continue
        checked += 1
        for surface, out in (
            ("engine", _engine(text)),
            ("policy", policy.redact_secrets(text)),
        ):
            try:
                json.loads(out)
            except ValueError:
                pytest.fail(f"{surface} broke JSON: {text!r} -> {out!r}")
    assert checked == 810, checked


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ({"password": "hunter2"}, {"password": REDACTED}),
        ({"password": ["hunter2", "s3cr3t99"]}, {"password": [REDACTED, REDACTED]}),
        (
            {"result": {"auth": {"api_key": "abc123def456"}}},
            {"result": {"auth": {"api_key": REDACTED}}},
        ),
        ({"password": ""}, {"password": ""}),
        ({"code": ["red", "x9Kq2mZ7"]}, {"code": ["red", REDACTED]}),
    ],
)
def test_structured_result_round_trips_as_a_dict(obj: dict, expected: dict) -> None:
    """The headline case, on the structured surface: redacted, still a dict."""
    assert PolicyManager().process_output(obj, redact=True)["result"] == expected


def test_pretty_printed_list_value_is_redacted_inside_its_quotes() -> None:
    """`"password": [\n  "hunter2"\n]` -- a list under a secret key. Rev 7 ate
    the `[`; main and the rev-8 spike left the value. Each quoted element is
    redacted in place and the brackets stay."""
    text = '{\n  "password": [\n    "hunter2"\n  ]\n}'
    for out in (_engine(text), _policy(text)):
        assert "hunter2" not in out
        assert json.loads(out) == {"password": [REDACTED]}


def test_operator_pattern_applies_to_a_percent_encoded_query_value(
    tmp_path: Path,
) -> None:
    """Item 4 of rev 8: an operator's own pattern, written for the decoded
    shape, still redacts the value when it arrives percent-encoded in a URL
    query (main decoded before matching)."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("redaction:\n  patterns:\n    - 'acme-[0-9]{6}'\n")
    policy = PolicyManager(policy_path=policy_file)
    plain = policy.redact_secrets("id acme-123456 end")
    assert "123456" not in plain  # the pattern is live on the plain surface
    out = policy.redact_secrets("https://h.example/?q=%61%63%6D%65%2D123456")
    assert "123456" not in out and "%61%63%6D%65" not in out, out


def test_url_decode_depth_bound_fails_closed() -> None:
    """Item 3 of rev 8: a value that decodes past the depth bound raises no
    RecursionError and is redacted whole rather than passed through."""
    text = "https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"
    for out in (_engine(text), _policy(text)):
        assert "%25%25" not in out
        assert REDACTED in out


def test_crlf_and_no_break_space_are_whitespace_too() -> None:
    """HTTP header folding and Windows dumps use CRLF; `\\s` on main matched
    `\\xa0`. Rev 7 narrowed continuations to LF with an indent gate (grok):
    each of these leaked while main redacted it.
    """
    for text, expected in [
        ("password:\r\nhunter2", "password:\r\n[REDACTED]"),
        ("password:\r\n  hunter2", "password:\r\n  [REDACTED]"),
        ("token\r\nabc123def456", "token\r\n[REDACTED]"),
        ("Authorization:\r\n  s3cr3tvalue", "Authorization:\r\n  [REDACTED]"),
        ("password:\xa0hunter2", "password:\xa0[REDACTED]"),
        (
            "Missing bearer token:\r\nthe bearer of",
            "Missing bearer token:\r\nthe bearer of",
        ),
        ("token:\nthe bearer of", "token:\nthe bearer of"),
    ]:
        assert _engine(text) == expected, text
        if "\xa0" in text:
            # the policy default now reads `\xa0` as whitespace too (like
            # main's `\s`) and replaces it with the value: same secret
            # removed, one fewer separator character
            assert "hunter2" not in _policy(text), _policy(text)
        else:
            assert _policy(text) == expected, text


def test_encoded_query_values_are_decoded_a_bounded_number_of_times() -> None:
    """Decoding a query value and asking the engine about it can meet another
    encoded URL; unbounded, a downstream-controlled diagnostic raised
    `RecursionError` (grok and codex, independently). Past three levels the
    value is treated as covered and redacted -- failing closed.
    """
    codex = "https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"
    assert len(codex) < 10_000
    assert _engine(codex) == "https://h.example/?q=[REDACTED]"
    assert _policy(codex) == "https://h.example/?q=[REDACTED]"
    grok = "https://h.example/?q=" + "%25" * 400 + "67%68%70%5F" + TOKEN[4:]
    assert _engine(grok) == "https://h.example/?q=[REDACTED]"
    assert (
        _engine("https://h.example/?q=%20hello%20world")
        == "https://h.example/?q=%20hello%20world"
    )


def test_encoded_query_values_are_matched_by_the_policy_patterns_too() -> None:
    """`%67%68%70%5Fabcdefghijklmnop` decodes to the C-13 probe the engine
    ignores on purpose; the policy's `ghp_` default must still catch it, as
    main did (codex): the operator's and default patterns are asked about
    the decoded value and a hit redacts the encoded value whole.
    """
    text = "https://h.example/?q=%67%68%70%5Fabcdefghijklmnop"
    assert _engine(text) == text
    assert _policy(text) == "https://h.example/?q=[REDACTED]"
    manager = PolicyManager()
    manager._redaction_regexes = [re.compile(r"mytoken-[a-z]+")]
    assert (
        manager.redact_secrets("https://h.example/?q=mytoken%2Ddeadbeef")
        == "https://h.example/?q=[REDACTED]"
    )


def test_a_comparison_survives_on_both_surfaces() -> None:
    """`if token == expected:` is untouched by the engine AND by the policy
    surface (whose default `token` pattern now carries the same separator
    grammar; rev 7 pinned only the engine -- grok)."""
    assert _engine("if token == expected:") == "if token == expected:"
    assert _policy("if token == expected:") == "if token == expected:"
    assert _policy("password==hunter2") == "password=[REDACTED]"
    assert _policy("password:=hunter2") == "password:[REDACTED]"


# === rev 9: the board's second round and the differential it widened ====== #
#
# One test per finding class, each red on rev 8 (`56d7f80`) and green here;
# where a finding lives on both surfaces, both are asserted. Where main's exact
# text differs from ours only by a separator character (the policy default
# re-spells `:　` as `:`), the assertion is `secret not in out`.


def _both(text: str) -> list[tuple[str, str]]:
    return [("engine", _engine(text)), ("policy", _policy(text))]


def _process(obj: dict) -> object:
    return PolicyManager().process_output(obj, redact=True)["result"]


def test_the_differential_corpus_covers_every_axis() -> None:
    """The widened generator really produces each axis it claims (a check is
    only a check if it would fail were the axis missing)."""
    corpus = _differential_corpus()
    keys = {key.lower() for _, key, *_ in corpus}
    seps = {sep for _, _, sep, _, _ in corpus}
    values = {value for *_, value, _ in corpus}
    wraps = {wrap for *_, wrap in corpus}
    assert {k.lower() for k in _DIFF_KEYS} <= keys
    assert set(_DIFF_SEPS) <= seps and set(_DIFF_VALUES) <= values
    assert set(_DIFF_WRAPS) <= wraps
    for axis in (
        "db.password",
        "accesstoken",
        "clientsecret",
        "password_confirmation",
        "--clientsecret",
    ):
        assert axis in keys, axis
    # every `str.isspace()` character but CR and LF, alone and after `:`
    spaces = {chr(i) for i in range(0x110000) if chr(i).isspace()} - {"\r", "\n"}
    assert set(_DIFF_SPACE_CHARS) == spaces
    assert {c for c in spaces} <= seps and {":" + c for c in spaces} <= seps
    assert {" == ", "== ", " ==", '": '} <= seps
    assert {"null", "true", "false", "-1.5e3", ",hunter22", "}hunter22"} <= values
    # JSON literals really sit after a quoted key in a valid document, and
    # the JSON wraps really produce nested objects and lists of objects
    documents = []
    for text, *_ in corpus:
        try:
            documents.append(json.loads(text))
        except ValueError:
            continue
    corpus_keys = {k.lower() for k in _DIFF_KEYS}

    def literal_members(node: object) -> int:
        if isinstance(node, list):
            return sum(literal_members(item) for item in node)
        if not isinstance(node, dict):
            return 0
        return sum(
            (
                k.lower() in corpus_keys
                and (v is None or isinstance(v, (bool, int, float)))
            )
            + literal_members(v)
            for k, v in node.items()
        )

    literal_after_quoted_key = [doc for doc in documents if literal_members(doc)]
    assert len(literal_after_quoted_key) >= len(_DIFF_KEYS)
    assert any(isinstance(d, dict) and "items" in d for d in documents)
    assert any(isinstance(d, dict) and "outer" in d for d in documents)
    assert any(isinstance(d, list) and isinstance(d[0], dict) for d in documents)
    shapes = [obj for obj, *_ in _dict_corpus()]
    assert any(v is None for obj in shapes if "note" in obj for v in obj.values())
    assert any("items" in obj for obj in shapes) and any(
        "outer" in obj for obj in shapes
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"access_token": null}', '{"access_token": null}'),
        ('{"token": true}', '{"token": true}'),
        ('{"password": false}', '{"password": false}'),
        # rev 10 (F5/F6, the maintainer's decision 1): a bare number after
        # a quoted key stays unchanged, as on main; numeric secrets under a
        # quoted key are Consiliency/pmcp#290's scope
        ('{"token": 42}', '{"token": 42}'),
        ('{"token": -1.5e3}', '{"token": -1.5e3}'),
    ],
)
def test_b1_a_json_literal_after_a_quoted_key_keeps_the_document(
    text: str, expected: str
) -> None:
    """B1: a bare JSON scalar after a quoted key is not a secret; the
    document stays JSON and a dict result stays a dict."""
    for surface, out in _both(text):
        assert out == expected, (surface, out)
    assert _process(json.loads(text)) == json.loads(expected)


@pytest.mark.parametrize(
    "obj", [{"Authorization": {"a": "b"}}, {"Authorization": ["9"]}]
)
def test_b2_authorization_never_starts_on_a_bracket(obj: dict) -> None:
    """B2: an object or list under `Authorization` is structure, not a value."""
    text = json.dumps(obj)
    for surface, out in _both(text):
        assert out == text, (surface, out)
    assert _process(obj) == obj


@pytest.mark.parametrize(
    "key",
    [
        "db.password",
        "self.password",
        "spring.datasource.password",
        # B4: PascalCase
        "AccessToken",
        "ClientSecret",
        "SessionToken",
        "DbPassword",
    ],
)
@pytest.mark.parametrize("sep", ["=", " = ", ": "])
def test_b3_b4_dotted_and_pascal_case_keys(key: str, sep: str) -> None:
    text = f"{key}{sep}hunter22"
    for surface, out in _both(text):
        assert "hunter22" not in out and out.startswith(key), (surface, out)


@pytest.mark.parametrize(
    "space",
    [c for c in _DIFF_SPACE_CHARS if c not in "\n\r"],
    ids=lambda c: f"U+{ord(c):04X}",
)
def test_b5_every_whitespace_character_separates(space: str) -> None:
    """B5: every `str.isspace()` character but CR/LF, after `:` and alone."""
    for text in (f"password:{space}hunter22", f"token{space}abc123def456"):
        for surface, out in _both(text):
            assert "hunter22" not in out and "abc123def456" not in out, (
                surface,
                out,
            )


@pytest.mark.parametrize(
    "text",
    [
        "password2=hunter22",
        "password_confirmation=hunter22",
        "passwordHash=hunter22",
        "secret_value=hunter22",
        "SECRET_KEY abc123def456",
        "--clientSecret abc123def456",
        "--sessionToken abc123def456",
        "--token_id abc123def456",
        # the whitespace form of a suffixed key (defect B of round 3)
        "password2 abc123def456",
        "passwordHash hunter2",
        "passwordHash\tabc123def456",
        "secret_value\tabc123def456",
    ],
)
def test_n1_suffixed_keys_redact_a_credential_shaped_value(text: str) -> None:
    secret = re.split(r"[=\s]", text)[-1]
    for surface, out in _both(text):
        assert secret not in out, (surface, out)


@pytest.mark.parametrize(
    "text",
    [
        "token_type=bearer",
        "password_length=12",
        "token_endpoint=https://h.example/oauth/token",
        "secret_arn=arn:aws:secretsmanager:us-east-1:1:secret:x",
        "token_endpoint https://h.example/oauth/token",
        "passwords expired now",
    ],
)
def test_n1_suffixed_keys_keep_metadata(text: str) -> None:
    """Guards (already green on rev 8): a suffix naming metadata keeps its
    non-credential value."""
    for surface, out in _both(text):
        assert out == text, (surface, out)


def test_n2_a_list_of_objects_is_not_a_flat_value() -> None:
    obj = {"secrets": [{"id": "db", "name": "prod"}]}
    for surface, out in _both(json.dumps(obj)):
        assert out == json.dumps(obj), (surface, out)
    assert _process(obj) == obj


def test_n3_a_quote_opening_on_structure_is_not_a_value() -> None:
    """N3: after an UNQUOTED key (`token: ` at the end of a string), a quote
    whose content starts on `,:]}` closes that string. After a QUOTED key the
    quote always opens the value."""
    obj = {"msg": "missing token: ", "code": 401}
    for surface, out in _both(json.dumps(obj)):
        assert out == json.dumps(obj), (surface, out)
    assert _process(obj) == obj
    for surface, out in _both('{"password": ", secret"}'):
        assert json.loads(out) == {"password": REDACTED}, (surface, out)


def test_n4_the_key_qualifier_is_bounded() -> None:
    """N4: rev 8 took ~216 s on this; main and rev 9 take well under 1 s.
    The 2 s bound is generous against CI noise and still catches the quadratic
    shape by two orders of magnitude."""
    import time

    text = "a_" * 33000
    for surface, redact in (("engine", _engine), ("policy", _policy)):
        start = time.perf_counter()
        redact(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, (surface, elapsed)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password == hunter2", "hunter2"),
        ("password== hunter2", "hunter2"),
        ("if token == hunter2:", "hunter2"),
    ],
)
def test_g1_a_spaced_double_equals_before_a_secret(text: str, secret: str) -> None:
    for surface, out in _both(text):
        assert secret not in out, (surface, out)
    for surface, out in _both("if token == expected:"):
        assert out == "if token == expected:", (surface, out)


def test_c2_a_percent_encoded_key_is_still_the_key() -> None:
    for surface, out in _both("https://h.example/?api%2Dkey=hunter2"):
        assert "hunter2" not in out, (surface, out)


def test_c4_bearer_across_a_crlf() -> None:
    for surface, out in _both("Bearer\r\nhunter2"):
        assert out == "Bearer\r\n[REDACTED]", (surface, out)
    prose = "--rotated bearer\n--ingredient assertion"
    for surface, out in _both(prose):
        assert out == prose, (surface, out)


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ({"text": 'token hunter2value" hi'}, {"text": 'token [REDACTED]" hi'}),
        (
            {"text": 'Authorization: hunter2value" hi'},
            {"text": 'Authorization: [REDACTED]" hi'},
        ),
    ],
)
def test_c6_a_quote_inside_a_leaf_keeps_the_dict(obj: dict, expected: dict) -> None:
    assert _process(obj) == expected


def test_c7_a_bracket_inside_a_quoted_list_element() -> None:
    """C7: `"first]"` does not end the list (this leaked on main too); a list
    under a non-secret key is untouched."""
    text = '{"password": ["first]", "hunter2"]}'
    for surface, out in _both(text):
        assert json.loads(out) == {"password": [REDACTED, REDACTED]}, (surface, out)
    assert _process(json.loads(text)) == {"password": [REDACTED, REDACTED]}
    kept = '{"error_codes": ["AADSTS50011"]}'
    for surface, out in _both(kept):
        assert out == kept, (surface, out)


@pytest.mark.parametrize(
    "text",
    [
        'password="hunter2\\\\"',
        "password=abc123\\",
        "token='abc123def456'",
        'api_key="abc123def456"',
        'aws_secret="wJalrXUtnFEMI\\\\"',
    ],
)
def test_policy_defaults_never_start_on_a_quote_or_end_on_a_backslash(
    text: str,
) -> None:
    """The operator-visible default patterns: the captured value never starts
    on a quote (rev 8 ate the opening one) and never ends on a backslash (the
    escape of a JSON quote); the engine redacts quoted values INSIDE them."""
    for pattern in DEFAULT_REDACTION_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if match.lastindex is None:
                continue
            value = match.group(match.lastindex)
            assert not value.startswith(('"', "'")), (pattern, value)
            assert not value.endswith("\\"), (pattern, value)
    out = _policy(text)
    quote = text[text.index("=") + 1]
    if quote in "\"'":
        assert out.startswith(text[: text.index("=") + 2] + REDACTED), out


@pytest.mark.parametrize(
    "obj",
    [
        {"a": "token: [x", "b": "]"},
        {"a": "password: [x", "b": "y]"},
    ],
)
def test_a_keyed_list_never_straddles_a_string_boundary(obj: dict) -> None:
    """After an unquoted key inside a leaf, `[` opens a list only if a literal
    array follows (quoted strings or JSON scalars): `[x", "b": "]` is two
    string values, not one list."""
    text = json.dumps(obj)
    for surface, out in _both(text):
        assert set(json.loads(out)) == set(obj), (surface, out)
        assert json.loads(out)["b"] == obj["b"], (surface, out)


@pytest.mark.parametrize(
    "obj",
    [
        {"a": "password='x", "b": "y'"},
        {"a": "secret='q", "b": 1, "c": "z'"},
    ],
)
def test_a_quoted_value_never_straddles_via_the_other_quote(obj: dict) -> None:
    """After an unquoted key, a single-quoted "value" that holds an unescaped
    double quote runs across a JSON string boundary; it is not a value."""
    text = json.dumps(obj)
    for surface, out in _both(text):
        assert json.loads(out) == obj, (surface, out)


@pytest.mark.parametrize(
    "space", ["\x0b", "\x85", "\xa0", "\u2003", "\u2028", "\u3000"]
)
@pytest.mark.parametrize("key", ["token", "bearer:", "password", "SECRET_KEY"])
def test_no_span_starts_inside_a_json_escape(space: str, key: str) -> None:
    """Defect A of round 3: `json.dumps` spells non-ASCII whitespace
    `\\uXXXX`; a span starting at the `u` left a lone backslash, so the
    document broke and a dict result came back as a string."""
    obj = {"text": f"{key}{space}abc123def456"}
    text = json.dumps(obj)
    for surface, out in _both(text):
        assert "abc123def456" not in json.loads(out)["text"], (surface, out)
    result = _process(obj)
    assert isinstance(result, dict) and "abc123def456" not in result["text"]


@pytest.mark.parametrize(
    "key",
    [
        "CLIENTSECRET",
        "Clientsecret",
        "clientsecret",
        "DBPASSWORD",
        "dbpassword",
        "ACCESSTOKEN",
        "accesstoken",
        "MYPASSWORD",
    ],
)
@pytest.mark.parametrize("sep", ["=", ": ", "\t"])
def test_a_case_folded_compound_key_is_still_a_key(key: str, sep: str) -> None:
    """Defect C of round 3: with its case boundary folded away, a compound
    key still names a secret when the value is credential-shaped."""
    for surface, out in _both(f"{key}{sep}abc123def456"):
        assert "abc123def456" not in out, (surface, out)


def test_a_mixed_case_identifier_is_not_a_glued_key() -> None:
    for text in ("Ed25519PrivateKey X509Cert", "errorcode=invalid_grant"):
        for surface, out in _both(text):
            assert out == text, (surface, out)


@pytest.mark.parametrize(
    "text",
    [
        "secret -1.5e3",
        "password2\xa0-1.5e3",
        "refresh_token:\r\n  -1.5e3",
        "token -abc123def",
        "password\n  -hunter22",
        "password:\n  -hunter22",
        "Bearer\n-abc123def",
    ],
)
def test_a_dash_glued_to_a_value_is_part_of_it(text: str) -> None:
    """A base64url secret can start with `-`; only a `--` flag or a lone
    marker followed by whitespace is a flag or a bullet (as on main)."""
    secret = text.split()[-1]
    for surface, out in _both(text):
        assert secret not in out, (surface, out)


@pytest.mark.parametrize(
    "text",
    [
        "secret --bucket",
        "token:\n  - item",
        "token:\n- item",
        "password:\n  * item",
        "--rotated bearer\n--ingredient assertion",
        "Bearer\n--flag12345",
    ],
)
def test_a_flag_or_a_bullet_after_a_keyword_is_not_its_value(text: str) -> None:
    for surface, out in _both(text):
        assert out == text, (surface, out)


def _has_escaped_quote(text: str) -> bool:
    """A backslash-escaped quote in the INPUT: JSON inside a leaf, which is
    Consiliency/pmcp#290's scope."""
    return '\\"' in text


def test_property_json_fuzz_keeps_documents_and_dicts() -> None:
    """The claude seat's JSON fuzz, committed (seed 7, 1 500 objects, each
    serialised four ways -- compact and indented, ASCII-escaped and not --
    so 6 000 documents per surface). Two invariants:

    * a document with no backslash-escaped quote (`\\"`, decided from the
      INPUT; Consiliency/pmcp#290's scope) still parses after redaction on both surfaces --
      main breaks ~1 000 of them per surface;
    * `process_output(obj)` returns a dict wherever main's did (main's type
      per object is recorded in the oracle fixture, not computed here).
    """
    policy = PolicyManager()
    objects = _json_fuzz_corpus()
    main_types = _main_oracle()["fuzz_types"]
    assert len(objects) == len(main_types) == 1500
    checked = excluded = 0
    broken: list[str] = []
    for obj, main_type in zip(objects, main_types):
        for indent in (None, 2):
            for ascii_only in (True, False):
                text = json.dumps(obj, indent=indent, ensure_ascii=ascii_only)
                if _has_escaped_quote(text):
                    excluded += 1
                    continue
                checked += 1
                for surface, out in (
                    ("engine", _engine(text)),
                    ("policy", policy.redact_secrets(text)),
                ):
                    try:
                        json.loads(out)
                    except ValueError:
                        broken.append(f"{surface}: {text!r} -> {out!r}")
        result = policy.process_output(obj, redact=True)["result"]
        if main_type == "dict" and not isinstance(result, dict):
            broken.append(f"process_output: {obj!r} -> {result!r}")
    assert broken == [], "\n".join(broken[:10])
    assert checked > 3000 and excluded > 0, (checked, excluded)
    assert main_types.count("dict") > 1000


# === rev 10: regression tests, one per fix family ========================== #
#
# Each is red on rev 9 (`60ca193`) and green here; each fix is also pinned by a
# mutant of its production change that these tests kill (the plan lists them).


def _leaf(text: str) -> str:
    """`process_output({"t": text})`'s leaf, which must still be a dict."""
    result = _process({"t": text})
    assert isinstance(result, dict), result
    return str(result["t"])


def _gone(text: str, secret: str) -> None:
    for surface, out in _both(text):
        assert secret not in out, (surface, out)


@pytest.mark.parametrize(
    "obj",
    [
        {"token": float("nan")},
        {"secret": float("inf")},
        {"session": float("-inf")},
        {"password": float("nan"), "x": 1},
        {"stats": {"tokens": float("nan")}},
        {"usage": {"input_tokens": 12, "output_tokens": 34}, "max_tokens": 1024},
        {"sessions": 3, "secrets": 0, "Authorization": 7},
    ],
)
def test_f5_f6_a_bare_json_scalar_after_a_quoted_key_is_unchanged(obj: dict) -> None:
    """F5/F6 (the maintainer's decision 1): every scalar `json.dumps` emits,
    `NaN` and `+-Infinity` included, stays as it is after a quoted key, as on
    main: the result stays a dict and no leaf changes type. Numeric secrets
    under a quoted key are Consiliency/pmcp#290's."""
    result = _process(obj)
    assert isinstance(result, dict), result
    assert json.dumps(result, sort_keys=True) == json.dumps(obj, sort_keys=True)
    for text in ('{"token": NaN}', '{"token": -Infinity}', '{"Authorization": 1e+20}'):
        for surface, out in _both(text):
            assert out == text, (surface, out)


@pytest.mark.parametrize(
    "pre",
    ["\xa0", "\x08", "\x0c", "\x0b", "\x1b", "\x7f", "\x85", "\u3000", "\u2028"]
    + ["\xe9", "\u2022", "\u201c", "\u5bc6", "\U0001f642", "\\", '"'],
    ids=lambda c: f"U+{ord(c):04X}",
)
@pytest.mark.parametrize(
    ("body", "secret"),
    [
        ("password=hunter22x", "hunter22x"),
        ("Token hunter2tok", "hunter2tok"),
        ("Secret abc123def456", "abc123def456"),
        ("Bearer hunter2tok", "hunter2tok"),
    ],
)
def test_f3_a_json_escape_before_a_key_is_a_boundary(
    pre: str, body: str, secret: str
) -> None:
    """F3: `process_output` serialises a dict leaf with `ensure_ascii=True`,
    so every control, non-ASCII and escaped whitespace character before a key
    arrives as an escape whose tail is alphanumeric (`\\u00a0password`,
    `\\bToken`); main's `\\b[A-Za-z0-9_-]*` swallowed the tail and redacted."""
    assert secret not in _leaf(pre + body)
    for surface, out in _both(json.dumps({"t": pre + body})):
        assert secret not in out, (surface, out)


@pytest.mark.parametrize(
    "text",
    [
        '{"t": "\\u00a0password=hunter22x"}',
        '{"t": "\\bsecret: hunter22x"}',
        '{"t": "\\u2022token=hunter22x"}',
        '{"t": "\\ftoken: hunter22x"}',
    ],
)
def test_f3_the_policy_defaults_read_a_json_escape_as_a_boundary(text: str) -> None:
    """F3 on the operator-visible defaults themselves (the engine covers the
    policy surface too, so only the patterns can show it): `\\b` made a key
    start right after an escape's alphanumeric tail on main."""
    hits = [
        m.group(m.lastindex)
        for pattern in DEFAULT_REDACTION_PATTERNS
        for m in re.finditer(pattern, text, re.IGNORECASE)
        if m.lastindex
    ]
    assert any(h.startswith("hunter22x") for h in hits), hits


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Cookie: session=bearer \u2006(t8B7fzJ0L)", "t8B7fzJ0L"),
        ("token: BEARER \r{hM6odfJdqx}", "hM6odfJdqx"),
        ("Authorization:\u2006'gn1y2tpo'", "gn1y2tpo"),
        ("AUTHORIZATION:\u2006Bearer\n\n'ay47NjWsmUf'", "ay47NjWsmUf"),
        ("authorization=\u2029bearer\t'gS6CJ7XD'", "gS6CJ7XD"),
    ],
)
def test_f3_an_escaped_space_after_bearer_or_authorization_separates(
    text: str, secret: str
) -> None:
    """F3's follow-up: in a serialised leaf the whitespace between
    `Bearer`/`Authorization:` and the value is an escape (`\\u2006`, `\\r`,
    `\\t`); main's `\\s+[^\\s,;]+` took it as part of the value and
    redacted the token with it."""
    assert secret not in _leaf(text)
    for surface, out in _both(json.dumps({"t": text})):
        assert secret not in out, (surface, out)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password:\n\nhunter22x", "hunter22x"),
        ("Temporary password:\n\n    hunter22x", "hunter22x"),
        ("password:\r\n\r\nhunter22x", "hunter22x"),
        ("password:\rhunter22x", "hunter22x"),
        ("password:\n\rhunter22x", "hunter22x"),
        ("Secret:\r\rhunter22x", "hunter22x"),
        ("token\n\nhunter22tok", "hunter22tok"),
        ("token\r\r\nhunter22tok", "hunter22tok"),
        ("token:\n\n  abc123def456", "abc123def456"),
        ("Authorization:\n\nhunter22x", "hunter22x"),
        # F4b: the break BEFORE the operator
        ("password\n: hunter22x", "hunter22x"),
        ("Authorization\r\n: hunter22x", "hunter22x"),
        ("api_key\n=\nhunter22x", "hunter22x"),
    ],
)
def test_f4_any_line_break_run_separates(text: str, secret: str) -> None:
    """F4: a blank line, a bare CR, `\\n\\r` or `\\r\\r\\n`, on either side of
    the operator: main's `[\\s:=]+` accepted any run. The unindented-break gate
    still keeps prose (`token:\\n\\nThe next paragraph`, the FP table)."""
    _gone(text, secret)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("X-Auth: Bearer hunter22x", "hunter22x"),
        ("X-Auth:Bearer hunter22x", "hunter22x"),
        ("auth: bearer\thunter2tok", "hunter2tok"),
        ("token: Bearer abc123def456", "abc123def456"),
        ("X-Access-Token: Bearer hunter2tok", "hunter2tok"),
        ("session=Bearer hunter2tok", "hunter2tok"),
        ("Cookie: session=Bearer hunter2tok", "hunter2tok"),
        ("headers: {X-Auth: Bearer hunter2tok}", "hunter2tok"),
    ],
)
def test_f2_bearer_after_a_key_and_separator(text: str, secret: str) -> None:
    """F2: a header or field whose value is `Bearer <token>`. The lookahead
    and the plain-word gate already keep `token_type=Bearer expires_in=3600`
    (the FP table)."""
    _gone(text, secret)
    assert secret not in _leaf(text)


def test_f9_the_pem_rule_is_linear() -> None:
    """F9: every BEGIN without an END scanned to the end of the text (rev 9:
    5.6 s on 264 KB; main 0.05 s). The bound is generous against CI noise and
    still an order of magnitude under rev 9."""
    import time

    text = "-----BEGIN RSA PRIVATE KEY-----\n" * 8250  # 264 KB
    for surface, redact in (("engine", _engine), ("policy", _policy)):
        start = time.perf_counter()
        redact(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, (surface, elapsed)
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----"
    assert _engine(f"key: {block} end") == "key: [REDACTED] end"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Database:Password=hunter22x", "hunter22x"),
        ("ConnectionStrings:Password=hunter22x", "hunter22x"),
        ("--Database:Password=hunter22x", "hunter22x"),
        ("App:ClientSecret=hunter22x", "hunter22x"),
        ("vault:secret=hunter22x", "hunter22x"),
        ("env:SECRET=hunter22x", "hunter22x"),
        ("mongodb:password: hunter22x", "hunter22x"),
    ],
)
def test_f1_a_colon_qualified_key_is_a_key(text: str, secret: str) -> None:
    """F1: `Section:Key` (.NET configuration, namespaced log fields). Not
    `::` (a path) and not a key inside an `arn:`/`urn:` name (the FP table)."""
    _gone(text, secret)
    assert secret not in _leaf(text)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password===hunter22x", "hunter22x"),
        ("password====hunter22x", "hunter22x"),
        ("password:: hunter22x", "hunter22x"),
        ("password=:\u205fhunter22x", "hunter22x"),
        ("Database:Password=:hunter22x", "hunter22x"),
        ("token:==abc123def456", "abc123def456"),
    ],
)
def test_n1_an_operator_run_separates(text: str, secret: str) -> None:
    """N1: main's `[\\s:=]+` took any run; a run holding `==` or `::` is a
    comparison or a path unless the value is credential-shaped
    (`if (token === expected)`, `token::Type`: the FP table)."""
    _gone(text, secret)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password_=hunter22x", "hunter22x"),
        ("password-=hunter22x", "hunter22x"),
        ("password__=hunter22x", "hunter22x"),
        ("id_token__Xv=hunter22x", "hunter22x"),
        ("api_keyNf--sKOs: hunter22x", "hunter22x"),
        ("password__ abc123def456", "abc123def456"),
    ],
)
def test_n2_a_trailing_joiner_or_joiner_run_in_the_suffix(
    text: str, secret: str
) -> None:
    _gone(text, secret)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ('password="hunter22x', "hunter22x"),
        ("api_key='hunter22x", "hunter22x"),
        ('secret: "hunter22x', "hunter22x"),
        ("aws_secret='hunter22x more", "hunter22x"),
    ],
)
def test_n5_an_unterminated_opening_quote(text: str, secret: str) -> None:
    """N5: main's policy defaults took the run after an opening quote."""
    _gone(text, secret)
    assert _engine('password="hunter22x') == 'password="[REDACTED]'
    for kept in ('password="', 'He said "token', "it's the password's fault"):
        for surface, out in _both(kept):
            assert out == kept, (surface, out)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('Bearer "hunter22x"', 'Bearer "[REDACTED]"'),
        ("Bearer 'hunter22x'", "Bearer '[REDACTED]'"),
        ("Bearer (hunter22x)", "Bearer ([REDACTED])"),
        ("Bearer [hunter22x]", "Bearer [[REDACTED]]"),
        ("Bearer {hunter22x}", "Bearer {[REDACTED]}"),
        ("Authorization: [hunter22x]", "Authorization: [[REDACTED]]"),
        ("Authorization: {hunter22x}", "Authorization: {[REDACTED]}"),
        ('Authorization: Bearer "hunter22x"', 'Authorization: Bearer "[REDACTED]"'),
    ],
)
def test_n6_n7_a_wrapped_bearer_or_authorization_value(
    text: str, expected: str
) -> None:
    """N6/N7: the inside of a wrapped value is redacted and the wrapping
    kept. After a quoted key a bracket is JSON structure."""
    for surface, out in _both(text):
        assert out == expected, (surface, out)
    for obj in ({"Authorization": [1.5]}, {"Authorization": {"a": "b"}}):
        assert _process(obj) == obj


@pytest.mark.parametrize(
    "key",
    [
        "otp_code",
        "verification_code",
        "mfa_code",
        "recovery_code",
        "sms_code",
        "invite_code",
        # a key or a redeemable value is not a status
        "key_code",
        "promo_code",
        "coupon_code",
        "discount_code",
    ],
)
def test_n8_code_under_a_non_status_qualifier_is_a_weak_key(key: str) -> None:
    """N8: `code` under a qualifier that is not a diagnostic word redacts a
    credential-shaped value, as main did; plain words and numbers are kept
    (a weak key), and a diagnostic qualifier keeps its value (class C12)."""
    _gone(f"{key}=abc123def456", "abc123def456")
    _gone(f'{{"{key}": "abc123def456"}}', "abc123def456")
    for kept in (
        f"{key}=expired",
        f"{key}=401",
        "status_code=401",
        "error_code=invalid_grant",
        "sqlstate_code=42P01",
    ):
        for surface, out in _both(kept):
            assert out == kept, (surface, out)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("C:\\secret=hunter22x", "hunter22x"),
        ("DOMAIN\\password=hunter22x", "hunter22x"),
        ("\\password hunter22x", "hunter22x"),
        ("\\0WM9qpSecret_old=tEDvZ67S1", "tEDvZ67S1"),
        ("\\token=hunter22x", "hunter22x"),
        ("\\tenant_id: hunter22x", "hunter22x"),
    ],
)
def test_f8_a_key_after_a_backslash(text: str, secret: str) -> None:
    """F8: a backslash is a key boundary, as `\\b` made it on main; only the
    tail of a JSON escape (`\\u00a0`, `\\n`) is never part of a key."""
    _gone(text, secret)
    assert secret not in _leaf(text)
    assert _leaf("\u2022-code-id=:hunter22x") == "\u2022-code-id=:[REDACTED]"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("java -jar x.jar -password hunter22x -user bob", "hunter22x"),
        ("-token abc123def456", "abc123def456"),
        ("_token hunter22x", "hunter22x"),
        ("-secret\thunter22x", "hunter22x"),
        ("---secret abc123def456", "abc123def456"),
        ("_-password hunter22x", "hunter22x"),
        ("a-b-c-d-e-f-g-h-i-j-password hunter22x", "hunter22x"),
    ],
)
def test_n9_a_single_dash_flag_before_whitespace(text: str, secret: str) -> None:
    """N9: a single-dash or `_` flag (any run of them), and a qualifier of
    any number of joined segments, before a whitespace-separated value."""
    _gone(text, secret)
    for kept in ("-token bucket", "-secret santa", "_token ok", "use -session expired"):
        for surface, out in _both(kept):
            assert out == kept, (surface, out)


@pytest.mark.parametrize(
    "text", ["PGPassword abc123def456", "DBPassword hunter22x", "APIToken abc123def456"]
)
def test_n4b_an_acronym_glued_to_a_titlecase_key(text: str) -> None:
    """N4b: `PGPassword` (libpq) is a key; `Ed25519PrivateKey X509Cert` and
    random-case glued keys (`gby3zPassword x`, the maintainer's decision 4)
    stay prose."""
    _gone(text, text.split()[-1])
    for kept in ("Ed25519PrivateKey X509Cert", "RSAPrivateKey Rsa2048Key"):
        for surface, out in _both(kept):
            assert out == kept, (surface, out)


def test_n8_the_diagnostic_qualifiers_are_the_stated_class() -> None:
    """The qualifiers under which `code` keeps its value are exactly the ones
    the differential's class C12 states, and none names a credential."""
    from pmcp.auth import _STATUS_CODE_QUALIFIERS

    assert set(_STATUS_CODE_QUALIFIERS) == set(G.DIAGNOSTIC_CODE_QUALIFIERS)
    assert not set(G.CREDENTIAL_CODE_QUALIFIERS) & set(_STATUS_CODE_QUALIFIERS)


@pytest.mark.parametrize(
    "text",
    [
        'see https://:v1OW9iGwM@h.example:99999?SID=%69wX2caGkT7Xjb8Ll"',
        'https://h.example/?token=abc123def456"',
        'url https://h.example/?q=1&sid=hunter22x" more',
    ],
)
def test_a_url_never_ends_on_the_backslash_of_an_escaped_quote(text: str) -> None:
    """In a serialised leaf a URL runs up to the `\\` of the closing `\\"`; a
    query-value span that ate it broke the JSON, and the dict came back as a
    string (the URL axis found it; main kept such a URL when its port did not
    parse)."""
    result = _process({"t": text})
    assert isinstance(result, dict), result
    for surface, out in _both(json.dumps({"t": text})):
        json.loads(out)


#: The false positives each rev-9 narrowing was written for (the rev-10
#: audit's guard list): `(text, engine, policy, dict leaf)`, `None` meaning
#: unchanged. Four entries changed from rev 9, each commented; every other
#: entry is exactly rev 9's output.
_FALSE_POSITIVE_TABLE: list[tuple[str, str | None, str | None, str | None]] = [
    ("token_type=Bearer expires_in=3600", None, None, None),
    ('{"token_type": "Bearer"}', None, None, None),
    ("token_type: Bearer, expires_in: 3600", None, None, None),
    ('{"token_type":"Bearer","expires_in":3600}', None, None, None),
    ("token_type=bearer&expires_in=3600", None, None, None),
    ("Missing bearer token", None, None, None),
    ("the bearer of bad news", None, None, None),
    ('Bearer realm="api"', None, None, None),
    ('WWW-Authenticate: Bearer realm="x", error="invalid_token"', None, None, None),
    (
        "token_type: Bearer\nexpires_in: 3600",
        "token_type: Bearer\nexpires_in: 3600",
        "token_type: Bearer\nexpires_in: 3600",
        "token_type: [REDACTED] 3600",
    ),
    ("auth: Bearer", None, None, None),
    ("scheme=Bearer scope=read", None, None, None),
    (
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:MyDbPassword-AbCdEf",
        None,
        None,
        None,
    ),
    (
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/db/password-Xk9mQ2",
        None,
        None,
        None,
    ),
    ("arn:aws:iam::123456789012:role/token-refresher", None, None, None),
    ("urn:ietf:params:oauth:token-type:access_token", None, None, None),
    ("urn:ietf:params:oauth:grant-type:token-exchange", None, None, None),
    ("at 12:30:token expired", None, None, None),
    ("ns:token bucket", None, None, None),
    (
        "token:\nthe bearer of",
        "token:\nthe bearer of",
        "token:\nthe bearer of",
        "token:[REDACTED] bearer of",
    ),
    (
        "token:\n  - item",
        "token:\n  - item",
        "token:\n  - item",
        "token:[REDACTED]  - item",
    ),
    (
        "Set the Authorization:\nheader first",
        "Set the Authorization:\nheader first",
        "Set the Authorization:\nheader first",
        "Set the Authorization:[REDACTED] first",
    ),
    (
        "password:\n\n- bullet",
        "password:\n\n- bullet",
        "password:\n\n- bullet",
        "password:[REDACTED] bullet",
    ),
    (
        "token:\n\nThe next paragraph",
        "token:\n\nThe next paragraph",
        "token:\n\nThe next paragraph",
        "token:[REDACTED] next paragraph",
    ),
    ("Secret\n\nIngredient list", None, None, None),
    (
        "password:\r\n\r\nSee the docs",
        "password:\r\n\r\nSee the docs",
        "password:\r\n\r\nSee the docs",
        "password:[REDACTED] the docs",
    ),
    ("session\n\nexpired", None, None, None),
    ("token\n\n", None, None, None),
    (
        "Enter your password:\n\n> ",
        "Enter your password:\n\n> ",
        "Enter your password:\n\n> ",
        "Enter your password:[REDACTED] ",
    ),
    (
        "token:\n\n  - a\n  - b",
        "token:\n\n  - a\n  - b",
        "token:\n\n  - a\n  - b",
        "token:[REDACTED]  - a\n  - b",
    ),
    ("caf\xe9 tokenizer=bert", None, None, None),
    ("na\xefve session expired", None, None, None),
    ("\u201ctoken\u201d bucket", None, None, None),
    ("\u2022token bucket", None, None, None),
    (
        "\u5bc6\u7801 token \u8fc7\u671f",
        "\u5bc6\u7801 token \u8fc7\u671f",
        "\u5bc6\u7801 token \u8fc7\u671f",
        "\u5bc6\u7801 token [REDACTED]",
    ),
    ("if (token === expected)", None, None, None),
    ("if token == expected:", None, None, None),
    # rev 10 (N1): `::` is a path -- the engine keeps it; the `token` policy default still reads `:` as its separator
    ("token::Type", "token::Type", "token: [REDACTED]", "token: [REDACTED]"),
    ("std::secret::Holder", None, None, None),
    # rev 10 (N1): as `token::Type`
    ("secret::new()", "secret::new()", "secret: [REDACTED]", "secret: [REDACTED]"),
    ("password === confirm", None, None, None),
    ("a::token::b", "a::token::b", "a::token: [REDACTED]", "a::token: [REDACTED]"),
    ('password="', None, None, None),
    (
        'He said "token: x',
        'He said "token: [REDACTED]',
        'He said "token:[REDACTED]',
        'He said "token:[REDACTED]',
    ),
    ('"password": "', None, None, None),
    ("token='", None, None, None),
    ("it's the password's fault", None, None, None),
    ("Bearer (see RFC 6750)", None, None, None),
    # rev 10 (N6): the inside of a bracketed Authorization value is redacted, as main redacted `[required]`
    (
        "Authorization: [required]",
        "Authorization: [[REDACTED]]",
        "Authorization: [[REDACTED]]",
        "Authorization: [[REDACTED]]",
    ),
    ('authorization: {"type": "bearer"}', None, None, None),
    ('"authorization": ["read", "write"]', None, None, None),
    ('Bearer "realm"', None, None, None),
    ("use the Bearer [scheme]", None, None, None),
    # rev 10 (N6): the bracket stays; rev 9 ate the opening one
    (
        "Authorization: (none)",
        "Authorization: ([REDACTED])",
        "Authorization: ([REDACTED])",
        "Authorization: ([REDACTED])",
    ),
    ("error_code=E_TIMEOUT_42", None, None, None),
    ("status_code=HTTP_401", None, None, None),
    ("exit_code=137", None, None, None),
    ("zip_code=94105", None, None, None),
    ("country_code=US", None, None, None),
    ("lang_code=en-US", None, None, None),
    ("response_code=ERR_42x", None, None, None),
    ("error_code=invalid_grant", None, None, None),
    ("-token bucket", None, None, None),
    ("-secret santa", None, None, None),
    ("_token ok", None, None, None),
    ("use -session expired", None, None, None),
    ("token-based auth", None, None, None),
    ("password_ reset", None, None, None),
    ("the tokenizer=bert", None, None, None),
    ("token bucket", None, None, None),
    ("session expired", None, None, None),
]


def test_the_false_positive_guard_list_holds() -> None:
    assert (
        len(_FALSE_POSITIVE_TABLE) == len({t for t, *_ in _FALSE_POSITIVE_TABLE}) == 71
    )
    assert sum(1 for _, e, *_ in _FALSE_POSITIVE_TABLE if e is None) >= 55
    policy = PolicyManager()
    for text, engine, pol, leaf in _FALSE_POSITIVE_TABLE:
        assert _engine(text) == (text if engine is None else engine), text
        assert policy.redact_secrets(text) == (text if pol is None else pol), text
        result = policy.process_output({"t": text}, redact=True)["result"]
        assert result == {"t": text if leaf is None else leaf}, (text, result)


def test_sources_hold_no_literal_control_or_separator_characters() -> None:
    """Review tooling refuses a bundle containing a transport-active control
    character (a literal U+2028 in this file blocked every external seat on
    plan rev 9): write such characters as escapes, in code and tests."""
    import unicodedata

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in [
        root / "src" / "pmcp" / "auth.py",
        root / "src" / "pmcp" / "policy" / "policy.py",
        Path(__file__),
        Path(__file__).parent / "_redaction_grammar.py",
    ]:
        for lineno, line in enumerate(path.read_text("utf-8").split("\n"), 1):
            for ch in line:
                if (
                    unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") and ch != "\t"
                ) or ch == "\x85":
                    offenders.append(f"{path.name}:{lineno}: {ch!r}")
    assert offenders == [], offenders
```

## Oracle regenerator

`tests/fixtures/regen_redaction_main_oracle.py`, verbatim from
`origin/wip/234-redactor-rev10-code` @ `e79d75f` (committed in rev 10; sha256
`31f5cc1f…2c36`). It replaces rev 9's scratch regenerator. See *How to apply*
for the procedure.

```python
"""Record `main`'s oracle over the redaction differential corpora
(Consiliency/pmcp#234). Run it from this checkout against a MAIN tree:

  PYTHONPATH=<main checkout>/src python tests/fixtures/regen_redaction_main_oracle.py \
      <main checkout>/src tests/fixtures/redaction_main_oracle.b64

then copy the output to `.consiliency/plans/detailed-234-redactor-main-oracle.b64`
(the two stay `cmp`-identical). Not collected by pytest.

Offline and deterministic (seeded corpora, gzip mtime=0, sorted keys, base64
wrapped at 76). Keys:
  grammar[i]     tests/_redaction_grammar.py's observe() code for row i of
                 corpus(2) (tier 1 is its prefix);
  grammar_fingerprint  {"1": ..., "2": ...}: fingerprint() of each tier;
  dict[i]        pieces main's process_output removed from the serialised
                 result of _dict_corpus() row i;
  dict_types[i]  / fuzz_types[i]: type name of process_output(obj)['result']
                 for the dict and JSON-fuzz corpora.
"""

import ast
import base64
import gzip
import importlib.util
import io
import json
import multiprocessing
import random
import re
import string
import sys
import uuid
from pathlib import Path

import pmcp
from pmcp.auth import sanitize_auth_diagnostic
from pmcp.policy.policy import PolicyManager

MAIN_SRC = str(Path(sys.argv[1]).absolute())
assert pmcp.__file__.startswith(MAIN_SRC), (pmcp.__file__, MAIN_SRC)
TESTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "_redaction_grammar", TESTS / "_redaction_grammar.py"
)
G = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(G)

tree = ast.parse((TESTS / "test_redaction.py").read_text())
FUNCS = {"_dict_corpus", "_json_fuzz_corpus"}


def keep(n):
    if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        t = n.targets[0] if isinstance(n, ast.Assign) else n.target
        return isinstance(t, ast.Name) and t.id.startswith("_DIFF_")
    return isinstance(n, ast.FunctionDef) and n.name in FUNCS


ns = {"random": random, "json": json, "re": re, "string": string, "uuid": uuid}
body = [n for n in tree.body if keep(n)]
assert {n.name for n in body if isinstance(n, ast.FunctionDef)} == FUNCS
exec(compile(ast.Module(body=body, type_ignores=[]), "c", "exec"), ns)
TOK = ns["_DIFF_TOKEN"]
pm = PolicyManager()
ROWS = G.corpus(2)


def _po(obj):
    return pm.process_output(obj, redact=True, max_bytes=G.BIG)["result"]


def observe(i):
    return G.observe(
        ROWS[i],
        lambda t: sanitize_auth_diagnostic(t, max_length=None),
        pm.redact_secrets,
        _po,
    )


def rem(a, b):
    return sorted(set(TOK.findall(a)) - set(TOK.findall(b)))


if __name__ == "__main__":
    with multiprocessing.get_context("fork").Pool(20) as pool:
        grammar = pool.map(observe, range(len(ROWS)), chunksize=500)
    out = {
        "grammar": grammar,
        "grammar_fingerprint": {
            "1": G.fingerprint(G.corpus(1)),
            "2": G.fingerprint(ROWS),
        },
        "dict": [],
        "dict_types": [],
        "fuzz_types": [],
    }
    for obj, *_ in ns["_dict_corpus"]():
        r = pm.process_output(obj, redact=True)["result"]
        out["dict"].append(
            rem(
                json.dumps(obj, indent=2),
                r if isinstance(r, str) else json.dumps(r, indent=2),
            )
        )
        out["dict_types"].append(type(r).__name__)
    for obj in ns["_json_fuzz_corpus"]():
        out["fuzz_types"].append(
            type(pm.process_output(obj, redact=True)["result"]).__name__
        )
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write(json.dumps(out, sort_keys=True, separators=(",", ":")).encode())
    Path(sys.argv[2]).write_text(base64.encodebytes(buf.getvalue()).decode())
    print(
        "recorded from",
        pmcp.__file__,
        {k: len(v) for k, v in out.items()},
        "tier1",
        len(G.corpus(1)),
        "dict_types",
        {t: out["dict_types"].count(t) for t in sorted(set(out["dict_types"]))},
        "fuzz_types",
        {t: out["fuzz_types"].count(t) for t in sorted(set(out["fuzz_types"]))},
    )
```

## Grammar module

`tests/_redaction_grammar.py`, verbatim from `e79d75f` (new in rev 10; sha256
`0671e9d5…33d1`): the generator, the observation code and the accepted-class
classifier of the grammar-derived differential. Stdlib only; it imports
nothing from `pmcp`.

```python
"""The grammar-derived never-worse-than-main differential (Consiliency/pmcp#234, rev 10).

Revs 8 and 9 each passed a differential whose axes were listed by hand from
past findings, with zero unaccepted rows, and each was then blocked by inputs
on an axis nobody had listed. This corpus is derived from the ORACLE's
grammar instead -- main's rules plus what `json.dumps` emits -- so an axis
cannot be missing because nobody thought of it.

Main's rules (`main:src/pmcp/auth.py`, `main:src/pmcp/policy/policy.py`):

* the keyword rule
  `(?i)\\b[A-Za-z0-9_-]*(?:KEYS|api[_-]?key)[A-Za-z0-9_-]*[\\s:=]+[A-Za-z0-9._~+/=-]{3,}`;
* `(?i)authorization\\s*[:=]\\s*(bearer\\s+)?[^\\s,;]+` and `(?i)\\bbearer\\s+[^\\s,;]+`;
* the policy defaults' key words (`passwd`, `pwd`, `aws_secret`, `aws_access`);
* `process_output(dict)`: `json.dumps(obj, indent=2)` (`ensure_ascii=True`),
  whose escapes are `\\" \\\\ \\b \\f \\n \\r \\t` and `\\uXXXX`, and whose bare
  scalars are `null true false`, integers, floats, `NaN` and `+-Infinity`.

A text row is `pre + qual + name + suffix + sep + value`, each part drawn from
the construct it stands for:

=========  ================================================================
axis       alphabet / grammar
=========  ================================================================
pre        the character before the key: none; every ASCII punctuation
           character but the joiners (`-`, `_`); `:`; `\\`; all 29
           `str.isspace()` characters; control characters (serialised as
           `\\u0000 \\u0001 \\b \\u001b \\u007f`); non-ASCII letters,
           punctuation and an astral character (serialised as `\\uXXXX` or a
           surrogate pair); an `arn:` / `urn:` resource-name context. Each
           optionally after the word `abc`.
qual       main's `[A-Za-z0-9_-]*` prefix: joined (`x_`, `x-`), glued in
           random case (1-8), glued past the 24 bound (25-32), a leading
           joiner run (`_ - -- --- _-`), 2-10 joined segments.
name       main's 20 keys plus the policy keys, lower / UPPER / Title /
           rAnDoM case; 30% of name-focus rows are `code` under a
           qualifier: a random word, a diagnostic one
           (`DIAGNOSTIC_CODE_QUALIFIERS`, class C12) or a credential one
           (`key otp mfa sms verification recovery invite promo coupon
           discount api access secret`), in any case, `_`- or `-`-joined.
suffix     main's `[A-Za-z0-9_-]*` suffix: declared (`s _id _key Key -id id
           key`), a trailing joiner or joiner run, descriptive (`_new 2 Hash
           _type _length ized ...`), random, and past the 24 bound.
sep        main's `[\\s:=]+`: EVERY 1- and 2-character string over the 31
           characters `str.isspace()` + `:` + `=`, plus sampled 3-character
           ones; focus rows draw 1-3 characters, half operator, half space.
value      main's value class `[A-Za-z0-9._~+/=-]{3,14}`, plain words,
           integers, quoted, unterminated-quote, bracketed, JSON literals and
           punctuated values.
bearer     `bearer\\s+[^\\s,;]+` after 13 contexts (`X-Auth: `, `x=`,
           `token: `, ...) or a random punctuation/non-ASCII/space character,
           a 1-3 character `\\s` run, a credential optionally wrapped in
           `"" '' () [] {} <>`.
authz      `authorization\\s*[:=]\\s*(bearer\\s+)?[^\\s,;]+`: a prefix, 0-2
           `\\s` characters each side of the operator, a scheme (or none, or
           one followed by a blank line), a credential or plain value,
           optionally wrapped.
wrap       where the pair sits: bare; in prose (a word and one ASCII
           punctuation or `str.isspace()` character on each side -- what
           ends main's `\\b` and value class); or in each position of a URL
           main's URL rule reads: path, query pair, query value, fragment.
url        main's URL grammar itself (`https?://[^\\s"'<>]+`, trailing
           `).,;` handed back, then `redact_auth_url`): userinfo (user,
           user:password, :password), hosts (one whose port does not parse,
           which makes main keep the URL as written), 0-3 query pairs whose
           key is or is not in `AUTH_SECRET_QUERY_KEYS` (any case, optionally
           percent-encoded) with a credential, plain, percent-encoded or
           empty value, and a fragment (none, a word, a secret,
           `access_token=` + a secret), in prose. The secrets are what main
           removes.
scalars    every scalar `json.dumps` emits under 37 keys (the 24 above plus
           `auth credentials authorization Authorization bearer tokens
           input_tokens max_tokens token_type Password API_KEY privateKey
           aws_secret_access_key`), top-level, nested and in a list of
           objects.
=========  ================================================================

Every text row is observed on 12 surfaces: `E`/`P` (`sanitize_auth_diagnostic`,
`PolicyManager.redact_secrets`) on the raw text; `E`/`P` on each of the four
spellings `json.dumps({"t": text}, ensure_ascii in (True, False), indent in
(None, 2))` (`EjAC EjAI EjUC EjUI PjAC PjAI PjUC PjUI`); `process_output(text)`
(`POs`); and `process_output({"t": text})` (`POd`, the dict-leaf path), whose
result type is recorded too. A scalar row is observed on `POo`, with its type.
A piece counts as present in an output as written or percent-decoded (a query
value's `+` read as a space): main's URL rule re-encodes what it keeps, and
a re-encoded secret is still there.

Sampling density (`corpus(tier)`; tier 2 is tier 1 followed by the rest):

===============  =====================================  ======================
block            tier 1 (the default suite)             tier 2 adds (`slow`)
===============  =====================================  ======================
sep              the 992 1- and 2-character separators  the 992 x `secret`,
                 x `password`, `token` x {credential,   `api_key`, `session`
                 plain}: 3 968                          x 2, and 3 000 sampled
                                                        3-character ones x 5
                                                        keys x 2: 35 952
pre              each of the 77 pre characters x        lead `abc` x 5 keys,
                 `password`, `token` x (`=`, `: `,      no lead x 3 more
                 ` `) x {credential, plain}: 924        keys: 3 696
code             `code` under each of the 37 diagnostic  --
                 and 13 credential qualifiers x (`=`,
                 `: `) x {credential, plain}: 200
focus:<axis>     500 per axis (pre qual name suffix     9 500 per axis:
                 sep value wrap bearer authz url):      95 000
                 5 000
mix              1 000 (every axis at once, the wrap    19 000
                 included)
scalar           13 scalars x 37 keys x 3 shapes:       --
                 1 443
===============  =====================================  ======================

Tier 1 is 12 535 rows; tier 2 is 166 183. A focus row varies one axis and
keeps the others benign (no pre, qualifier, suffix or wrap; `=` or `: `; a
credential value), so a failure is attributed to one axis; mix rows vary
them all at once (`-password hunter22x` was found only there).

How it runs: tier 1 is `test_grammar_differential_never_worse_than_main_
except_by_stated_class` in the default suite. Tier 2 is
`test_grammar_differential_full_set`, marked `slow`: `pytest
tests/test_redaction.py -m slow` (about 2-3 minutes). `addopts` excludes
`slow` by default, so CI -- which runs `pytest tests/` with `addopts` --
does NOT run it.

This module is stdlib-only and imports nothing from `pmcp`: the fixture is
recorded by running it against `main`'s tree.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

# ---------------------------------------------------------------- alphabets

#: Every character `str.isspace()` accepts -- Python's `\s` (pinned against a
#: full enumeration by the axis test, not computed here: the enumeration
#: depends on the interpreter's Unicode version).
SPACES = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003"
    "\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
#: main's `[\s:=]`.
SEP_ALPHABET = SPACES + ":="
#: Every ASCII punctuation character but the identifier joiners `-`/`_`
#: (the `qual` axis) and `:`/`\` (classes of their own).
ASCII_PUNCT = "!\"#$%&'()*+,./;<=>?@[]^`{|}~"
#: -> `\u0000 \u0001 \b \u001b \u007f` in `json.dumps` output.
CONTROL = "\x00\x01\x08\x1b\x7f"
#: A letter, CJK, bullet, curly quote, dash, middle dot, astral emoji (a
#: surrogate pair when escaped), sharp s, Greek and Cyrillic.
NON_ASCII = "\xe9\u5bc6\u2022\u201c\u2014\xb7\U0001f642\xdf\u03a9\u0418"
#: A resource-name context: a key inside it names a resource.
RESOURCE = ("arn:aws:secretsmanager:us-east-1:1:secret:", "urn:ietf:params:oauth:")
PRE_CHARS: dict[str, tuple[str, ...]] = {
    "start": ("",),
    "ascii-punct": tuple(ASCII_PUNCT),
    "colon": (":",),
    "backslash": ("\\",),
    "isspace": tuple(SPACES),
    "control": tuple(CONTROL),
    "non-ascii": tuple(NON_ASCII),
    "resource": RESOURCE,
}
MAIN_KEYS = (
    "access_token",
    "api_key",
    "api-key",
    "apikey",
    "assertion",
    "client_secret",
    "code",
    "cookie",
    "id_token",
    "jwt",
    "password",
    "refresh_token",
    "saml",
    "secret",
    "session",
    "set-cookie",
    "sid",
    "tenant-id",
    "tenant_id",
    "token",
)
POLICY_KEYS = ("passwd", "pwd", "aws_secret", "aws_access")
KEYS = MAIN_KEYS + POLICY_KEYS
IDENT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MAIN_VALUE = IDENT + "._~+/=-"
PLAIN_WORDS = (
    "letmeinnow",
    "expired",
    "required",
    "bucket",
    "none",
    "hunter",
    "changeme",
    "Basic",
)
DIGITS = "0123456789"
SCALARS: dict[str, object] = {
    "null": None,
    "true": True,
    "false": False,
    "0": 0,
    "-1": -1,
    "2**100": 2**100,
    "1.5": 1.5,
    "-0.0": -0.0,
    "1e+20": 1e20,
    "1e-07": 1e-7,
    "NaN": float("nan"),
    "Infinity": float("inf"),
    "-Infinity": float("-inf"),
}
SCALAR_KEYS = KEYS + (
    "auth",
    "credentials",
    "authorization",
    "Authorization",
    "bearer",
    "tokens",
    "input_tokens",
    "max_tokens",
    "token_type",
    "Password",
    "API_KEY",
    "privateKey",
    "aws_secret_access_key",
)
BEARER_PRE = (
    "",
    "X-Auth: ",
    "X-Auth:",
    "x=",
    "token: ",
    "session=",
    "Cookie: session=",
    "(",
    '"',
    "use a ",
    "headers: {X-Auth: ",
    "X-Api-Token: ",
    "auth: ",
)
FOCI = ("pre", "qual", "name", "suffix", "sep", "value", "wrap")
#: main's `AUTH_SECRET_QUERY_KEYS`: the query keys `redact_auth_url` redacts
#: (after `parse_qsl` decodes them, case-insensitively).
MAIN_QUERY_KEYS = (
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "auth_code",
    "authorization",
    "bearer",
    "client_secret",
    "code",
    "id_token",
    "assertion",
    "key",
    "password",
    "refresh_token",
    "saml",
    "secret",
    "session",
    "sid",
    "ticket",
    "token",
    "jwt",
)
#: The stated diagnostic class (C12): `code` under one of these qualifiers
#: (its last `_`/`-` segment) names a status or a descriptive code and keeps
#: its value. Pinned equal to `pmcp.auth._STATUS_CODE_QUALIFIERS` by a test.
DIAGNOSTIC_CODE_QUALIFIERS = frozenset(
    {
        "area",
        "byte",
        "char",
        "color",
        "colour",
        "country",
        "currency",
        "err",
        "errno",
        "error",
        "event",
        "exception",
        "exit",
        "fault",
        "http",
        "iso",
        "item",
        "lang",
        "language",
        "locale",
        "op",
        "opcode",
        "postal",
        "product",
        "rc",
        "reason",
        "region",
        "response",
        "result",
        "ret",
        "return",
        "sku",
        "source",
        "sqlstate",
        "state",
        "status",
        "zip",
    }
)
#: Qualifiers under which `code` names a credential or a redeemable value.
CREDENTIAL_CODE_QUALIFIERS = (
    "key",
    "otp",
    "mfa",
    "sms",
    "verification",
    "recovery",
    "invite",
    "promo",
    "coupon",
    "discount",
    "api",
    "access",
    "secret",
)
EXHAUSTIVE_KEYS = ("password", "token", "secret", "api_key", "session")

Row = dict[str, Any]


def recase(rng: random.Random, word: str, how: str | None = None) -> str:
    how = how or rng.choice(["lower", "upper", "title", "random"])
    if how == "lower":
        return word.lower()
    if how == "upper":
        return word.upper()
    if how == "title":
        return word[:1].upper() + word[1:]
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in word)


def cred(rng: random.Random) -> str:
    """A credential-shaped value from main's value class: alphanumerics with a
    digit after the first character, which is a lower-case letter."""
    n = rng.randint(8, 16)
    s = [rng.choice(IDENT) for _ in range(n)]
    s[0] = rng.choice("abcdefghijkmnpqrstuvwxyz")
    s[rng.randrange(1, n)] = rng.choice(DIGITS)
    return "".join(s)


def ident_run(rng: random.Random, lo: int, hi: int, joiners: bool = True) -> str:
    alphabet = IDENT + ("_-" if joiners else "")
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(lo, hi)))


def sep_kind(sep: str) -> str:
    if any(c in "\r\n" for c in sep):
        return "line-break"
    if sep.count("=") >= 2 or sep.count(":") >= 2:
        return "operator-run"
    if sep.isspace():
        return "whitespace-only"
    return "mixed"


def _sample_pre(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "start", ""
    cls = rng.choice(list(PRE_CHARS))
    lead = "" if cls == "resource" else rng.choice(["", "abc"])
    return cls, lead + rng.choice(PRE_CHARS[cls])


def _sample_qual(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "none", ""
    kind = rng.choice(
        ["joined", "glued", "glued-long", "leading-joiner", "multi-segment"]
    )
    if kind == "joined":
        return kind, ident_run(rng, 1, 6, False) + rng.choice("_-")
    if kind == "glued":
        return kind, recase(rng, ident_run(rng, 1, 8, False))
    if kind == "glued-long":
        return kind, ident_run(rng, 25, 32, False)
    if kind == "leading-joiner":
        return kind, rng.choice(["_", "-", "--", "---", "_-"])
    segments = rng.randint(2, 10)
    return kind, "".join(
        ident_run(rng, 1, 3, False) + rng.choice("_-") for _ in range(segments)
    )


def _sample_suffix(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "none", ""
    kind = rng.choice(["declared", "trailing-joiner", "descriptive", "random", "long"])
    if kind == "declared":
        return kind, rng.choice(["s", "_id", "_key", "Key", "-id", "id", "key"])
    if kind == "trailing-joiner":
        return kind, rng.choice(["_", "-", "__", "_-", "--"])
    if kind == "descriptive":
        return kind, rng.choice(
            [
                "_new",
                "_old",
                "2",
                "Hash",
                "_hash",
                "_PROD",
                "_type",
                "_length",
                "ized",
                "_value",
            ]
        )
    if kind == "long":
        return kind, "_" + ident_run(rng, 25, 30, False)
    return kind, ident_run(rng, 1, 8)


def _sample_sep(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "default", rng.choice(["=", ": "])
    n = rng.choice([1, 2, 2, 3, 3])
    sep = "".join(
        rng.choice(":=") if rng.random() < 0.5 else rng.choice(SPACES) for _ in range(n)
    )
    return sep_kind(sep), sep


def _sample_value(rng: random.Random, focus: bool) -> tuple[str, str]:
    if not focus:
        return "default", cred(rng)
    kind = rng.choice(
        [
            "main-class",
            "plain",
            "number",
            "quoted",
            "unterminated-quote",
            "bracketed",
            "json-literal",
            "punctuated",
        ]
    )
    if kind == "main-class":
        v = "".join(rng.choice(MAIN_VALUE) for _ in range(rng.randint(3, 14)))
    elif kind == "plain":
        v = rng.choice(PLAIN_WORDS)
    elif kind == "number":
        v = str(rng.randint(0, 10 ** rng.randint(1, 12)))
    elif kind == "quoted":
        q = rng.choice("\"'")
        v = q + cred(rng) + q
    elif kind == "unterminated-quote":
        v = rng.choice("\"'") + cred(rng)
    elif kind == "bracketed":
        a, b = rng.choice(["()", "[]", "{}", "<>"])
        v = a + cred(rng) + b
    elif kind == "json-literal":
        v = rng.choice(
            ["null", "true", "false", "NaN", "Infinity", "-Infinity", "-0.0", "1e+20"]
        )
    else:
        v = cred(rng) + rng.choice(["!", "@x", "#1", "$", "%2F", "&x", "*"])
    return kind, v


def _text(f: dict[str, str]) -> str:
    return (
        f"{f.get('left', '')}{f['pre']}{f['qual']}{f['name']}{f['suffix']}"
        f"{f['sep']}{f['value']}{f.get('right', '')}"
    )


def _pct(rng: random.Random, text: str) -> str:
    """Percent-encode one to three characters of ``text`` (as `parse_qsl`
    decodes it)."""
    chars = list(text)
    for i in rng.sample(range(len(chars)), min(len(chars), rng.randint(1, 3))):
        chars[i] = "%{:02X}".format(ord(chars[i]))
    return "".join(chars)


_URL_BASE = ("https://h.example", "http://h.example:8443", "https://127.0.0.1")


def _sample_wrap(rng: random.Random, focus: bool) -> tuple[str, str, str]:
    """Where the pair sits: bare, in prose (a word and a delimiter each side:
    what ends main's `\\b` and value class), or in each position of a URL
    main's URL rule reads (path, query pair, query value, fragment)."""
    if not focus:
        return "none", "", ""
    kind = rng.choice(
        [
            "none",
            "none",
            "prose",
            "prose",
            "url-path",
            "url-query-pair",
            "url-query-value",
        ]
        + ["url-fragment"]
    )
    if kind == "none":  # so the mix block keeps unwrapped pairs too
        return kind, "", ""
    base = rng.choice(_URL_BASE)
    if kind == "prose":
        left = rng.choice(["", "abc", "error:", "log"]) + rng.choice(
            ASCII_PUNCT + SPACES
        )
        right = rng.choice(ASCII_PUNCT + SPACES) + rng.choice(["", "tail", "x=1"])
    elif kind == "url-path":
        left, right = base + "/p/", rng.choice(["", "/v1", "?q=1"])
    elif kind == "url-query-pair":
        left = base + "/?" + rng.choice(["", "x=1&"])
        right = rng.choice(["", "&y=2", "#f"])
    elif kind == "url-query-value":
        left, right = base + "/?q=", rng.choice(["", "&y=2"])
    else:
        left, right = base + "/#", ""
    return kind, left, right


def _url_row(rng: random.Random) -> Row:
    """A URL from main's URL grammar (`https?://[^\\s"'<>]+`, trailing `).,;`
    handed back, then `redact_auth_url`): userinfo (dropped), a host (one
    whose port does not parse makes main keep the URL as written), query
    pairs whose key is or is not in `AUTH_SECRET_QUERY_KEYS` (any case,
    optionally percent-encoded) with a credential, plain, percent-encoded or
    empty value, and a fragment (dropped). The pieces are what main removes:
    userinfo passwords, values under a secret key, fragment secrets -- one
    per secret, so at most five."""
    secrets: list[str] = []

    def secret(value: str) -> str:
        secrets.append(value)
        return value

    kinds = []
    ui = rng.choice(["none", "user", "user-pass", "pass-only"])
    userinfo = {"none": "", "user": "user@"}.get(ui) or (
        ("user:" if ui == "user-pass" else ":") + secret(cred(rng)) + "@"
    )
    if ui != "none":
        kinds.append("userinfo")
    host = rng.choice(
        ["h.example", "h.example:8443", "127.0.0.1", "[::1]", "h.example:99999"]
    )
    path = rng.choice(["", "/", "/v1/items", "/cb"])
    pairs = []
    for _ in range(rng.randint(0, 3)):
        key_kind = rng.choice(["secret", "secret", "secret-encoded", "other"])
        if key_kind == "other":
            key = rng.choice(["q", "page", "access", "next", "redirect", "u"])
        else:
            key = recase(rng, rng.choice(MAIN_QUERY_KEYS))
            if key_kind == "secret-encoded":
                key = _pct(rng, key)
        value_kind = rng.choice(["cred", "plain", "encoded", "empty"])
        value = {
            "cred": cred(rng),
            # a non-secret key's plain value never repeats a secret's word,
            # or a kept `redirect=Basic` would read as a kept secret `Basic`
            "plain": rng.choice(
                PLAIN_WORDS if key_kind != "other" else ("home", "en", "2")
            ),
            "encoded": _pct(rng, cred(rng)),
            "empty": "",
        }[value_kind]
        if key_kind != "other" and value:
            secret(value)
        kinds.append(f"query-{key_kind}-{value_kind}")
        pairs.append(f"{key}={value}")
    query = ("?" + "&".join(pairs)) if pairs else rng.choice(["", "?"])
    fragment = rng.choice(["", "#top", "#access_token=", "#"])
    if fragment in ("#access_token=", "#"):
        fragment += secret(cred(rng))
        kinds.append("fragment")
    left = rng.choice(["", "GET ", "see ", "(", '"', "url="])
    right = rng.choice(["", ".", ")", ",", ";", " failed", '"'])
    text = f"{left}{rng.choice(['http', 'https'])}://{userinfo}{host}{path}{query}{fragment}{right}"
    return {
        "block": "focus:url",
        "bucket": "url",
        "kinds": kinds,
        "f": None,
        "t": text,
        "value": " ".join(secrets),
        # one piece per secret: the longest run of an encoded one
        "pieces": [max(PIECE_RE.findall(v) or [v], key=len) for v in secrets],
    }


def _text_row(rng: random.Random, focus: str | None, block: str) -> Row:
    pre_k, pre = _sample_pre(rng, focus == "pre")
    qual_k, qual = _sample_qual(rng, focus == "qual")
    name_k = "case" if focus == "name" else "lower"
    name = rng.choice(KEYS)
    if focus == "name" and rng.random() < 0.3:
        name = "code"
        name_k = rng.choice(["code-qualified", "code-diagnostic", "code-credential"])
        if name_k == "code-diagnostic":
            word = rng.choice(sorted(DIAGNOSTIC_CODE_QUALIFIERS))
        elif name_k == "code-credential":
            word = rng.choice(CREDENTIAL_CODE_QUALIFIERS)
        else:
            word = ident_run(rng, 2, 10, False)
        qual = recase(rng, word, rng.choice(["lower", "upper", "title"])) + rng.choice(
            "_-"
        )
    if focus == "name" and name_k == "case":
        name = recase(rng, name)
    suf_k, suffix = _sample_suffix(rng, focus == "suffix")
    sep_k, sep = _sample_sep(rng, focus == "sep")
    val_k, value = _sample_value(rng, focus == "value")
    wrap_k, left, right = _sample_wrap(rng, focus == "wrap")
    kinds = {
        "wrap": wrap_k,
        "pre": pre_k,
        "qual": qual_k,
        "name": name_k,
        "suffix": suf_k,
        "sep": sep_k,
        "value": val_k,
    }
    f = {
        "pre": pre,
        "qual": qual,
        "name": name,
        "suffix": suffix,
        "sep": sep,
        "value": value,
        "left": left,
        "right": right,
    }
    bucket = f"{focus}:{kinds[focus]}" if focus in kinds else block
    return {"block": block, "bucket": bucket, "f": f, "t": _text(f), "value": value}


def _mix_row(rng: random.Random) -> Row:
    row = _text_row(rng, None, "mix")
    for focus in FOCI:
        other = _text_row(rng, focus, "mix")["f"]
        for part in ("left", "right") if focus == "wrap" else (focus,):
            row["f"][part] = other[part]
    row["t"] = _text(row["f"])
    row["value"] = row["f"]["value"]
    row["bucket"] = "mix"
    return row


def _bearer_row(rng: random.Random) -> Row:
    pre = rng.choice(list(BEARER_PRE) + [rng.choice(ASCII_PUNCT + NON_ASCII + SPACES)])
    ws = "".join(rng.choice(SPACES) for _ in range(rng.randint(1, 3)))
    v = cred(rng)
    wrap = rng.choice(["", "", "", '""', "''", "()", "[]", "{}", "<>"])
    val = (wrap[0] + v + wrap[1]) if wrap else v
    if pre.rstrip().endswith((":", "=")):
        kind = "after-:/="
    elif any(c in "\r\n" for c in ws):
        kind = "line-break"
    else:
        kind = "wrapped-value" if wrap else "plain-context"
    f = {
        "pre": pre,
        "qual": "",
        "name": recase(rng, "bearer"),
        "suffix": "",
        "sep": ws,
        "value": val,
    }
    return {
        "block": "focus:bearer",
        "bucket": f"bearer:{kind}",
        "f": f,
        "t": _text(f),
        "value": v,
    }


def _authz_row(rng: random.Random) -> Row:
    pre = rng.choice(["", "", "Proxy-", "x", "(", '"', "X-"])
    ws1 = "".join(rng.choice(SPACES) for _ in range(rng.choice([0, 0, 1, 2])))
    ws2 = "".join(rng.choice(SPACES) for _ in range(rng.choice([0, 1, 1, 2])))
    scheme = rng.choice(
        ["", "", "Bearer ", "bearer\t", "Basic ", "Token ", "Bearer\n\n"]
    )
    v = cred(rng) if rng.random() < 0.7 else rng.choice(PLAIN_WORDS)
    wrap = rng.choice(["", "", "", '""', "''", "()", "[]", "{}"])
    val = (wrap[0] + v + wrap[1]) if wrap else v
    sep = ws1 + rng.choice(":=") + ws2
    if any(c in "\r\n" for c in sep + scheme):
        kind = "line-break"
    else:
        kind = "wrapped-value" if wrap else "scheme" if scheme else "plain"
    f = {
        "pre": pre,
        "qual": "",
        "name": recase(rng, "authorization"),
        "suffix": "",
        "sep": sep + scheme,
        "value": val,
    }
    return {
        "block": "focus:authz",
        "bucket": f"authorization:{kind}",
        "f": f,
        "t": _text(f),
        "value": v,
    }


def _fixed_row(block: str, bucket: str, value: str, **parts: str) -> Row:
    f = {
        "pre": "",
        "qual": "",
        "name": "password",
        "suffix": "",
        "sep": "=",
        "value": value,
    }
    f.update(parts)
    return {"block": block, "bucket": bucket, "f": f, "t": _text(f), "value": value}


def _sep_rows(rng: random.Random, seps: list[str], keys: tuple[str, ...]) -> list[Row]:
    return [
        _fixed_row("sep", f"sep:{sep_kind(sep)}", v, name=k, sep=sep)
        for sep in seps
        for k in keys
        for v in (cred(rng), rng.choice(PLAIN_WORDS))
    ]


def _pre_rows(
    rng: random.Random, leads: tuple[str, ...], keys: tuple[str, ...]
) -> list[Row]:
    return [
        _fixed_row("pre", f"pre:{cls}", v, pre=lead + ch, name=k, sep=sep)
        for cls, chars in PRE_CHARS.items()
        for ch in chars
        for lead in (("",) if cls == "resource" else leads)
        for k in keys
        for sep in ("=", ": ", " ")
        for v in (cred(rng), rng.choice(PLAIN_WORDS))
    ]


def _code_rows(rng: random.Random) -> list[Row]:
    """`code` under every diagnostic and every credential qualifier, with a
    credential and a plain value, after `=` and `: `."""
    return [
        _fixed_row("code", f"code:{kind}", v, qual=word + "_", name="code", sep=sep)
        for kind, words in (
            ("diagnostic", sorted(DIAGNOSTIC_CODE_QUALIFIERS)),
            ("credential", CREDENTIAL_CODE_QUALIFIERS),
        )
        for word in words
        for sep in ("=", ": ")
        for v in (cred(rng), rng.choice(PLAIN_WORDS))
    ]


def _scalar_rows() -> list[Row]:
    rows = []
    for sname, s in SCALARS.items():
        for k in SCALAR_KEYS:
            for shape in ("top", "nested", "list"):
                if shape == "top":
                    obj: object = {k: s}
                elif shape == "nested":
                    obj = {"a": 1, "x": {k: s, "n": 2}}
                else:
                    obj = {"items": [{"id": 1, k: s}, {"id": 2}]}
                rows.append(
                    {
                        "block": "scalar",
                        "bucket": f"scalar:{sname}",
                        "f": None,
                        "o": obj,
                        "value": None,
                    }
                )
    return rows


def _focus_rows(rng: random.Random, per_axis: int) -> list[Row]:
    rows = []
    for focus in FOCI:
        rows += [_text_row(rng, focus, f"focus:{focus}") for _ in range(per_axis)]
    rows += [_bearer_row(rng) for _ in range(per_axis)]
    rows += [_authz_row(rng) for _ in range(per_axis)]
    rows += [_url_row(rng) for _ in range(per_axis)]
    return rows


PAIRS = list(SEP_ALPHABET) + [a + b for a in SEP_ALPHABET for b in SEP_ALPHABET]
BLOCKS = (
    ("sep", "pre", "code")
    + tuple(f"focus:{f}" for f in (*FOCI, "bearer", "authz", "url"))
    + ("mix", "scalar")
)


def corpus(tier: int) -> list[Row]:
    """Tier 1 (the default suite), or tier 2: tier 1 followed by the rest."""
    rng = random.Random(2026_09_26)
    rows = _sep_rows(rng, PAIRS, ("password", "token"))
    rows += _pre_rows(rng, ("",), ("password", "token"))
    rows += _code_rows(rng)
    rows += _focus_rows(rng, 500)
    rows += [_mix_row(rng) for _ in range(1000)]
    rows += _scalar_rows()
    if tier == 1:
        return rows
    rng = random.Random(2026_09_27)
    triples = ["".join(rng.choice(SEP_ALPHABET) for _ in range(3)) for _ in range(3000)]
    rows += _sep_rows(rng, PAIRS, ("secret", "api_key", "session"))
    rows += _sep_rows(rng, triples, EXHAUSTIVE_KEYS)
    rows += _pre_rows(rng, ("",), ("secret", "api_key", "session"))
    rows += _pre_rows(rng, ("abc",), EXHAUSTIVE_KEYS)
    rows += _focus_rows(rng, 9500)
    rows += [_mix_row(rng) for _ in range(19000)]
    return rows


def fingerprint(rows: list[Row]) -> str:
    """A digest of the rows as written: the fixture records the digest of the
    corpus it was recorded over, so a corpus change without a new fixture
    fails instead of comparing rows against another row's oracle."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(row.get("t", row.get("o"))).encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
    return digest.hexdigest()


# ------------------------------------------------------------ observation

#: The pieces of a value that count as the secret: main's value class, 3+.
PIECE_RE = re.compile(r"[A-Za-z0-9_.+/~=-]{3,}")
TEXT_SURFACES = (
    "E",
    "P",
    "EjAC",
    "EjAI",
    "EjUC",
    "EjUI",
    "PjAC",
    "PjAI",
    "PjUC",
    "PjUI",
    "POs",
    "POd",
)
SERIALISED = frozenset(TEXT_SURFACES[2:10] + ("POd", "POo"))
_DIGITS32 = "0123456789abcdefghijklmnopqrstuv"
_SPELLINGS = (
    ("AC", True, None),
    ("AI", True, 2),
    ("UC", False, None),
    ("UI", False, 2),
)
BIG = 10**7


def pieces(row: Row) -> list[str]:
    if "pieces" in row:
        return list(row["pieces"])
    return PIECE_RE.findall(row["value"]) if row["value"] else []


def observe(
    row: Row,
    engine: Callable[[str], str],
    redact_secrets: Callable[[str], str],
    process_output: Callable[[object], object],
) -> str:
    """What survives on each surface, as one base-32 digit per surface (bit i:
    piece i is still in the output, as written or percent-decoded -- main's
    URL rule re-encodes what it keeps, which is not a removal) and a final type letter for the dict
    path (`d` dict, `s` str). A piece's spelling is the same on every surface:
    nothing in `PIECE_RE`'s alphabet is escaped by `json.dumps`."""
    ps = pieces(row)
    assert len(ps) <= 5, ps

    def mask(out: str) -> str:
        decoded = unquote(out)
        return _DIGITS32[
            sum(
                1 << i
                for i, p in enumerate(ps)
                # a query value's `+` is a space once decoded
                if p in out or p in decoded or p.replace("+", " ") in decoded
            )
        ]

    def po(obj: object) -> tuple[str, str]:
        r = process_output(obj)
        if isinstance(r, str):
            return "s", r
        return ("d" if isinstance(r, dict) else "o"), json.dumps(r, indent=2)

    if "o" in row:
        kind, out = po(row["o"])
        return mask(out) + kind
    t = row["t"]
    outs = [engine(t), redact_secrets(t)]
    dumped = [json.dumps({"t": t}, ensure_ascii=a, indent=i) for _, a, i in _SPELLINGS]
    outs += [engine(d) for d in dumped] + [redact_secrets(d) for d in dumped]
    outs.append(po(t)[1])
    kind, out = po({"t": t})
    outs.append(out)
    return "".join(mask(o) for o in outs) + kind


def worse_surfaces(row: Row, main: str, here: str) -> list[str]:
    """The surfaces where `here` keeps a piece `main` removed, and `POd.type`
    / `POo.type` where main's result was a dict and this one's is not."""
    names = ("POo",) if "o" in row else TEXT_SURFACES
    worse = [s for s, m, h in zip(names, main, here) if int(h, 32) & ~int(m, 32)]
    if main[-1] == "d" and here[-1] != "d":
        worse.append(names[-1] + ".type")
    return worse


def removed_by_main(row: Row, main: str) -> bool:
    """The positive control: main removed some piece on some surface."""
    full = (1 << len(pieces(row))) - 1
    return any(int(m, 32) != full for m in main[:-1])


# ---------------------------------------------------- the accepted classes


def _plain(value: str) -> bool:
    v = value.strip("\"'").rstrip(".:!?")
    return bool(
        re.fullmatch(r"[A-Za-z_]+", v)
        or re.fullmatch(r"[+-]?[0-9]+|0[xX][0-9a-fA-F]+", v)
    )


def credential(value: str) -> bool:
    """D2, restated: not a plain word or number, and digit-bearing and 6+
    characters or punctuated and 8+."""
    value = value.strip("\"'")
    if _plain(value):
        return False
    return len(value) >= (6 if any(c.isdigit() for c in value) else 8)


_DECLARED = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "api-key",
        "assertion",
        "auth",
        "aws_access",
        "aws_secret",
        "client_secret",
        "code",
        "cookie",
        "credential",
        "credentials",
        "id_token",
        "jwt",
        "passwd",
        "password",
        "private_key",
        "privatekey",
        "private-key",
        "pwd",
        "refresh_token",
        "saml",
        "secret",
        "secret_access_key",
        "session",
        "set-cookie",
        "sid",
        "tenant-id",
        "tenant_id",
        "token",
    }
)
_WEAK = frozenset({"code", "auth", "credential", "credentials"})


def _declared(name: str, suffix: str) -> bool:
    if (name + suffix).lower() in _DECLARED:
        return True
    return (
        bool(re.fullmatch(r"[_-]?(?:id|key)|s", suffix.lower()))
        and name.lower() in _DECLARED
    )


def _single_case(word: str) -> bool:
    word = word.lstrip("-_")
    return (
        word.isupper() or word.islower() or (word[:1].isupper() and word[1:].islower())
    )


def _acronym_title(word: str) -> bool:
    return re.fullmatch(r"[A-Z0-9]{1,8}[A-Z][a-z]+", word.lstrip("-_")) is not None


def _inner_key(name: str, suffix: str) -> bool:
    """The key as written holds another secret-key word that starts inside the
    written name and ends inside the suffix (`aws_access` + `id` holds
    `sid`): main's containing match read that inner key."""
    word = (name + suffix).lower()
    for key in KEYS:
        start = word.find(key, 1)
        while start != -1:
            if start < len(name) < start + len(key):
                return True
            start = word.find(key, start + 1)
    return False


#: The stated classes (Consiliency/pmcp#234 rev 10: the audit's accept list
#: and the maintainer's four decisions). Each is decided from the row as
#: written -- `pre`, `qual`, `name`, `suffix`, `sep`, `value` -- and the
#: surface it is read on (a serialisation is a wrap), never from any output.
CLASSES = {
    "N3": "a glued prefix or a suffix of more than 24 alphanumerics: an identifier, not a key (a cost bound)",
    "N10": "a key inside an `arn:`/`urn:` resource name names a resource",
    "C3a": "`code` never fires on a whitespace-only separator",
    "C3": "a whitespace-only separator with a non-credential-shaped value (D2)",
    "N11": "a `name=value` token after a keyword and whitespace is its own pair",
    "N4": "a glued mixed-case key before whitespace is an identifier, not a key (`Ed25519PrivateKey X509Cert`, `gby3zPassword x`)",
    "N4c": "the key as written holds another key word starting inside the written name (`aws_accessid` holds `sid`)",
    "C4": "an operator run holding `==` or `::` with a non-credential-shaped value is a comparison or a path",
    "C5": "a suffixed key with a non-credential-shaped value names metadata",
    "C6": "a glued key (no joiner before the name, any case) counts only with a credential-shaped value that is not a URL or ARN",
    "C7": "after an unquoted key a value starting on `,`/`}` is JSON structure",
    "C8": "a separator whose last line break is unindented, with a non-credential-shaped unquoted value, ends a sentence",
    "N6b": "a quoted value on a serialised surface is JSON inside a leaf (Consiliency/pmcp#290)",
    "C10": "a weak key keeps a plain word or number; `code` under a non-OAuth qualifier keeps any non-credential-shaped value (N8's gate)",
    "C11": "`Authorization`/`Bearer` followed by a plain word, bare or wrapped in quotes or brackets, is prose",
    "C12": "a `code` key qualified by a diagnostic or descriptive word (`DIAGNOSTIC_CODE_QUALIFIERS`, the qualifier's last segment) keeps its value (`error_code=E_TIMEOUT_42`, `sqlstate_code=42P01`)",
}


def accepted(f: dict[str, str], surface: str) -> str | None:
    """The accepted class of a (row, surface) this redactor is worse on than
    main, or None: a defect."""
    pre, qual, name, suffix = f["pre"], f["qual"], f["name"], f["suffix"]
    pre = f.get("left", "") + pre  # the wrap's text before the pair
    glue_m = re.search(r"[A-Za-z0-9]*$", pre)
    full_qual = (glue_m.group(0) if glue_m else "") + qual
    sep, value = f["sep"], f["value"]
    lead_m = re.match(r"[:=]*", value)
    lead = lead_m.group(0) if lead_m else ""
    sep, value = sep + lead, value[len(lead) :] or value
    lname = name.lower()
    cred = credential(value)
    glued_run = re.search(r"[A-Za-z0-9]*$", full_qual)
    glued = glued_run.group(0) if glued_run else ""
    key = glued + name + suffix
    if len(glued) > 24 or sum(c.isalnum() for c in suffix) > 24:
        return "N3"
    if re.search(r"\b[au]rn:[^\s\"'<>]*$", pre, re.IGNORECASE):
        return "N10"
    if lname == "code" and re.split(r"[_-]", full_qual.strip("_-").lower())[-1] in (
        DIAGNOSTIC_CODE_QUALIFIERS
    ):
        return "C12"
    if sep.isspace():
        if lname == "code":
            return "C3a"
        if not cred:
            return "C3"
        # the value as written runs on into the wrap (`x=1`, `=tail`)
        if re.match(r"[A-Za-z_-]+=[^=]", value + f.get("right", "")):
            return "N11"
        if glued and not _single_case(key) and not _acronym_title(key):
            return "N4"
    if ("==" in sep or "::" in sep) and not cred:
        return "C4"
    if suffix and not _declared(name, suffix) and not cred:
        return "C5"
    if glued and (not cred or "://" in value or value.lower().startswith("arn:")):
        return "C6"
    if not sep.startswith('"') and value.startswith((",", "}")):
        return "C7"
    if (
        re.search(r"[\r\n][^ \t\xa0]*$", sep)
        and not value.startswith(('"', "'"))
        and not cred
    ):
        return "C8"
    if surface in SERIALISED and re.match(
        r"(?:(?:bearer|basic|digest|negotiate|ntlm|token)\s+)?\"", value, re.IGNORECASE
    ):
        return "N6b"
    if surface in SERIALISED and '\\"' in sep:
        return "N6b"
    if lname in _WEAK and (not suffix or _declared(name, suffix)):
        if _plain(value):
            return "C10"
        qualifier = full_qual.strip("_-").lower()
        if lname == "code" and qualifier not in _OAUTH_QUALIFIERS and not cred:
            return "C10"
    if lname in ("authorization", "bearer") and _plain(_unwrap(value)):
        return "C11"
    if _inner_key(name, suffix):
        return "N4c"
    return None


_OAUTH_QUALIFIERS = frozenset({"", "auth", "authorization", "oauth", "device", "user"})


def _unwrap(value: str) -> str:
    if len(value) >= 2 and value[0] + value[-1] in ('""', "''", "()", "[]", "{}", "<>"):
        return value[1:-1]
    return value
```

## pyproject.toml change

`git diff 9ca081e origin/wip/234-redactor-rev10-code -- pyproject.toml`,
verbatim (the `slow` marker, excluded by default; `pyproject.toml` is unchanged
between `9ca081e` and today's `main`).

```diff
diff --git a/pyproject.toml b/pyproject.toml
index 500a26d..9b3dc88 100644
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
  `original_size` parameter and `collect_redaction_spans` (new, public) a
  keyword-only `covers`; `widen_over_escapes` is new and public (rev 9,
  imported by `policy.py`); `redact_auth_url` is unchanged (rev 4's
  `path_transform` is gone). Every existing call is unchanged. No CHANGELOG
  line for either beyond the `### Security` entry, which should say that redaction
  now runs before truncation and that passes no longer see each other's
  output.
- The diagnostic engine's URL output changes shape: a redacted query value
  is `[REDACTED]`, not `%5BREDACTED%5D`, and the URL is otherwise left as
  written; `sanitize_public_auth_url`/elicitation URLs keep `main`'s form.
- SECURITY.md's key list (line 120-122) should name the widened set, or
  refer to `AUTH_DIAGNOSTIC_SECRET_KEYS` / `WEAK_SECRET_KEYS` — implementer.
- `pyproject.toml` gains a `slow` pytest marker, excluded by default in
  `addopts` (`-m 'not live and not slow'`); CI runs `pytest tests/` with
  `addopts`, so it does not run the slow tier (decision 7). CONTRIBUTING or
  the test docs should say `pytest tests/test_redaction.py -m slow` runs
  it — implementer.
- No README change: operators see the same `[REDACTED]` marker; the
  `redaction.patterns` policy key is unchanged in shape (operators who set their
  own patterns still displace the defaults, including the fixed `token` one).

## Dependencies & order

1. `src/pmcp/auth.py` — the shape block and the three keyword rules (all new
   symbols), then the `sanitize_auth_diagnostic` body. Nothing else compiles
   against them until they exist.
2. `src/pmcp/policy/policy.py` — `_REDACTION_WINDOW_SLACK`, the defaults,
   `truncate_output`, `_truncation_marker`, `_pattern_matches`,
   `redaction_spans`, `redact_secrets`, `process_output`.
3. `tests/test_redaction.py` from `## Test bodies`,
   `tests/_redaction_grammar.py` from `## Grammar module`,
   `tests/fixtures/regen_redaction_main_oracle.py` from
   `## Oracle regenerator`, the `pyproject.toml` marker diff, and
   `tests/fixtures/redaction_main_oracle.b64` copied from
   `.consiliency/plans/detailed-234-redactor-main-oracle.b64`.
4. `uv run pytest tests/test_redaction.py tests/test_auth.py tests/test_policy.py tests/test_project_source_consent_policy.py tests/test_trust_boundaries_e2e.py`
   — must be green with `tests/test_auth.py` **unmodified**.
5. Mutation table (below) — every row RED, every diff confirmed.
6. `SECURITY.md` claim + CHANGELOG, then the full suite and the CI gates.

## Verification

```bash
cd <worktree>
uv run pytest tests/test_redaction.py -q --cov-fail-under=0                      # 472 passed, 15 deselected (slow)
uv run pytest tests/test_auth.py -q --cov-fail-under=0                           # 128 passed, file byte-identical to main
uv run pytest tests/test_policy.py tests/test_project_source_consent_policy.py \
              tests/test_trust_boundaries_e2e.py -q --cov-fail-under=0           # C-13 proofs + truncation pins
uv run pytest --collect-only -q tests/test_redaction.py | grep -c '::'            # 472 (a -k that matches nothing exits 0)
# tier 2 of the grammar differential -- the `slow` marker; NOT run by CI (decision 7):
uv run pytest tests/test_redaction.py -m slow -q --cov-fail-under=0               # 15 passed, ~3 min (-m on the command line overrides addopts)
uv run ruff check src/ tests/                                                     # CI gate
uv run ruff format --check src/ tests/                                            # CI gate
uv run mypy src/                                                                  # CI gate -- measured: Success: no issues found in 49 source files
uv run python3 scripts/check_security_claims.py                                   # after the SECURITY.md edit
uv run python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md        # blocking inconsistencies: 0
# full suite -- as a background task that notifies on exit (never `nohup … & disown`,
# which notifies no one); npm env vars unset; ~4 500 tests, ~10 min. Plain `pytest tests/`
# uses addopts (`not live and not slow`); `-m 'not live'` on the command line REPLACES
# addopts and so also runs the 15 slow cases.
uv run pytest tests/ -q > <scratch>/pmcp-234-full.log 2>&1; echo EXIT=$? >> <scratch>/pmcp-234-full.log
tail -n 3 <scratch>/pmcp-234-full.log
```

Measured on the rev-10 code (`origin/wip/234-redactor-rev10-code` @
`e79d75f`, a scratch worktree): `test_redaction.py` **472 passed, 15
deselected** (21 s); the slow tier **15 passed in 195 s**; `test_auth.py`
(unmodified) **128 passed**; the five targeted files **658 passed, 15
deselected**; `ruff check`, `ruff format --check` clean; `mypy src/`
**Success: no issues found in 49 source files**. Full suite: see
Unverified. Rev 9 (history): on
`8e7d98c`, `test_redaction.py` **306 passed** (~10 s);
`test_auth.py` (unmodified) **128 passed**; the five targeted files above
together **492 passed**; `ruff check` and `ruff format --check` on
`src/ tests/` clean; `mypy src/` **Success: no issues found in 49 source
files**. Full suite, run by the coordinator on `8e7d98c` (`-m 'not live'`;
the log names no tree, but its count is consistent: 4 370 − 4 213 = 157 =
306 − 149): **4370 passed, 3 skipped, 25 deselected in 459.38s (0:07:39)**,
`DONE-EXIT=0`. Rev 8 (history): 149 passed; full suite 4213 passed, 3
skipped, 25 deselected in 421.04s. Rev 7
and earlier, kept as history: `test_redaction.py` **136 passed** (14.1 s);
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

> **Rev 10.** The 21 rev-10 mutants, each run over the whole default-tier
> file, are in "Mutants" under the rev-10 section (all killed). The rows
> below (rev 9's seven round-3 mutants, D1, D2, Q1-Q3, and M01-M38) were
> **not re-run on rev 10**; their evidence is the revision named with each.

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
the reason is stated so no one mistakes its absence for an omission. Rev 7
rebuilds **M28** as a two-rule mutant (the whole-URL rewrite span *and* rev
5's unconditional containment restored together; with only the rewrite
restored, the new containment rule merged the URL to `[REDACTED]` and the
test went red for the wrong reason — codex), adds **M34** (a quote before
`Bearer` rejected), **M35** (rev 6's `[:=](?!=)` separator), **M36** (encoded
query values not decoded), **M37** (structured results not redacted as
structure), **M38** (no newline after the separator) — M34, M35 and M38 are
red on the differential as well as on their named tests — and re-anchors
M05/M08/M21/M24/M26/M33 to the rev-7 text (the runner flagged each as NOT
APPLIED first). Rev 7's final run, all 35 rows, is the table below
(M01-M38).

**Rev 9.** Every mutant below was re-run for this plan on the frozen rev-9
code (`8e7d98c`) in a scratch worktree: the edit applied as a text
replacement, each anchor's occurrence count and the diff counted, the named
test run **and then the whole `tests/test_redaction.py`** (306 nodes, no
`-k`), the file restored from a saved copy and its sha256 re-checked against
the frozen value (baseline 306 passed; restored after every row). The
round-3 mutants are the test agent's (`scratchpad/234-r9tests/mutants.py`),
one per fix that the widened differential found; two of them edit **two**
sites (anchor occurrences 2) because the rule they undo lives in both
`_KEYWORD_KEY_SEP` and `_KEYWORD_WS_RE`, or in both continuations — that is
the intended mutant, not a loose anchor.

| id | anchor occurrences / diff lines | named test | whole file | red functions |
|---|---|---|---|---|
| list-body — rev 8's quote-aware bracket body restored in place of the literal array | 1 / 2 | `test_a_keyed_list_never_straddles_a_string_boundary`: 2 failed | 2 failed, 304 passed | `test_a_keyed_list_never_straddles_a_string_boundary` |
| A — `widen_over_escapes` made a no-op | 1 / 1 | `test_no_span_starts_inside_a_json_escape`: 24 failed | 25 failed, 281 passed | that test, `test_differential_on_structured_results_never_worse_than_main` |
| B — no suffix on the whitespace rule | 1 / 2 | `test_n1_suffixed_keys_redact_a_credential_shaped_value`: 4 failed, 8 passed | 7 failed, 299 passed | that test, `test_a_dash_glued_to_a_value_is_part_of_it`, `test_bearer_is_a_scheme_not_a_word`, the string differential |
| C — no glued prefix (`{0,24}?` → `{0}?`, both key regexes) | 2 / 4 | `test_a_case_folded_compound_key_is_still_a_key`: 24 failed | 25 failed, 281 passed | that test, the string differential |
| other quote — the other-quote straddle clause removed | 1 / 2 | `test_a_quoted_value_never_straddles_via_the_other_quote`: 2 failed | 2 failed, 304 passed | that test |
| dash (1) — rev 8's `(?![-*#>])` restored on the whitespace rule | 1 / 2 | `test_a_dash_glued_to_a_value_is_part_of_it`: 4 failed, 3 passed | 5 failed, 301 passed | that test, the string differential |
| dash (2) — rev 8's continuation lookahead `(?=[^\s\-*#>])` restored (both continuations) | 2 / 4 | `test_a_dash_glued_to_a_value_is_part_of_it`: 2 failed, 5 passed | 3 failed, 303 passed | that test, the string differential |

The five rev-8 mutants, re-anchored where needed and re-run on rev 9 (whole
file): **D1** (decode depth fails open) 2 failed — the two depth tests;
**D2** (`covers=None`) 2 failed — the two decoded-value pattern tests;
**Q1** (quoted span includes its quotes) 18 failed — including
`test_redaction_never_breaks_a_json_document`, the dict differential, the
JSON fuzz, `test_n3_…` and `test_policy_defaults_…` ×4; **Q2** (no list
pass) 4 failed — `round_trips_as_a_dict[obj1]`, `[obj4]`, the pretty-printed
list test, `test_c7_…`; **Q3** (list weak-key gate off, now `if False:`) 2
failed — `round_trips_as_a_dict[obj4]`, `test_b2_…[obj1]`. All RED.
Q4 remains equivalent (no guard to mutate). The rev-8 table below records
the same five on `56d7f80`.

The board findings B1-B5, N1-N4, G1, C2, C4, C6 and C7 are pinned by their
named tests, each red on rev 8 and green on rev 9 (the red/green table in
Changes); no separate mutant was written for them.

**Rev 8.** Five mutants, one per new mechanism, were run on the frozen rev-8
code (`56d7f80`, applied from this plan in a scratch worktree off `main`): each a
single-occurrence text replacement (the runner refuses an anchor that does
not occur exactly once), the diff counted, the **whole**
`tests/test_redaction.py` run (149 nodes, no `-k`), the file restored from a
saved copy and its sha256 re-checked against the frozen value (baseline
before the run: 149 passed; all restored). Run on `56d7f80`; on `d316364`
the same five gave the same failures (Q3 then had a 4-line diff, its gate
spanning three lines). A sixth, **Q4** (skip an empty
quoted value `""` in `_keyword_sep_spans`), is **equivalent**:
`apply_redaction_spans` drops a zero-length span, so the guard changed
nothing — the guard was deleted from the code rather than kept untested.
`{"password": ""}` staying `""` (`test_structured_result_round_trips_as_a_dict[obj3-expected3]`)
is that zero-length drop at work (`apply_redaction_spans` keeps only spans
with `start < end`). By the same argument an empty-element check in
`_keyword_list_spans` would be equivalent, so it is not there either.

| id | verdict | evidence | why it is red |
|---|---|---|---|
| D1-decode-depth-fails-open | RED | 2 diff lines (`return True` → `return False` at `_MAX_DECODE_DEPTH`); 2 failed, 147 passed | `test_url_decode_depth_bound_fails_closed`, `test_encoded_query_values_are_decoded_a_bounded_number_of_times`: past the bound the encoded blob is passed through instead of redacted |
| D2-policy-covers-none | RED | 2 diff lines (`covers=self._pattern_matches` → `covers=None` in `redaction_spans`); 2 failed, 147 passed | `test_operator_pattern_applies_to_a_percent_encoded_query_value`, `test_encoded_query_values_are_matched_by_the_policy_patterns_too`: an operator pattern and the `ghp_` default never see the decoded value |
| Q1-quoted-span-whole | RED | 2 diff lines (the inside-the-quotes adjustment removed); 12 failed, 137 passed | a quoted keyed value is redacted with its quotes again: `test_redaction_never_breaks_a_json_document`, `test_differential_on_structured_results_never_worse_than_main` (a dict comes back as a string), `test_structured_result_round_trips_as_a_dict` ×3, and the 7 quote-preserving expectations |
| Q2-no-list-pass | RED | 1 diff line (`*_keyword_list_spans(text)` removed from `collect_redaction_spans`); 3 failed, 146 passed | `test_pretty_printed_list_value_is_redacted_inside_its_quotes`, `test_structured_result_round_trips_as_a_dict[obj1-…]` and `[obj4-…]`: list elements under a secret key survive |
| Q3-list-weak-filter-off | RED | 2 diff lines (the weak-key plain-word gate in `_keyword_list_spans` replaced by `if False:`); 1 failed, 148 passed | `test_structured_result_round_trips_as_a_dict[obj4-expected4]`: `{"code": ["red", …]}` loses `red` |
| Q4-empty-quoted-value-skip | EQUIVALENT — guard deleted | (recorded in the rev-8 session notes; not re-run here: there is nothing left to mutate) | `apply_redaction_spans` drops zero-length spans |

**Rows M01-M38 were not re-run on rev 8 or on rev 9.** Their evidence below is rev 7's
run on rev 7's code; the rev-8 and rev-9 code keeps every mechanism they target except
M37's. M05's premise changed in rev 9: `secret-bearer hunter2` is now
redacted by the suffixed-key rule (as on `main`), so the assertion that
made M05 red is gone; `secret-bearer failed` is still kept, and M05 must be
re-derived before it is trusted. **M37 is retired** (its anchor, `_redact_leaves`, no longer exists —
the structured layer went to Consiliency/pmcp#290), as M10 and M23 were in
rev 4. The implementer re-runs the whole table on their own tree (Execution
Policy); a row whose anchor moved must report NOT APPLIED, not pass.

Rev 7's final run, all 35 rows:

| id | verdict | evidence | why it is red |
|---|---|---|---|
| M01-ws-gate-off | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | whitespace keyword rule redacts any value again: `token bucket` -> `token [REDACTED]` |
| M02-no-payload-bound | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | an 8 KB base64 blob is scored as a credential and replaced whole |
| M03-whole-run-with-joiners | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.06s | `pmcp-7d9f8b6c5-x2k9q` scored whole reads as random and is redacted |
| M04-hex-without-0x | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | `<Foo object at 0x7f3a2b1c4d50>` loses its address: the `x` breaks uniform hex |
| M05-bearer-word-boundary | RED | 4 diff lines; 1/1 nodes collected; 1 failed in 0.05s | `\bbearer` fires inside the hyphenated word `secret-bearer` (main's bug) and redacts the next word: `secret-bearer hunter2` -> `secret-bearer [REDACTED]` |
| M06-bearer-gate-off | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | `Missing bearer token` -> `Missing bearer [REDACTED]` |
| M07-code-qualifiers-off | RED | 3 diff lines; 1/1 nodes collected; 1 failed in 0.05s | `error_code=AADSTS50011` and `reason_code=E-1234` are treated as OAuth codes and redacted |
| M08-sep-spans-newline | RED | 4 diff lines; 1/1 nodes collected; 1 failed in 0.05s | `Missing bearer token:\nthe bearer of` -> the next line's first word is redacted |
| M09-split-guard-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | base64 padding is taken as the separator: `dXNlcjpwYXNzd29yZA= [REDACTED]` |
| M11-cut-before-redact | RED | 1 diff lines; 1/1 nodes collected; 1 failed in 0.04s | the cut at 400 leaves nine characters of the token, which no longer has a redactable shape |
| M12-main-default-token-pattern | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | main's default pattern redacts the word after `token` on the policy surface even though the engine no longer does |
| M13-prefixed-rule-off | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.05s | `sk-abcdef123456` has a prefix but too little entropy for the transition score (engine nodes only: the policy-surface twin still passes via the `\bsk-` default pattern) |
| M14-prefixed-accepts-alpha-body | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.07s | the engine eats `ghp_abcdefghijklmnop`, and the C-13 proof that a DEFAULT pattern applies goes vacuous |
| M15-aws-vendor-shape-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | `AKIAIOSFODNN7EXAMPLE` is uniform upper-case with one digit: the transition score cannot see it |
| M16-spike-url-path-redaction | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | the inherited spike's `redact_auth_url` change: the Okta authorization-server id in an elicitation URL is redacted |
| M17-single-number-camel-clause-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | `Oauth2ClientError` (one embedded number, ratio 0.375) is scored opaque |
| M18-payload-bound-after-path-split | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.07s | a slash-leading payload is split at every `/` and each piece scored: `/9j/...` JPEG base64 comes back as `[REDACTED]/[REDACTED]/...` |
| M19-redaction-window-without-slack | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.09s | a 4 000-char keyed value straddling the cap ends outside a slack-less window, is not matched, and its first characters survive the cut |
| M20-quoted-value-stops-at-escaped-quote | RED | 2 diff lines; 2/2 nodes collected; 1 failed, 1 passed in 2.97s | JSON {"password": "a\"hunter2"} -> {"password": [REDACTED]hunter2"} on both surfaces; the property test finds it in the first few hundred passwords |
| M21-bearer-value-eats-quotes | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.06s | rev 2 Bearer value class eats the closing quote and brace: {"note": "see Bearer abc123def456"} -> {"note": "see Bearer [REDACTED] |
| M22-keyed-values-pass-removed | RED | 1 diff lines; 3/3 nodes collected; 3 failed in 0.07s | no keyed-value pass at all: {"password": "hunter2"} survives |
| M24-authorization-rule-misses-quoted-key-and-eats-quotes | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | rev 2 Authorization key syntax: the quote after a JSON key defeats authorization\s*[:=], so {"authorization": "Bearer abc123def456", "x": 1} is not matched at all |
| M25-mutate-in-place-composition | RED | 1 diff lines; 3/3 nodes collected; 1 failed, 2 passed in 3.38s | revs 1-3 composition for one pass: the URL pass rewrites the text before the others read it, removes the backslash escaping a quote, and {"password": "https://...\"hunter2"} -> {"password": [REDACTED]hunter2"}; the property test finds the class on its own |
| M26-truncate-then-redact | RED | 23 diff lines; 3/3 nodes collected; 3 failed in 0.07s | main order (cut, then redact): the cut leaves ghp_16C7e, "hunter2 , "hunter2\ -- shapes no rule recognises; the sweep reports leaks |
| M27-passwd-dropped-from-the-strong-set | RED | 1 diff lines; 1/1 nodes collected; 1 failed in 1.65s | `passwd` is still named by the policy default `(secret|password|passwd|pwd)` but no longer by the engine: `{"passwd": "…"}` leaks and the derived-key property reports it |
| M28-url-rewrite-span | RED | 4 diff lines; 2/2 nodes collected; 2 failed in 0.73s | rev 5 URL pass WITH rev 5 containment (both rules restored together): the whole-URL rewrite span contains and drops every keyed/shape span inside the query, so ?pwd=hunter2 and ?access=ghp_... leak with the URL context intact (with only the rewrite restored, the new containment rule merges the URL to [REDACTED] and the test fails for the wrong reason) |
| M29-window-edge-emitted | RED | 3 diff lines; 1/1 nodes collected; 1 failed in 0.06s | rev 5 window: spans applied to the whole window and the far edge returned when redaction shrank it under the cap; a value straddling max_bytes+16384 shows its head |
| M30-marker-inertness | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.65s | rev 5 marker rule: any span touching a literal [REDACTED] in the INPUT is dropped, so password=hunter2[REDACTED] survives |
| M32-authorization-gate-off | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | authorization: none -> authorization: [REDACTED] |
| M33-double-equals-is-a-separator | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | if token == expected: -> if token =[REDACTED] expected: |
| M34-bearer-rejected-after-a-quote | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.61s | rev 6 Bearer lookbehind: a quote before Bearer blocks the rule, so {"text": "Bearer test-token"} passes; main redacts it; the differential reports it |
| M35-separator-rejects-httpie-and-ruby | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.64s | rev 6 separator: password:=hunter2 and password==hunter2 pass the engine, {"password"=>"hunter2"} turns into =[REDACTED]"hunter2"; main redacts them; the differential reports it |
| M36-encoded-query-values-not-decoded | RED | 2 diff lines; 1/1 nodes collected; 1 failed in 0.04s | ?q=%67%68%70%5F… (an encoded ghp_ token) keeps its encoded prefix |
| M37-structured-results-not-redacted-leaf-by-leaf | RETIRED in rev 8 | rev 7: 2 diff lines; 1/1 nodes collected; 1 failed in 0.05s | its target, `_redact_leaves`, was removed with the structured layer (decision (c), Consiliency/pmcp#290) |
| M38-no-newline-after-the-separator | RED | 2 diff lines; 2/2 nodes collected; 2 failed in 0.66s | YAML block style and pretty JSON with the value on the next line pass; main redacts them; the differential reports it |

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
      → `{"password": "[REDACTED]"}` on both surfaces, and
      `see https://example.test/?token=abc123def456&page=2.` keeps its host and
      route on the engine: `see https://example.test/?token=[REDACTED]&page=2.`
      (`test_no_pass_can_consume_a_boundary_another_pass_needs`); M25 red.
- [ ] **Truncation order.** `"a"*177 + " " + json.dumps({"password": "hunter2\\tail"}) + "c"*100`
      at `max_bytes=300` contains no `hunter2`; a 4 000-char value straddling
      the cap is `[REDACTED]` (`test_truncation_after_a_backslash_cannot_expose_a_password`,
      `test_the_redaction_window_reaches_past_the_cap`); M26 and M19 red.
- [ ] **Grammar differential (rev 10; replaces rev 9's hand-listed
      differential).** `test_grammar_differential_never_worse_than_main_except_by_stated_class`
      (tier 1, 12 535 rows × 12 surfaces): 0 unaccepted, the oracle's
      fingerprint equal to the corpus's, the positive control above 85 % of
      text rows (measured 10 070 of 11 070); `test_grammar_differential_full_set`
      (`-m slow`, 15 blocks, 166 183 rows): 0 unaccepted;
      `test_the_grammar_corpus_covers_every_axis` green. Every row of tables
      A and B is ⊇ or names its class; the class counts match the rev-10
      table. The slow tier is run by the implementer before merge (it is
      not in CI).
- [ ] **Rev 10 findings.** Each named test green, each red on rev 9
      (157 red nodes in 21 functions): F1-F5, F8, F9, N1, N2, N4b, N5,
      N6/N7, N8 (including `key_code` redacted, decision 5), N9, the
      escaped-space and policy-default F3 tests, the URL backslash; the
      scalar rows unchanged (decision 1); the 71-string false-positive
      guard list holds; the 21 mutants killed.
- [ ] **No literal control or separator characters** in the sources
      (`test_sources_hold_no_literal_control_or_separator_characters`) or
      in this plan (categories Cc, Cf, Zl, Zp except tab and newline, and
      U+0085: 0).
- [ ] `test_the_differential_corpus_covers_every_axis` (rev 9's string
      corpus, still generated for the JSON-validity test) green.
- [ ] **Dict differential.** `test_differential_on_structured_results_never_worse_than_main`:
      800 dict results through `process_output(dict, redact=True)`, 0
      unaccepted; wherever `main`'s result is a dict (the oracle's
      `dict_types`, 786 of 800) rev 10's is a dict too (measured on rev 10:
      dict → dict 786, str → dict 14, dict → str 0).
- [ ] **JSON fuzz.** `test_property_json_fuzz_keeps_documents_and_dicts`:
      seed 7, 1 500 objects × 4 serialisations; every document without `\"`
      (> 3 000; measured 3 272) still parses on both surfaces, and
      `process_output` returns a dict wherever `main` did (1 181 objects).
- [ ] **Rev 9 board classes.** Each named test green, each red on rev 8:
      B1 (`null`/`true`/`false` kept; since rev 10 every bare scalar is
      kept, decision 1, so `{"token": 42}` is unchanged), B2, B3 (`db.password=`), B4 (`AccessToken=`), B5 (all 27
      non-line-break `str.isspace()` characters), N1-N4 (`"a_" * 33000`
      under 2 s), G1 (`password == hunter2` redacted, `if token ==
      expected:` kept), C2, C4, C6, C7, the policy defaults, list straddle,
      A, B, C, dash, other quote; the seven round-3 mutants RED.
- [ ] **Blocking regressions.** `{"text": "Bearer test-token"}` →
      `{"text": "Bearer [REDACTED]"}`; `password:=hunter2` → `password:=[REDACTED]`;
      `password==hunter2` → `password==[REDACTED]`; `if token == expected:`
      unchanged on both surfaces; `?q=%67%68%70%5F…` → `?q=[REDACTED]`; `{"password"=>"hunter2"}`
      → `{"password"=>"[REDACTED]"}` (M34, M35, M36 red).
- [ ] **Serialisation — decision (c).** No structured layer: a dict result
      is serialised and redacted as text, as on `main`.
      `process_output({"password": "hunter2"}, redact=True)` →
      `{"password": "[REDACTED]"}` and the four other shapes of
      `test_structured_result_round_trips_as_a_dict` come back as dicts;
      the 810 JSON rows of the corpus stay valid JSON on both surfaces
      (`test_redaction_never_breaks_a_json_document`); a `Bearer` value in a
      JSON text leaf is redacted with the leaf's JSON intact. JSON text
      inside a leaf (`\"password\": \"hunter2\"`) is **not** an
      acceptance criterion here — Consiliency/pmcp#290 (M37 retired; Q1 red).
- [ ] **URL queries.** `https://example.com/?aws_secret_access_key=wJal…`,
      `…?access=ghp_…`, `…/login?pwd=hunter2`, `…?q=<24 random alnum>&page=2`
      → the value `[REDACTED]`, the rest of the URL as written, on both
      surfaces; `redact_auth_url` byte-identical to `main` (M28 red).
- [ ] **Window edge.** With a shrinkable PEM prefix and a quoted password
      straddling `max_bytes + 16384`, no cut emits `hunter2` and the output
      never exceeds `max_bytes` (M29 red).
- [ ] **Markers.** `password=hunter2[REDACTED]` → `password=[REDACTED]`,
      `{"password": "hunter2 [REDACTED]"}` → `{"password": "[REDACTED]"}`;
      `password=[REDACTED]`, `password= [REDACTED]`, `{"password":
      [REDACTED]}` are fixed points on both surfaces (M30 red).
- [ ] **Composition property** ≥ 4 000 spans checked, 0 leaks; **never worse
      than main**: all 42 corpus inputs pass on both surfaces.
- [ ] `authorization: none` and `Set the Authorization:\nheader first`
      survive; `if token == expected:` survives on both surfaces
      (`test_a_comparison_survives_on_both_surfaces`; M32, M33 red).
- [ ] **Rev 8 regressions against `main` closed.** `password:\r\nhunter2`,
      `token\r\nabc123def456`, `Authorization:\r\n  s3cr3tvalue`,
      `password:\xa0hunter2` → value `[REDACTED]` on both surfaces, while
      `token:\nthe bearer of` survives
      (`test_crlf_and_no_break_space_are_whitespace_too`);
      `"https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"` raises nothing
      and comes back `https://h.example/?q=[REDACTED]`
      (`test_url_decode_depth_bound_fails_closed`; D1 red); an operator
      pattern and the `ghp_` default apply to a percent-encoded query value
      (`test_operator_pattern_applies_to_a_percent_encoded_query_value`; D2
      red); `{\n  "password": [\n    "hunter2"\n  ]\n}` → each element
      `"[REDACTED]"`, still JSON, on both surfaces
      (`test_pretty_printed_list_value_is_redacted_inside_its_quotes`; Q2, Q3
      red).
- [ ] **Key coverage.** `test_property_every_declared_key_is_redacted_in_every_form`
      derives its keys from the source (31 keys) and checks > 5 000 strings
      with 0 leaks; `{"passwd": "Xk9#mQ2vL"}`, `{"pwd": …}`, `{"private_key":
      …}`, `{"credentials": …}`, `{"auth": …}` → value `[REDACTED]`;
      `{"Authorization": "Xk9 mQ2vL"}` → `{"Authorization": "[REDACTED]"}`;
      `{"Authorization": "(Xk9mQ2vL"}` and `password=(Xk9mQ2vL)` redacted;
      `credentials: include` and `auth=basic` kept; M27 red.
- [ ] **Properties.** `test_property_every_keyed_password_is_redacted_whole`
      checks > 10 000 strings with 0 leaks (measured 10 310);
      `test_property_truncation_never_exposes_a_keyed_password` sweeps
      > 20 000 cuts with > 15 000 inside a value, asserts every value is hit
      at least once, 0 leaks; `test_property_prose_survives_byte_identical`
      2 000 lines byte-identical on both surfaces.
- [ ] `{"password": "hunter2 Bearer test-token"}` → `{"password": "[REDACTED]"}`,
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
- [ ] `{"password": "a\"hunter2"}` → `{"password": "[REDACTED]"}` and
      `{'password': 'a\'hunter2'}` → `{'password': '[REDACTED]'}` on both
      surfaces (`test_a_quoted_value_runs_to_its_closing_quote`).
- [ ] Custom pattern `[A-Za-z0-9+/]{16,}={1,2}` on `basic dXNlcjpwYXNzd29yZA== auth`
      → `basic [REDACTED] auth`; `mykey: abcdef` → `mykey: [REDACTED]`.
- [ ] Every row of the mutation table is RED for its named reason, with a
      non-empty confirmed diff (M37 retired; Q4 equivalent, its guard deleted).
- [ ] `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/`
      clean; full suite green; `check_security_claims.py` green after the
      SECURITY.md edit.

## Non-goals

- Replacing `AUTH_DIAGNOSTIC_SECRET_KEYS` / `AUTH_SECRET_QUERY_KEYS` or
  changing URL query redaction.
- Redacting inside `redact_auth_url` paths (elicitation URLs must stay
  openable — defect 1).
- Redacting the *whole* output before truncating (a full-output redaction
  pass on multi-MB tool results). `process_output` redacts before it cuts
  over a bounded window only (`max_bytes` + 16 KiB, Class B above).
- Redacting a structured result as structure before it is serialised, and
  JSON text inside a string leaf — Consiliency/pmcp#290 (decision (c)).
- Detecting MIME-wrapped base64 payloads as payloads (documented residual FP).
- Entropy (Shannon) scoring; the class-transition score was kept because it
  separates camelCase and hex from random text where entropy thresholds do not.

## Unverified

- **Full suite: verified.** rev 10, run for this plan on `e79d75f` (a
  scratch worktree, npm env vars unset, plain `pytest tests/`, i.e. with
  `addopts` = `not live and not slow`): **4536 passed, 3 skipped, 40
  deselected in 435.27s (0:07:15)**, `DONE-EXIT=0`. The coordinator's run of
  the same tree with `-m 'not live'` on the command line (which replaces
  `addopts` and so also ran the 15 slow cases) reported **4551 passed, 3
  skipped, 25 deselected, 1 error**: 4 551 − 4 536 = 15, the slow tier; the
  one error is **environmental**, a teardown assertion in
  `tests/runtime/harness.py` (`test_workflow_guards.py::TestMutationHelperContract::test_an_unknown_mutant_name_is_refused`:
  the host's live gateway on :3344 went down during the run, `before=3254
  after=None`), not a redaction test. `ruff check`, `ruff format --check`,
  `mypy src/` clean on `e79d75f`.
- **Rev 10 counts that differ slightly from the implementation agent's
  report:** `main`'s URL rows, 8 897 / 1 687 here vs 8 895 / 1 686 reported
  (never-worse holds on both); the slow tier took 195 s here vs 171 s. The
  agent's recorded second-seed run used the previous generator (145 983
  rows) and code; the second seed here (7, 77) is on `e79d75f` and the
  final generator: 0 unaccepted on both tiers.
- History: rev 9: **4370 passed, 3 skipped, 25 deselected in 459.38s (0:07:39)**, `DONE-EXIT=0`, `-m 'not live'`, run by the coordinator on `8e7d98c` (the log names no tree; the count is consistent with +157 tests over rev 8). `ruff check`, `ruff format --check` and `mypy src/` (49 files) clean on `8e7d98c`, run for this plan. Rows M01-M38 were **not** re-run on rev 9; the seven round-3 mutants and D1, D2, Q1-Q3 were.
- **Fuzz numbers in the rev-9 commit messages are the seat's draw, not the committed corpus:** `9b5c86a`/`2855e6c` report `main` breaking 1 005 / 1 276 of the seat's 6 000 documents; on the committed `_json_fuzz_corpus` this plan measured 1 026 / 1 311 (all 6 000) and 358 / 398 (the 3 272 in scope). Rev 9's 4 / 4 reproduces.
- The `977fd49` escape sweep ("0 of 117") was not re-run; the committed `test_no_span_starts_inside_a_json_escape` (24 cases) was, red on rev 8 and green on rev 9.
- History: rev 8: **4213 passed, 3 skipped, 25 deselected in 421.04s (0:07:01)**, `-m 'not live'`, npm env vars unset, on a scratch worktree of `origin/wip/234-redactor-rev8-code` @ `d316364` (the parent of the embedded `56d7f80`; they differ only in comments, a docstring and a no-op check; oracle fixture in place); `ruff check`, `ruff format --check` clean and `uv run mypy src/` Success, 49 source files, on the same tree. Rows M01-M38 were **not** re-run on rev 8 (see Mutation evidence); Q1-Q3, D1, D2 were. History: rev 7: **4200 passed, 3 skipped, 25 deselected in 651.87s (10:51)**, run detached with `-m 'not live'` on the rev-7 tree (the exact `src/` hashes embedded above, with the oracle fixture at `tests/fixtures/redaction_main_oracle.b64`) after the 35-row mutation table; `uv run mypy src/` on the same tree: Success, 49 source files.
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
