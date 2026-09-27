# Detailed plan: secret redaction, additive rules over the redactor's own output (Consiliency/pmcp#234)

> **Revision 17 (2026-09-27): rev 16's board, round 2.** Held again:
> - the floor (0 violations on about 2.8M cases);
> - markers on every surface;
> - the prefilter, and no super-linear regex.
>
> One regression, F1: rev 16 dropped every additive span that overlapped a
> marker. The redactor's keyword rule writes markers inside a strong key's
> value (a key word, then a word), so the rest of such a value showed.
> Rev 17 cuts a span that starts before a marker around the markers,
> instead of dropping it. A span that starts on a marker is still dropped,
> so the query-tail fix stands.
>
> Two disclosures:
> - credentials inside JSON object keys are kept (F2, Residuals);
> - a Bearer value never starts on a whitespace escape, so `Bearer\r\na-b=…`
>   inside a JSON string is left to the base pass (F3). A first attempt to
>   restore rev 15's reading here was quadratic, and the quantifier sweep
>   caught it.
>
> Code: `origin/wip/234-additive` @ `8aae554`, embedded against `main` @
> `260cc1a`.
>
> **Revision 16 (2026-09-27): rev 15's board.** Every seat held the floor
> by construction: the claude seat ran a 2.8M-case differential with no
> floor violation.
>
> The board found five defects in the additive layer itself:
>
> - on the policy surface, markers were swallowed and re-spelled, and a
>   value ran through `&` into the next query parameter;
> - an ambiguous list pattern backtracked cubically (codex);
> - JSON object keys were redacted into collisions (claude);
> - peak memory was high on long JSON strings;
> - the rules missed userinfo behind an upper-case URL scheme.
>
> Each is fixed as a class ("Rev 15 board findings"). A sweep over every
> quantifier of every redactor regex now guards what the work counter cannot
> see.
>
> One reported gap was checked and does not exist: the key prefilter missing
> multi-character case folds (`ß`). Python's `re` does not match those.
> Even so, the prefilter is now the keyword patterns' own alternation under
> their flag, tested against the patterns on every fold variant.
>
> Rev 16's code was `wip/234-additive` @ `e77c691`.
>
> **Revision 15 (2026-09-27): additive only (maintainer decision).**
>
> The redactor runs its own rules unchanged. Rev 10's shape-based and
> separator-anchored rules then run over that redactor's **output**, and can
> only replace more of it with the marker. So the floor holds by
> construction: every piece the redactor removes stays removed.
>
> There is no replay, no suppression predicate and no recorded oracle. The
> redactor's existing false positives stay. The approved exceptions of revs
> 10-14 are dropped and listed under Residuals as possible future,
> separately reviewed changes.
>
> Rev 15's code was `wip/234-additive` @ `ff73fd0`.

## Task

Close Consiliency/pmcp#234. The secret redactor between a downstream server's
text and the agent's context is keyword-anchored. Credentials that carry no
keyword pass through: `AKIA…`, `ghp_…` in prose, `xoxb-…`, JWT-shaped runs
outside the JWT rule's reach, a token in a URL path, a value under a key the
redactor does not list. Add shape-based and separator-anchored rules that
catch them, **without ever redacting less than before**.

Surfaces (unchanged):
- `sanitize_auth_diagnostic` (E);
- `PolicyManager.redact_secrets` (P);
- `process_output` on a string (POs) and on a structured result (POd).

## Rev 16 board findings → rev 17

| id | finding | fix | red on rev 16 (`e77c691`) | green | mutant |
|---|---|---|---|---|---|
| F1 (blocking) | an additive span overlapping a marker was dropped even when it started before it; a strong key's value the redactor partly marked showed the rest (`"Secret [REDACTED] 2024!"`, a Cookie header's hex ticket, a PEM fragment) | a span that starts before a marker is cut around the markers; a span that starts on one is still dropped | derived test (every strong key × every key word the redactor reacts to × three value templates, on E, P, POd and a plain `key: "…"` form): 5 472 failures. Cookie headers (4 header spellings × 4 first cookies × 5 ticket names): the ticket kept in 80 of 80. The PEM block with a marker inside kept a line | 0; 0 of 80; the PEM block redacted | rev 16's merge |
| F2 | credentials inside JSON object keys survive (the other side of rev 15's F2 fix) | disclosed (Residuals) | — | — | — |
| F3 | rev 16's Bearer check (a value never starts on a whitespace escape) stops `Bearer\r\na-b=…` in a JSON string from being redacted by the additive rule; it was not disclosed | kept, and disclosed (Changes, Residuals). The check exists so a whitespace escape is never read as the value. Asking instead whether a run of escapes is followed by a value, as a first rev-17 cut did, rescans the run at every split of the separator. That was quadratic: 35 s at 64 KB of `bearer` plus `\n` escapes in a JSON leaf, caught by the slow tier's quantifier sweep | — | — | a test times the quadratic form (over 0.3 s at 16 KB) and the kept form (under 1 s) |

**Rev 15 against rev 17, on the credential corpora:**
- **Corpora:** the credential corpus, the board rows, half of tier 1, the
  marker texts, keyed forms of 300 random passwords, the derived
  strong-key values and the JSON fuzz: 34 193 texts, on E and P.
- **Result:** rev 17 keeps a piece rev 15 removed in 248 (text, surface)
  cases:
  - 124 are where rev 15 dropped or re-spelled a marker;
  - 80 are where rev 15 swallowed a marker, extending it over the text
    after it. Guarantee 4 forbids that; it is rev 15's query-tail class.
  - The remaining 44 are whitespace escapes (`\u2004`, `\u3000`, …)
    that rev 15's Bearer rule read as a value (F3). None of them is a
    credential.
- **What those 80 include:** 24 credential-shaped pieces, all of one shape.
  The redactor's keyword rule took a scheme word as a key's value, and the
  token follows the marker after a whitespace escape, wrapped in brackets:
  `{"t": "token: bearer\u2009[tawn87Sc]"}` becomes `token:
  [REDACTED]\u2009[tawn87Sc]` on P. They are listed under Residuals.

## Rev 15 board findings → rev 16

| source | finding | fix (the class) | red on rev 15 (`ff73fd0`) | green | mutant |
|---|---|---|---|---|---|
| grok, claude F1 | policy surface: no marker skip, and a value ran through `&`, so a query tail was lost and `%5BREDACTED%5D` re-spelled | spans overlapping a marker are dropped on every surface; a value starting on a marker is skipped; a policy value inside a query ends at `&`/`#` | `redact_secrets("see https://h.example/cb?token=abc123def456&page=2 now")` → `…?token=[REDACTED] now`; the marker check fails on P, POs, POd | `…?token=%5BREDACTED%5D&page=2 now`; the check passes on all four surfaces | rev 15's policy pass |
| codex | `_LIST_BODY`: three `\s*` could split one whitespace run; a missing `]` backtracked cubically | an unambiguous list body; every quantified pattern checked (sweep below) | `tokens: [` + 2 KB of spaces: 2.57 s; the quantifier sweep: 10 of 648 shapes over 0.5 s at 4 KB (~30 s each) | ms; 0 of 798 over the cap at 4 and 64 KB | rev 15's list body |
| claude F2 | JSON object keys redacted into collisions | spans confined to string values | `{"Xk9m…": 1, "Pq7r…": 2}` → `{'[REDACTED]': 2}` | unchanged dict | keys clipped as values |
| claude F3 | slow-input cost understated; ~155/260 MB peak from the per-character string pattern | string patterns unrolled; the residual restated | 1 MB `token_` run: 9.3 s E, 14.5 s POd, peaks ~155/260 MB | 8.8 s E, 8.9 s POd, peaks 39/40 MB | — |
| claude F4 | an upper-case scheme hid userinfo from the URL rule | the additive URL pattern is case-insensitive | `HTTPS://user:s3cr3tpass@h.example/p` kept | `HTTPS://[REDACTED]@h.example/p` | — |
| claude F4 | the 400-character cut can show base text past the base's own window | documented; a differential with the cut added | — | the cut output's stretches occur, in order, in the base's full output | — |
| grok | the prefilter's single-character folds miss `ß`/`ẞ` multi-character folds | checked: Python's `re` (3.10 to 3.14) does not match them, so no key is missed; the prefilter is now the patterns' own alternation, tested on every fold variant | no leak on any surface (`paßword=@hunter2` is kept by the base pass too) | — | — |

## Why the replay-based floor was abandoned

Revs 11-14 replayed the redactor's substitutions with origin tracking, so
that one engine could both reproduce the redactor's removals and add rev 10's
rules, with named predicates suppressing the redactor's false positives.

Each review round found a new super-linear or wrong path in the replay's own
machinery. These were not defects in the rules:
- a marker standing for a whole URL (rev 12);
- a re-encoded URL component standing for all of itself (rev 13);
- a re-spelled port making the host one piece (rev 14, round 4);
- any URL with an upper-case host letter failing alignment and becoming
  wholly `[REDACTED]` (since rev 11).

Its test tier also outgrew CI: the three `test (3.x)` jobs of the draft
integration PR hit the 25-minute limit.

Running the redactor as it is and adding rules over its output needs none of
that machinery, and gives the floor by construction.

## Design

1. **Base pass: the redactor as it is.**
   - `sanitize_auth_diagnostic` calls `_sanitize_base(text)`. That function is
     the body the function had, moved verbatim: the URL rule, Authorization,
     Bearer, the keyword rule through `pmcp.keyword_matcher`, and JWTs.
   - `redact_secrets` runs `_sanitize_base` and then each effective policy
     pattern with its first-separator split, as before.
   - `process_output` is unchanged: it truncates and then calls
     `redact_secrets`.
2. **Additive pass: `pmcp.redaction_additive`, over the base output.** Rev 10's
   rules:
   - separator-anchored keyword rules over a wider key set, keyword lists,
     whitespace keywords with a credential-shaped value, and Bearer and
     Authorization values;
   - URL userinfo, fragment and secret-keyed query values, percent-decoded,
     so an encoded token is judged in its decoded form;
   - shape rules: JWTs, vendor shapes (AWS, Slack, Google), prefixed tokens
     (`sk-…`, `ghp_…`, `glpat-…`), PEM private-key blocks, and high-entropy
     runs;
   - on the policy surface, rev 10's form of each effective default pattern,
     and each operator pattern applied again with a split that keeps base64
     padding whole.

   Every additive replacement writes the marker `[REDACTED]`.
3. **Merge.**
   - All additive spans over the base output are applied in one step.
   - A marker already in the text (`[REDACTED]`, or the URL rule's
     `%5BREDACTED%5D`) is left exactly as it is, on every surface:
     - a span that starts on a marker is dropped, and so is a keyword or
       policy value that starts on one;
     - a span that starts before a marker keeps its reach: each
       non-whitespace stretch of it outside the markers is replaced, and
       each marker stays as written. `{"password": "Secret [REDACTED]
       2024!"}` becomes `{"password": "[REDACTED][REDACTED][REDACTED]"}`;
     - on the policy surface, a value inside a URL query ends at the next
       `&` or `#`.

     Extending a marker over the text after it would only hide that text,
     such as `secret_arn=[REDACTED]:aws:…` or the rest of a query.

     Run again over its own output, the pass keeps every marker and only
     adds. It is monotone, not idempotent: next to a marker it wrote, a
     second pass may mark a scheme word the first pass left.
4. **JSON.** When the base output is a JSON document, every additive span is
   confined to the contents of string **values**, widened to whole escapes.
   It never touches a delimiting quote, a bracket, a separator, a scalar or
   an object key, so two keys can never collapse into one.

## Guarantees and how each is proven

| # | guarantee | proof (tests in `tests/test_redaction_additive.py`) |
|---|---|---|
| 1 | **Floor**: the final output is the base output with markers written over some of it | `_sanitize_base` equals the vendored redactor (`tests/_main_redactor.py`, `main`'s code verbatim) on every tier-1 text, the prose, the credentials and the JSON fuzz. Every stretch of the final output outside a marker occurs, in order, in the base output: tier 1, the board rows of four review rounds, the prose, the credentials and the fuzz. A piece-level differential on all 12 surfaces of tier 1, and a quarter of tier 2 in the slow tier, checks that nothing the redactor removed survives and that no dict turns into a string. Mutant: an additive pass that returns its input unredacted is caught. |
| 2 | **JSON**: a document the base pass keeps valid stays valid | The 1 500-object fuzz, compact and indented, on E and P: wherever the base output parses, the final output parses too. `process_output` keeps every dict the base returned as a dict. A direct check confines a span that crosses a string boundary to the string. Mutant: without the clipping, that document breaks. |
| 3 | **Linear** time and memory of the additive pass, and no regex backtracks | A deterministic work count: every loop and string build adds what it iterates over or copies to `pmcp.redaction_additive.WORK`. Across 19 shapes from four review rounds and the rules' own loops, the count grows linearly at 16, 64 and 256 KB on all four entry points. The tracemalloc peak of the whole call grows linearly at 8, 32 and 128 KB on E and POd. Mutant: asking every resource name per match is caught. The slow tier sweeps a sample of 70 000+ generated shapes on the work count. `main`'s own timing guards (`tests/test_keyword_matcher.py`) pass unchanged, with coverage on. What the counter cannot see, a regex's own backtracking, is swept from each regex's quantifier structure. For every quantifier of every redactor regex (798 shapes), the input is the shortest text that reaches it, one unit repeated, then one of six failing tails. The engine must stay under 1 s at 4 KB (default tier). Every surface and a JSON string leaf must stay under 1/2/5 s at 4/16/64 KB (slow tier). Mutant: rev 15's list body. |
| 4 | **Markers** never split, swallowed, re-spelled or marked again | On all four surfaces (E, P, POs, POd), over URL queries under every policy-default key, a seventh of tier 1 and the board rows: every marker of the base output is still there in order, whole and in its own spelling; no `REDACTED` appears outside a whole marker; and a second additive pass over the output keeps every marker and only adds. Mutant: rev 15's policy pass. |

The rules' purpose is covered as well:
- **Partly-marked values:** for every strong key, every key word the base
  pass reacts to and three value templates, no character of the value
  outside the markers survives on E, P and POd. The same holds for Cookie
  headers carrying a hex ticket and for a PEM block with a marker inside.
- **Credentials:** every entry of rev 10's credential corpus is removed on E
  and P, as is a keyed password drawn from every printable character.
- **Prose:** on the prose corpus and 2 000 generated prose lines, the final
  output equals the base output. The additive rules add no false positive
  of their own there.

**Linear by construction (per function).**
- Each additive pass is one `finditer` over the text, with O(match) work
  per match. The key-qualifier and glued runs in the key patterns are
  bounded (8 segments, 24 characters).
- The three keyword passes are skipped when no key literal occurs. The
  prefilter folds characters as `re.IGNORECASE` does, which a test pins.
- `_in_resource_name` is one binary search per question.
  `widen_over_escapes` reads backslash runs from one precomputed pass.
- An unindented break is found by one reverse search.
- Percent-decoding recurses at most 3 levels, each on a substring.
- The merge is one sort, one sweep and one join. The JSON clipping is one
  `json.loads`, one tokenisation and a binary search per span.

## Residuals

- The redactor's own false positives stay: `token bucket` → `token
  [REDACTED]`, `status_code=[REDACTED]`, and the rest. The additive rules add
  none on the prose corpora.
- **Possible future, separately reviewed changes** (dropped from revs
  10-14). Each would suppress one of the redactor's own false positives:
  - C10: `code` with a plain word or number;
  - C3 / C3a: a whitespace-only separator before a non-credential value;
  - C4 / C5 / C6 / C7 / C8 / C11 / C12: comparisons, suffixed keys, glued
    keys, JSON structure, sentence breaks, prose after Bearer, and
    diagnostic `code` qualifiers;
  - N3 / N4 / N10: identifiers and resource names;
  - N11: a `name=value` pair after a keyword;
  - the four syntax predicates;
  - rev 10's fixes for a flag or bullet after a keyword and a joiner before
    `bearer`.
- Linear but slow: on 1 MB of a joined key run (`token_`…), the additive
  keyword regexes take about 9 s (E and POd, dev0), against 0.14 s for the
  base pass. Other 1 MB shapes: an `a-` run 0.3 s; a URL with many
  `token=` pairs 2.4 s (E) and 2.8 s (POd). Peak memory at 1 MB is about
  40 MB. `process_output` is capped at 50 KB by default, which keeps it
  under a second there. `sanitize_auth_diagnostic` redacts the whole text
  before its 400-character cut, so the diagnostic path can reach these
  times.
- The 400-character cut: when an additive marker is shorter than what it
  replaced, the cut output can show base text past the base pass's own
  400-character window. That text was kept, not removed, by the base pass.
- `Bearer` followed by whitespace escapes and a `name=value` pair inside a
  JSON string (`Bearer\r\na-b=x`) is left to the base pass, which does not
  redact it either: the additive Bearer rule never starts a value on a
  whitespace escape (F3).
- Credentials inside JSON **object keys** are kept
  (`{"cache:Xk9mQ2vLp3RtY7wBaaQ1": 1}`). Redacting keys collapsed maps
  whose keys are random-looking ids into one entry (rev 15's F2). Keys are
  structure, and the base pass does not redact inside them either.
- A value the base pass replaced only in part, where the marker comes first
  and the rest follows it: the additive pass does not extend the marker over
  that rest. A bracketed token after a scheme word the keyword rule took as
  the value is the credential-shaped case (`token: bearer\u2009[tawn87Sc]`
  → `token: [REDACTED]\u2009[tawn87Sc]` on P; 24 cases in the rev 15
  against rev 17 differential).
- Shape false positives (rev 10's class): a short opaque path segment
  (`https://youtu.be/<id>` → `https://youtu.[REDACTED]`), opaque query
  values, cursors and `req_…` ids, and base64 images under 256 characters.
- Truncation: `process_output` truncates before it redacts, as before. A
  cut inside a token can leave its prefix; shape rules still catch a
  prefix that is itself credential-shaped.
- JSON text inside a string leaf is Consiliency/pmcp#290.
- Rev 10's residual table for what the additive rules do not catch stands:
  bare uniform-hex secrets, single runs over 256 characters, and a digitless
  password after a whitespace keyword.

## History (condensed)

| rev | design | what the next review found |
|---|---|---|
| 1-10 | a re-implementation of the rules with shape rules added | each round, inputs where it redacted less than the redactor (axes outside the generator) |
| 11-14 | the redactor's rules replayed as a floor, rev 10 on top, suppressions by predicate | the floor held; the replay's own machinery kept producing super-linear and wrong paths, and CI outgrew its budget |
| **15** | **the redactor as it is, rev 10's rules over its output** | — |

## Changes

- `src/pmcp/redaction_additive.py` (new): rev 10's rules (quoted-string
  patterns unrolled, the list body unambiguous, the URL pattern
  case-insensitive), the merge that leaves markers whole, the JSON clipping
  to string values, the key prefilter and the work counter.
- `src/pmcp/auth.py`: `_sanitize_base` (the old body, moved);
  `sanitize_auth_diagnostic` = base, then additive, then the cut.
- `src/pmcp/policy/policy.py`: `redact_secrets` = base, then patterns, then
  additive (the engine's rules plus rev 10's forms of the defaults, with the
  linear `_value_separator`, a marker skip and a stop at `&`/`#` inside a
  query). `DEFAULT_REDACTION_PATTERNS` is unchanged.
- Tests:
  - `tests/test_redaction_additive.py` (new);
  - `tests/_main_redactor.py` (the redactor, vendored verbatim);
  - `tests/_redaction_grammar.py` (rev 10's corpus generator, without its
    classifier);
  - `tests/_redaction_shapes.py` (the shape generators, including the
    quantifier-structure family);
  - `pyproject.toml`: a `slow` marker, excluded by `addopts`.
- No recorded oracle fixture and no `redaction_floor.py`.

Documentation (implementer):
- a CHANGELOG `### Security` entry for the added rules;
- SECURITY.md's redaction claim, widened;
- that `pytest -m slow` runs the slow tier.

## Verification

```bash
uv sync --all-extras -p 3.10
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
  uv run pytest -m 'not live and not slow' -q      # default tier (CI)
env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
  uv run pytest -m 'slow and not live' -q          # slow tier
```

**Measured on the embedded code (`8aae554`)**, as read from the logs:

| check | result |
|---|---|
| `ruff check src/ tests/` | All checks passed! |
| `ruff format --check src/ tests/` | 170 files already formatted |
| `mypy src/` | Success: no issues found in 52 source files |
| default tier (dev0) | **4680 passed, 3 skipped, 80 deselected in 517.44s (0:08:37)** |
| slow tier (dev0) | **55 passed, 4708 deselected in 383.52s (0:06:23)** |

How these ran:
- Both tiers ran with `npm_config_cache`, `npm_config_store_dir` and
  `pnpm_config_store_dir` unset.
- The two 60 s tests in the default tier are
  `tests/test_progressive_disclosure.py`'s `test_invoke_query_docs` and
  `test_invoke_query_docs_conceptual`, which this plan does not touch.
- The slow tier's longest case is the tier-2 floor differential (144 s).
- One earlier slow run on the first rev-17 cut failed three
  quantifier-sweep chunks. That was the quadratic Bearer lookahead (F3);
  it was reverted before these runs.

**CI cost.** Rev 15 was timed on GitHub runners through a draft
do-not-merge PR (#309, closed), running `pytest tests/ --cov` on the default
tier:

| run | commit | `test (3.10)` | `test (3.11)` | `test (3.12)` |
|---|---|---|---|---|
| rev 15 | `ff73fd0` | 14m20s | 12m23s | 12m59s |
| `main` for comparison | `260cc1a` | 9m08s | 14m23s | 8m27s |

Rev 17's default tier was not re-timed on runners: locally it took 8:37,
against rev 15's 8:45 and rev 16's 8:20. It has not grown materially.

**Embedding proof** (run for this plan):

- `origin/main` was re-fetched: still `260cc1a`.
- The patch extracted from this file with the commands under "Patch
  against `main`" is `cmp`-equal to the generated diff.
- `git apply --check` and `git apply` succeeded on a fresh worktree of
  `260cc1a`.
- The result matches `wip/234-additive` at `8aae554`: `pyproject.toml` is
  `cmp`-identical, and `diff -rq` of `src/` and `tests/` shows no
  differences.
- On that tree (`pmcp.__file__` printed from it):
  - `ruff check`: All checks passed!
  - `ruff format --check`: 170 files already formatted
  - `mypy src/`: no issues in 52 source files
  - default tier of `test_redaction_additive.py`,
    `test_keyword_matcher.py`, `test_auth.py`, `test_policy.py`,
    `test_project_source_consent_policy.py` and
    `test_trust_boundaries_e2e.py`: **380 passed, 55 deselected in 55.47s**

## Acceptance criteria

1. The embedded patch applies to `main` @ `260cc1a` and reproduces the
   branch's files.
2. `ruff check`, `ruff format --check` and `mypy src/` are clean.
3. The default tier and the slow tier are green. The `test (3.x)` jobs finish
   well inside 25 minutes on GitHub runners.
4. Guarantees 1-4 hold as tested. Each mutant is caught.
5. `tests/test_keyword_matcher.py` passes unmodified, with coverage on.
6. Every credential-corpus entry is removed. The prose corpora equal the base
   output.

## Non-goals

- Changing the redactor's own rules or suppressing any of its false
  positives. Each is a separately reviewed change (Residuals).
- Redacting before truncating in `process_output`.
- JSON text inside a string leaf (Consiliency/pmcp#290).

## Unverified

- Timings are from dev0 and from one CI run.
- The slow tier is not run in CI.

## Execution Policy

- execute: effort=medium, reason=the patch is complete and measured, but it
  is security-sensitive: re-run the differential and the mutants on the
  implementer's tree.


## Patch against `main` @ `260cc1a`

This is `git diff --full-index 260cc1a 8aae554`, the whole change.

`sha256` of the patched files (first 16 hex digits):

| file | sha256 |
|---|---|
| `auth.py` | `4976402fabcf92b7` |
| `policy.py` | `d1081ce93ba64da4` |
| `redaction_additive.py` | `b02426ca33e0dc84` |
| `keyword_matcher.py` | `4c256b3d813bed15` (unchanged) |

To apply, on a fresh worktree of `main`:

```bash
sed -n '/^<!-- PATCH-BEGIN -->$/,/^<!-- PATCH-END -->$/p' <this plan> \
  | sed '1,2d;$d' | sed '$d' > additive.patch
git apply --check additive.patch && git apply additive.patch
```

<!-- PATCH-BEGIN -->
```diff
diff --git a/pyproject.toml b/pyproject.toml
index 500a26dd9217b1dc365bfb098699316d289498f6..ef89f73e4556d6af97fc26f429eb580da2d01126 100644
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
+    "slow: opt-in long-running tests (the full redaction differential and timing sweep; run with -m slow)",
     "timeout(seconds): expected maximum runtime for slow opt-in tests",
     "real_cwd: run from the invocation directory, opting out of the autouse isolate_cwd fixture (only for tests whose subject IS the working directory)",
 ]
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index 0e58d07734b410ce37d65f463f97437f186647c9..ccb5e365b4dfe2ce5ac18ca687dee11ce4178b38 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -20,6 +20,7 @@ import jwt
 from jwt import PyJWKSet
 
 from pmcp.keyword_matcher import key_start_pattern, redact_keyword_values
+from pmcp.redaction_additive import redact_additive
 from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo
 
 
@@ -578,8 +579,20 @@ def sanitize_url_elicitation_url(
 
 
 def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
-    """Return a display-safe diagnostic string for auth failures."""
-    text = str(value)
+    """Return a display-safe diagnostic string for auth failures.
+
+    The redactor's own rules run first, unchanged (`_sanitize_base`); the
+    additive rules (`pmcp.redaction_additive`) then run over that output and
+    can only replace more of it with the marker (Consiliency/pmcp#234). The
+    cut is taken last, as before.
+    """
+    text = redact_additive(_sanitize_base(str(value)))
+    return text if max_length is None else text[:max_length]
+
+
+def _sanitize_base(text: str) -> str:
+    """The redactor's own rules, in order: URLs, Authorization, Bearer, the
+    keyword rule, JWTs."""
 
     def redact_url_match(match: re.Match[str]) -> str:
         whole = match.group(0)
@@ -595,7 +608,7 @@ def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) ->
     text = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
     text = redact_keyword_values(text, _KEYWORD_KEY_START)
     text = _JWT_RE.sub("[REDACTED]", text)
-    return text if max_length is None else text[:max_length]
+    return text
 
 
 def _parse_www_auth_params(raw: str) -> dict[str, str]:
diff --git a/src/pmcp/policy/policy.py b/src/pmcp/policy/policy.py
index cac27021c46dcd6c2a066fa779ccf58046bc0c94..56704e3f8a2b196bae5ca3d97a15b3a912f46b0e 100644
--- a/src/pmcp/policy/policy.py
+++ b/src/pmcp/policy/policy.py
@@ -2,6 +2,7 @@
 
 from __future__ import annotations
 
+import bisect
 import fnmatch
 import hashlib
 import json
@@ -22,7 +23,16 @@ from pmcp.types import (
     ServerPolicy,
     ToolPolicy,
 )
-from pmcp.auth import sanitize_auth_diagnostic
+from pmcp.auth import _sanitize_base
+from pmcp.redaction_additive import (
+    REDACTED,
+    URL_RE,
+    Span,
+    redact_additive,
+    starts_with_marker,
+    widen_over_escapes,
+    work,
+)
 
 if TYPE_CHECKING:
     # Annotation only. `pmcp.manifest`'s package `__init__` imports the loader and
@@ -55,6 +65,71 @@ DEFAULT_REDACTION_PATTERNS = [
     r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
 ]
 
+#: Rev 10's forms of the defaults, entry for entry (Consiliency/pmcp#234):
+#: applied IN ADDITION to the default they stand beside, whenever that
+#: default is effective, over the output the defaults already redacted. They
+#: only add redactions.
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
+def _url_query_ranges(text: str) -> list[tuple[int, int]]:
+    """Where each URL's query runs in ``text``: from after its `?` to the
+    URL's end (disjoint, ascending)."""
+    work(len(text))
+    ranges = []
+    for match in URL_RE.finditer(text):
+        mark = text.find("?", match.start(), match.end())
+        if mark >= 0:
+            ranges.append((mark + 1, match.end()))
+    return ranges
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
@@ -694,8 +769,16 @@ class PolicyManager:
         return (truncated_str, True, original_size)
 
     def redact_secrets(self, output: str) -> str:
-        """Redact secrets from output."""
-        result = sanitize_auth_diagnostic(output, max_length=None)
+        """Redact secrets from output.
+
+        The redactor's own rules run first, unchanged: the engine's
+        (`_sanitize_base`), then each effective pattern, split at its first
+        `:`/`=`. The additive rules then run over that output
+        (Consiliency/pmcp#234): the engine's, and rev 10's forms of the
+        effective patterns (`_additive_spans`). They can only replace more of
+        it with the marker.
+        """
+        result = _sanitize_base(output)
 
         for regex in self._redaction_regexes:
 
@@ -709,7 +792,64 @@ class PolicyManager:
 
             result = regex.sub(replace_match, result)
 
-        return result
+        return self._redact_additive(result)
+
+    def _redact_additive(self, text: str) -> str:
+        """The additive pass alone, over text the redactor's own rules have
+        already redacted."""
+        return redact_additive(
+            text, covers=self._pattern_matches, extra=self._additive_spans(text)
+        )
+
+    def _additive_regexes(self) -> list[re.Pattern[str]]:
+        """The additive form of each effective pattern: rev 10's form of a
+        default (`_ADDITIVE_DEFAULT_PATTERNS`), an operator's pattern as it
+        is -- applied with `_value_separator`, which keeps base64 padding
+        whole where the first-separator split does not."""
+        return [
+            _ADDITIVE_FOR_DEFAULT.get(r.pattern, r) for r in self._redaction_regexes
+        ]
+
+    def _pattern_matches(self, text: str) -> bool:
+        """Does any effective or additive pattern match ``text``? Asked of a
+        percent-decoded query value, so an encoded token cannot evade a
+        pattern written for its decoded shape."""
+        return any(
+            regex.search(text)
+            for regex in (*self._redaction_regexes, *self._additive_regexes())
+        )
+
+    def _additive_spans(self, text: str) -> list[Span]:
+        """Spans of the additive form of each effective pattern, over the
+        output the patterns already redacted. Like the engine's additive
+        rules, a value that starts on a marker is left alone (the redactor
+        already took it; running on would only swallow what follows), and
+        inside a URL's query a value ends at `&` or `#` -- the next
+        parameter is not part of it."""
+        spans: list[Span] = []
+        query_ranges = _url_query_ranges(text)
+        query_starts = [start for start, _ in query_ranges]
+        for regex in self._additive_regexes():
+            work(len(text))  # the pattern's scan
+            for match in regex.finditer(text):
+                full_match = match.group(0)
+                work(2 * len(full_match) + 1)  # the match, the split
+                split = _value_separator(full_match)
+                start = match.start() + split + 1 if split >= 0 else match.start()
+                while start < match.end() and text[start].isspace():
+                    start += 1
+                end = match.end()
+                if starts_with_marker(text[start:end]):
+                    continue
+                i = bisect.bisect_right(query_starts, start) - 1
+                if i >= 0 and start < query_ranges[i][1]:
+                    for stop in "&#":
+                        cut = text.find(stop, start, end)
+                        if cut >= 0:
+                            end = cut
+                if start < end:
+                    spans.append((start, end, REDACTED))
+        return widen_over_escapes(text, spans)
 
     def process_output(
         self,
diff --git a/src/pmcp/redaction_additive.py b/src/pmcp/redaction_additive.py
new file mode 100644
index 0000000000000000000000000000000000000000..a97347ecf51e019f144fe0cb34a2e9224f54e7c1
--- /dev/null
+++ b/src/pmcp/redaction_additive.py
@@ -0,0 +1,1285 @@
+"""The additive redaction rules (Consiliency/pmcp#234).
+
+`sanitize_auth_diagnostic` and `PolicyManager.redact_secrets` first run the
+redactor exactly as it was (its URL, Authorization, Bearer, keyword and JWT
+rules, then the policy patterns), and then apply these rules to that
+redactor's OUTPUT. A rule here can only replace text with the redaction
+marker; it never restores anything. So every piece the redactor removed
+stays removed, by construction, and these rules can only add.
+
+What they add: separator-anchored keyword rules over a wider key set,
+keyword lists, whitespace keywords with a credential-shaped value, Bearer
+and Authorization values, secrets in URL query values (percent-decoded),
+and shapes that need no keyword -- JWTs, vendor token shapes, prefixed
+tokens, PEM private-key blocks and high-entropy runs.
+
+Three guarantees, each tested (`tests/test_redaction_additive.py`):
+
+* the floor above;
+* JSON: when the text is a JSON document, a replacement is confined to
+  string contents, whole escapes at a time, so a document stays a
+  document (`_clip_to_json_strings`);
+* linear time and memory: every loop and string build adds what it
+  iterates over or copies to a test-only work counter (`WORK`).
+"""
+
+from __future__ import annotations
+
+import bisect
+import json
+import re
+from collections.abc import Callable, Iterator
+from urllib.parse import unquote
+
+#: Test-only work counter. None in production; the complexity tests set it to
+#: `[0]`, and every loop and string build below adds what it iterates over
+#: or copies, so linear time is asserted on a deterministic count.
+WORK: list[int] | None = None
+
+
+def work(amount: int) -> None:
+    if WORK is not None:
+        WORK[0] += amount
+
+
+def unindented_break(text: str) -> bool:
+    """Does ``text`` hold a line break with no space, tab or no-break space
+    after it? The last break decides; one reverse search."""
+    work(len(text))
+    last = max(text.rfind("\r"), text.rfind("\n"))
+    return last >= 0 and not any(c in " \t\xa0" for c in text[last + 1 :])
+
+
+#: The query keys whose values the URL rule redacts (the same set as
+#: `pmcp.auth.AUTH_SECRET_QUERY_KEYS`; a test pins them equal).
+QUERY_SECRET_KEYS = {
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
+
+ADDITIVE_SECRET_KEYS = {
+    "access_token",
+    "api_key",
+    "apikey",
+    "assertion",
+    "auth",
+    "aws_access",
+    "aws_secret",
+    "client_secret",
+    "code",
+    "cookie",
+    "credential",
+    "credentials",
+    "id_token",
+    "jwt",
+    "passwd",
+    "password",
+    "private_key",
+    "pwd",
+    "refresh_token",
+    "saml",
+    "secret",
+    "secret_access_key",
+    "session",
+    "set-cookie",
+    "sid",
+    "tenant-id",
+    "tenant_id",
+    "token",
+}
+
+#: Keys that name a credential only sometimes. A plain word or number after
+#: them is kept: `credentials: include` (a fetch mode), `auth=basic`,
+#: `auth: none`, `{"code": -32601}` (every JSON-RPC error), `{"code":
+#: "not_found"}`. Anything else -- a token, `user:pass`, a path -- is
+#: redacted. Every other key in `ADDITIVE_SECRET_KEYS` is strong: its
+#: value is redacted whatever it looks like. `tests/test_redaction.py`
+#: derives its key-coverage property from both sets, so a key named here but
+#: not redacted in every form fails that test.
+WEAK_SECRET_KEYS = frozenset({"auth", "code", "credential", "credentials"})
+
+_JWT_RE = re.compile(
+    r"(?<![A-Za-z0-9_-])"
+    r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
+    r"(?![A-Za-z0-9_-])"
+)
+
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
+    test-only work counter, `work`)."""
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
+            *sorted(re.escape(key) for key in ADDITIVE_SECRET_KEYS),
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
+    + r"(?P<value>\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"(?![A-Za-z0-9_])|'[^'\\\n]*(?:\\.[^'\\\n]*)*'(?![A-Za-z0-9_])"
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
+_LIST_ELEMENT = r"(?:\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"|'[^'\\\n]*(?:\\.[^'\\\n]*)*'|-?[0-9][0-9.eE+-]*|null|true|false)"
+#: Unambiguous: each run of whitespace has exactly one place to go (a
+#: missing `]` backtracks over it once, not over every way of splitting it
+#: between three `\s*`, which was cubic -- rev 15's board).
+_LIST_BODY = (
+    r"(?P<list>\[\s*(?:"
+    + _LIST_ELEMENT
+    + r"\s*(?:,\s*"
+    + _LIST_ELEMENT
+    + r"\s*)*(?:,\s*)?)?\])"
+)
+_KEYWORD_LIST_RE = re.compile(
+    _KEYWORD_KEY_SEP + _LIST_BODY,
+    re.IGNORECASE,
+)
+_QUOTED_RE = re.compile(
+    r"\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"|'[^'\\\n]*(?:\\.[^'\\\n]*)*'"
+)
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
+    # a value never starts on a whitespace escape: the separator's own `+`
+    # would give one back and read it as the value (`Bearer\u3000[...]`).
+    # One character of lookahead; asking whether a run of escapes is
+    # followed by a value instead rescanned the run at every split of the
+    # separator -- quadratic on `bearer` + many `\n` escapes, which the
+    # quantifier sweep caught. So `Bearer\r\na-b=x` inside a JSON string
+    # (a pair after the scheme, escapes between) is left to the base pass.
+    r"(?!" + _JSON_SPACE_ESCAPE + r")"
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
+    r"(?:(?P<quoted>\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"(?![A-Za-z0-9_])|'[^'\\\n]*(?:\\.[^'\\\n]*)*'(?![A-Za-z0-9_]))"
+    r"|(?:(?:bearer|basic|digest|negotiate|ntlm|token)"
+    r"(?:[^\S\r\n]|" + _JSON_SPACE_ESCAPE + r")+)?"
+    r"[\"'(\[{<](?P<inner>[^\s,;\"'()\[\]{}<>\\]+)[\"')\]}>]"
+    r"|(?P<bare>(?![\[{])(?:(?:bearer|basic|digest|negotiate|ntlm|token)[^\S\r\n]+)?"
+    r"[^\s,;\"']*[^\s,;\"')\]}\\]))",
+    re.IGNORECASE,
+)
+
+#: A URL in free text; trailing sentence punctuation is handed back.
+URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
+
+
+#: A separator whose (last) line break is followed by no indentation:
+#: `unindented_break`, one reverse search (a regex
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
+def starts_with_marker(value: str) -> bool:
+    """A value the redactor before these rules already replaced: extending
+    the marker over what follows it only hides more of the text around it
+    (`secret_arn=[REDACTED]:aws:...`)."""
+    return value.lstrip("\"'").startswith(("[REDACTED]", "%5BREDACTED%5D"))
+
+
+def _keyword_sep_spans(text: str) -> list[Span]:
+    work(len(text))  # the pass's scan
+    spans: list[Span] = []
+    in_resource_name = _in_resource_name(text)
+    for match in _counted(_KEYWORD_SEP_RE.finditer(text)):
+        if in_resource_name(match.start()):
+            continue  # `arn:…:secret:Name` names a secret, it is not one
+        if starts_with_marker(match.group("value")):
+            continue  # the redactor before these rules already took it
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
+        and not starts_with_marker(match.group("value"))
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
+    `QUERY_SECRET_KEYS` key (`[REDACTED]`), and the fragment (dropped).
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
+        # the userinfo, as a marker: the additive rules only ever write one
+        spans.append((base + netloc_start, base + at, REDACTED))
+    fragment = raw_url.find("#")
+    if fragment >= 0:
+        spans.append((base + fragment + 1, base + len(raw_url), REDACTED))
+    query_start = raw_url.find("?")
+    if query_start >= 0:
+        query_end = fragment if fragment > query_start else len(raw_url)
+        position = query_start + 1
+        for pair in raw_url[query_start + 1 : query_end].split("&"):
+            key, equals, value = pair.partition("=")
+            value_start = position + len(key) + 1
+            if equals and value and unquote(key).lower() in QUERY_SECRET_KEYS:
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
+    for match in _counted(URL_RE.finditer(text)):
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
+    text: str, *, _depth: int = 0, covers: Covers | None = None
+) -> list[Span]:
+    """Every redaction the additive rules would make to ``text``, as spans
+    over it. Each rule reads the same, unmodified ``text``; the order of the
+    list does not matter (`apply_redaction_spans` merges overlaps).
+    ``covers`` lets the policy surface add its own patterns to the question
+    asked of a percent-decoded query value."""
+    return _additive_spans(text, _depth, covers)
+
+
+#: Any key word, as the keyword patterns read it: the same alternation under
+#: the same flag (`re.IGNORECASE`), so every case fold the patterns apply is
+#: applied here too -- it is their `name` group on its own.
+_ANY_KEY_RE = re.compile(rf"(?:{_secret_key_alternation()})", re.IGNORECASE)
+
+
+def _may_hold_a_key(text: str) -> bool:
+    """A necessary condition for a keyword match: the patterns' own key
+    alternation occurs somewhere (one search)."""
+    work(len(text))
+    return _ANY_KEY_RE.search(text) is not None
+
+
+def _additive_spans(text: str, depth: int, covers: Covers | None) -> list[Span]:
+    """The additive rules, each over the same text. The three keyword
+    passes need a key word in the text; when there is none they are skipped
+    (their patterns try a qualifier at every position, which costs a scan
+    of the whole text for nothing)."""
+    keyed = _may_hold_a_key(text)
+    spans = [
+        *_url_spans(text, depth, covers),
+        *(_keyword_sep_spans(text) if keyed else ()),
+        *(_keyword_list_spans(text) if keyed else ()),
+        *_authorization_spans(text),
+        *_bearer_spans(text),
+        *(_keyword_ws_spans(text) if keyed else ()),
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
+#: The redaction marker as the text may already hold it.
+_MARKERS = (REDACTED, "%5BREDACTED%5D")
+_MARKER_RE = re.compile(r"\[REDACTED\]|%5BREDACTED%5D")
+#: Replacements that cover whatever they contain.
+_COVERING = frozenset({*_MARKERS, ""})
+
+
+def merge_redaction_spans(text: str, spans: list[Span]) -> list[Span]:
+    """The disjoint spans `apply_redaction_spans` applies, ascending.
+
+    Every marker already in the text -- `[REDACTED]`, and the URL rule's
+    `%5BREDACTED%5D` -- is left exactly as it is:
+
+    * a span that starts ON a marker is dropped: it reads a value the
+      redactor already replaced, and extending the marker would only hide
+      the text after it (the rest of a query);
+    * a span that starts BEFORE a marker keeps its reach: each stretch of it
+      outside the markers it overlaps is replaced, and each marker stays as
+      written (`{"password": "Secret [REDACTED] 2024!"}` -- the redactor's
+      keyword rule wrote a marker inside a strong key's value -- becomes
+      `{"password": "[REDACTED][REDACTED][REDACTED]"}`; rev 16 dropped the
+      whole span and left `Secret` and `2024!`). A stretch of only
+      whitespace between markers is left.
+
+    So a marker is never split, swallowed or re-spelled; run again over its
+    own output, the pass keeps every marker and only adds. The rest merge: overlapping or nested spans
+    become one `[REDACTED]`, except that a span inside one whose replacement
+    covers it (the marker, or the empty string) is dropped.
+    """
+    work(len(text) + len(spans) * max(1, len(spans).bit_length()))  # scan, sort
+    markers = [m.span() for m in _MARKER_RE.finditer(text)]
+    marker_starts = [start for start, _ in markers]
+    pieces: list[Span] = []
+    for start, end, replacement in spans:
+        i = bisect.bisect_right(marker_starts, start) - 1
+        if i >= 0 and markers[i][1] > start:
+            continue  # starts on a marker
+        position = start
+        i += 1
+        while i < len(markers) and markers[i][0] < end:
+            work(1)
+            if markers[i][0] > position:
+                pieces.append((position, markers[i][0], replacement))
+            position = max(position, markers[i][1])
+            i += 1
+        if position < end:
+            pieces.append((position, end, replacement))
+    return _merge_unmarked(text, pieces)
+
+
+def _merge_unmarked(text: str, pieces: list[Span]) -> list[Span]:
+    """Merge spans that overlap no marker (see `merge_redaction_spans`)."""
+    ordered = sorted(
+        (
+            piece
+            for piece in pieces
+            if piece[0] < piece[1] and not text[piece[0] : piece[1]].isspace()
+        ),
+        key=lambda span: (span[0], -span[1], span[2] != REDACTED),
+    )
+    merged: list[Span] = []
+    for start, end, replacement in ordered:
+        if merged and start < merged[-1][1]:
+            previous_start, previous_end, previous_replacement = merged[-1]
+            if end <= previous_end and previous_replacement in _COVERING:
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
+
+# --------------------------------------------------------------- JSON text
+
+#: A JSON string token, the loop unrolled: a run of plain characters is one
+#: step, not one per character (the per-character alternation kept engine
+#: state for every character of a long string).
+_JSON_STRING_RE = re.compile(r"\"[^\"\\]*(?:\\.[^\"\\]*)*\"")
+_JSON_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\.", re.DOTALL)
+
+
+def _is_object_key(text: str, end: int) -> bool:
+    """Is the string token ending at ``end`` an object key (the next
+    non-whitespace character is `:`)?"""
+    rest = end
+    while rest < len(text) and text[rest] in " \t\r\n":
+        rest += 1
+    return rest < len(text) and text[rest] == ":"
+
+
+def _clip_to_json_strings(text: str, spans: list[Span]) -> list[Span]:
+    """When ``text`` is a JSON document, confine every span to the contents
+    of string VALUES, whole escapes at a time: a replacement then never
+    touches a delimiting quote, a bracket, a separator, a bare scalar or an
+    object key, and never splits an escape. So the document stays a
+    document, and no two keys of an object can collide into one (rev 15's
+    board: random-looking keys redacted to the same marker collapsed a map
+    to its last entry). Anything a span covered outside a string value is
+    left as it is."""
+    head = text.lstrip()[:1]
+    if head not in ("{", "[", '"') or not spans:
+        return spans
+    try:
+        json.loads(text)
+    except (ValueError, RecursionError):
+        return spans
+    work(3 * len(text))
+    values: list[tuple[int, int]] = []
+    for match in _JSON_STRING_RE.finditer(text):
+        start, end = match.span()
+        if _is_object_key(text, end):
+            continue
+        values.append((start, end))
+    starts = [start for start, _ in values]
+    # a position inside a string is a boundary unless it is inside an escape
+    # (backslashes only occur in strings in a valid document)
+    boundary = bytearray(b"\x01") * (len(text) + 1)
+    for escape in _JSON_ESCAPE_RE.finditer(text):
+        boundary[escape.start() + 1 : escape.end()] = bytes(
+            escape.end() - escape.start() - 1
+        )
+    clipped: list[Span] = []
+    for a, b, replacement in spans:
+        i = max(0, bisect.bisect_right(starts, a) - 1)
+        while i < len(values) and values[i][0] < b:
+            start, end = values[i]
+            low, high = max(a, start + 1), min(b, end - 1)
+            work(1)
+            if low < high:
+                while not boundary[low]:
+                    low -= 1
+                while not boundary[high]:
+                    high += 1
+                clipped.append((low, high, replacement))
+            i += 1
+    return clipped
+
+
+def redact_additive(
+    text: str, *, covers: Covers | None = None, extra: list[Span] | None = None
+) -> str:
+    """Apply the additive rules (and ``extra`` spans) to ``text``: the
+    output of the redactor that ran before them."""
+    spans = collect_redaction_spans(text, covers=covers)
+    if extra:
+        spans.extend(extra)
+    return apply_redaction_spans(text, _clip_to_json_strings(text, spans))
diff --git a/tests/_main_redactor.py b/tests/_main_redactor.py
new file mode 100644
index 0000000000000000000000000000000000000000..ffa2f6e202d64db940da68b275e4c34e0ddbd321
--- /dev/null
+++ b/tests/_main_redactor.py
@@ -0,0 +1,146 @@
+"""The redactor's own rules, vendored VERBATIM from `origin/main` (260cc1a) for the additive-rule tests
+(Consiliency/pmcp#234): `origin/main`'s `sanitize_auth_diagnostic` and
+`PolicyManager.redact_secrets` as they are, so the differential compares the
+product with an independent copy of what ran before the additive rules. The
+keyword rule runs through `pmcp.keyword_matcher`, as on `main`.
+"""
+
+# ruff: noqa: E501
+from __future__ import annotations
+
+import re
+from urllib.parse import parse_qsl, quote, urlparse, urlunparse
+
+from pmcp.keyword_matcher import key_start_pattern, redact_keyword_values
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
+_KEYWORD_KEY_START = key_start_pattern(AUTH_DIAGNOSTIC_SECRET_KEYS)
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
+        whole = match.group(0)
+        raw_url = whole.rstrip(").,;")  # trailing sentence punctuation, in one pass
+        return redact_auth_url(raw_url) + whole[len(raw_url) :]
+
+    text = re.sub(r"https?://[^\s\"'<>]+", redact_url_match, text)
+    text = re.sub(
+        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
+        r"\1[REDACTED]",
+        text,
+    )
+    text = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
+    text = redact_keyword_values(text, _KEYWORD_KEY_START)
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
+    result = sanitize_auth_diagnostic(output, max_length=None)
+    if redaction_regexes is None:
+        redaction_regexes = compiled_default_patterns()
+    for regex in redaction_regexes:
+
+        def replace_match(match: re.Match[str]) -> str:
+            full_match = match.group(0)
+            for i, char in enumerate(full_match):
+                if char in ":=":
+                    return full_match[: i + 1] + " [REDACTED]"
+            return "[REDACTED]"
+
+        result = regex.sub(replace_match, result)
+    return result
diff --git a/tests/_redaction_grammar.py b/tests/_redaction_grammar.py
new file mode 100644
index 0000000000000000000000000000000000000000..b87368d77d02649381ed9a8411544601935c882a
--- /dev/null
+++ b/tests/_redaction_grammar.py
@@ -0,0 +1,927 @@
+"""A corpus derived from the redactor's own grammar (Consiliency/pmcp#234).
+
+The differential in `tests/test_redaction_additive.py` compares the redactor
+with and without the additive rules over this corpus. It is derived from the
+grammar of the rules that run first -- their keyword, Authorization, Bearer
+and URL rules, the policy defaults, and what `json.dumps` emits -- so an
+axis cannot be missing because nobody thought of it.
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
+How it runs: tier 1 is in the default suite; a quarter of tier 2 is
+marked `slow` (`pytest -m slow`), which CI does not run. Stdlib only.
+"""
+
+from __future__ import annotations
+
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
diff --git a/tests/_redaction_shapes.py b/tests/_redaction_shapes.py
new file mode 100644
index 0000000000000000000000000000000000000000..cd149c199d61ad3b81c62dd3150938084e65f1f6
--- /dev/null
+++ b/tests/_redaction_shapes.py
@@ -0,0 +1,457 @@
+"""Adversarial input shapes derived from the redactor's own regular
+expressions (Consiliency/pmcp#234).
+
+A super-linear path in a regex or a scan needs an input the path can consume
+over and over and then fail on. So each pattern is parsed (`re._parser`) and
+every piece a quantifier can repeat -- a literal run, a representative of
+each character class, each key word of an alternation -- becomes a unit;
+each unit is repeated to the target length, with each of a few failing tails
+and after each of the redactor's trigger words. The timing sweep in
+`tests/test_redaction_additive.py` runs every shape through every public entry
+point at growing sizes and asserts the growth is linear.
+
+Stdlib only.
+"""
+
+from __future__ import annotations
+
+import re
+from collections.abc import Callable, Iterable, Iterator
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
+    import pmcp.redaction_additive
+
+    found: list[re.Pattern[str]] = []
+    for module in (
+        pmcp.redaction_additive,
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
+
+
+# ------------------------------------------------- from quantifier structure
+#
+# A regex backtracks super-linearly where one run of text can be divided in
+# many ways between quantifiers (adjacent or nested quantifiers over
+# overlapping classes, an optional element between repeated ones), and a
+# later element then fails. So for every quantifier of every pattern the
+# fourth family builds: the shortest text that brings the pattern to that
+# quantifier, one unit the quantifier repeats, many times, and a tail that
+# fails (rev 15's board: `tokens: [` + spaces with no `]` was cubic).
+
+
+def _sample(tree: Iterable[tuple[object, object]]) -> str:
+    """A short text the subtree matches (assertions contribute nothing)."""
+    out = []
+    for op, arg in tree:
+        if op is sre_constants.LITERAL:
+            out.append(chr(arg))  # type: ignore[arg-type]
+        elif op is sre_constants.IN:
+            chars = _class_chars(arg)  # type: ignore[arg-type]
+            out.append(chars[:1] or "a")
+        elif op is sre_constants.CATEGORY:
+            out.append(_CATEGORY_CHARS.get(arg, "a")[:1])  # type: ignore[arg-type]
+        elif op is sre_constants.ANY:
+            out.append("a")
+        elif op in (sre_constants.MAX_REPEAT, sre_constants.MIN_REPEAT):
+            low = arg[0]  # type: ignore[index]
+            out.append(_sample(arg[2]) * max(low, 0))  # type: ignore[index]
+        elif op is sre_constants.SUBPATTERN:
+            out.append(_sample(arg[-1]))  # type: ignore[index]
+        elif op is sre_constants.BRANCH:
+            out.append(_sample(arg[1][0]))  # type: ignore[index]
+    return "".join(out)
+
+
+def _quantifier_leads(
+    tree: list[tuple[object, object]], prefix: str
+) -> Iterator[tuple[str, str]]:
+    """(text that brings the pattern to a quantifier, one unit it repeats),
+    for every quantifier in ``tree``."""
+    for index, (op, arg) in enumerate(tree):
+        before = prefix + _sample(tree[:index])
+        if op in (sre_constants.MAX_REPEAT, sre_constants.MIN_REPEAT):
+            body = list(arg[2])  # type: ignore[index]
+            unit = _sample(body) or "a"
+            yield before, unit
+            yield from _quantifier_leads(body, before)
+        elif op is sre_constants.SUBPATTERN:
+            yield from _quantifier_leads(list(arg[-1]), before)  # type: ignore[index]
+        elif op is sre_constants.BRANCH:
+            for branch in arg[1]:  # type: ignore[index]
+                yield from _quantifier_leads(list(branch), before)
+
+
+QUANTIFIER_TAILS = ("", "!", "\n", '"', "]", "x")
+
+
+def quantifier_shapes(
+    patterns: Iterable[re.Pattern[str]],
+) -> dict[str, Callable[[int], str]]:
+    out: dict[str, Callable[[int], str]] = {}
+    for pattern in patterns:
+        tree = list(sre_parse.parse(pattern.pattern, pattern.flags))
+        for lead, unit in set(_quantifier_leads(tree, "")):
+            if len(lead) > 200:
+                continue
+            for tail in QUANTIFIER_TAILS:
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
+                out[f"q({lead!r})+{unit!r}*k+{tail!r}"] = make
+    return out
diff --git a/tests/test_redaction_additive.py b/tests/test_redaction_additive.py
new file mode 100644
index 0000000000000000000000000000000000000000..63bd0f757bd859523d5675ba3f2295ecd0ce5a50
--- /dev/null
+++ b/tests/test_redaction_additive.py
@@ -0,0 +1,1339 @@
+"""The additive redaction rules (Consiliency/pmcp#234).
+
+`sanitize_auth_diagnostic` and `PolicyManager.redact_secrets` run the
+redactor's own rules unchanged, then the additive rules
+(`pmcp.redaction_additive`, rev 10's) over that output. Four guarantees:
+
+1. **Floor, by construction.** The additive rules only replace text with the
+   marker, so every piece the redactor removed stays removed. Checked against
+   an independent copy of the redactor (`tests/_main_redactor.py`): the base
+   pass is that redactor exactly, and every stretch of the final output
+   outside a marker appears, in order, in the base output.
+2. **JSON.** When the base output is a JSON document, the final output is
+   one too, and a structured result that came back a dict still does.
+3. **Linear time and memory** of the additive pass on every entry point, on a
+   deterministic work count and tracemalloc peaks -- never on the clock.
+4. **Markers** are never split, doubled into one another, or re-spelled.
+
+Plus what the additive rules are for: credentials the base pass keeps are
+removed, and prose the base pass keeps survives.
+"""
+
+from __future__ import annotations
+
+import json
+import multiprocessing
+import random
+import re
+import string
+import tracemalloc
+import uuid
+from collections.abc import Callable
+
+import pytest
+
+import pmcp.redaction_additive as A
+from pmcp.auth import AUTH_SECRET_QUERY_KEYS, _sanitize_base, sanitize_auth_diagnostic
+from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
+from tests import _main_redactor as M
+from tests import _redaction_grammar as G
+
+MAIN_PATTERNS = M.compiled_default_patterns()
+
+
+def _ours_e(text: str) -> str:
+    return sanitize_auth_diagnostic(text, max_length=None)
+
+
+def _ours_p(text: str) -> str:
+    return PolicyManager().redact_secrets(text)
+
+
+def _main_e(text: str) -> str:
+    return M.sanitize_auth_diagnostic(text, max_length=None)
+
+
+def _main_p(text: str) -> str:
+    return M.redact_secrets(text, MAIN_PATTERNS)
+
+
+def _ours_process(obj: object) -> object:
+    return PolicyManager().process_output(obj, redact=True, max_bytes=G.BIG)["result"]
+
+
+def _main_process(obj: object) -> object:
+    if isinstance(obj, str):
+        return _main_p(obj)
+    out = _main_p(json.dumps(obj, indent=2))
+    try:
+        return json.loads(out)
+    except json.JSONDecodeError:
+        return out
+
+
+# ================================================================ corpora ==== #
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
+status_code=401 error_code=invalid_grant token_type=Bearer expires_in=3600
+token_endpoint=https://auth.example/oauth/token code=404 token v2 is out
+{"code": -32601, "message": "Method not found", "data": {"code": "not_found"}}
+{"code": "not_found", "message": "no such tool", "request_id": "550e8400-e29b-41d4-a716-446655440000"}
+WWW-Authenticate: Bearer realm="api", error="insufficient_scope", scope="read write"
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
+        lambda: f"--{rng.choice(_PROSE_WORDS)} {rng.choice(_PROSE_WORDS)}",
+        lambda: f'{{"code": -{rng.randint(32000, 32768)}, "message": "{words()}"}}',
+    ]
+    return rng.choice([" ", ", ", "; ", ". ", "\n"]).join(
+        rng.choice(clauses)() for _ in range(rng.randint(3, 6))
+    )
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
+# ============================================================ the base pass ==== #
+
+
+def test_the_constants_are_the_redactors() -> None:
+    assert A.QUERY_SECRET_KEYS == AUTH_SECRET_QUERY_KEYS
+    assert tuple(DEFAULT_REDACTION_PATTERNS) == tuple(M.DEFAULT_REDACTION_PATTERNS)
+
+
+def _texts(rows: list[G.Row]) -> list[str]:
+    out: list[str] = []
+    for row in rows:
+        if "o" in row:
+            out.append(json.dumps(row["o"], indent=2))
+            continue
+        out.append(row["t"])
+        out.extend(
+            json.dumps({"t": row["t"]}, ensure_ascii=a, indent=i)
+            for _, a, i in G._SPELLINGS
+        )
+    return out
+
+
+def test_the_base_pass_is_the_redactor_unchanged() -> None:
+    """`_sanitize_base` is the vendored redactor, output for output, on every
+    tier-1 text, the prose, the credentials and the JSON fuzz."""
+    texts = _texts(G.corpus(1)) + [PROSE] + [c[1] for c in CREDENTIALS]
+    texts += [json.dumps(o, indent=2) for o in _json_fuzz_corpus()]
+    assert [t for t in texts if _sanitize_base(t) != _main_e(t)] == []
+
+
+# ================================================================== floor ==== #
+
+_MARKER_SPLIT_RE = re.compile(r"\[REDACTED\]|%5BREDACTED%5D")
+
+
+def _is_additive_of(ours: str, base: str) -> bool:
+    """Is ``ours`` ``base`` with some stretches replaced by the marker (or
+    removed)? Every stretch of ``ours`` outside a marker must appear in
+    ``base``, in order, after the previous one."""
+    position = 0
+    for piece in _MARKER_SPLIT_RE.split(ours):
+        if not piece:
+            continue
+        found = base.find(piece, position)
+        if found < 0:
+            return False
+        position = found + len(piece)
+    return True
+
+
+def _floor_violations(texts: list[str]) -> list[str]:
+    bad = []
+    policy = PolicyManager()
+    for text in texts:
+        for label, ours, base in (
+            ("E", _ours_e(text), _main_e(text)),
+            ("P", policy.redact_secrets(text), _main_p(text)),
+        ):
+            if not _is_additive_of(ours, base):
+                bad.append(f"{label} {text[:120]!r}")
+    return bad
+
+
+def test_floor_the_output_only_adds_markers_to_the_redactors_output() -> None:
+    """On half the tier-1 texts (every spelling), the board rows of four
+    review rounds, the prose, the credentials and the JSON fuzz; the slow
+    tier runs a quarter of tier 2."""
+    texts = _texts(G.corpus(1)[::2]) + _board_texts() + [PROSE]
+    texts += [c[1] for c in CREDENTIALS] + [json.dumps(o) for o in _json_fuzz_corpus()]
+    assert _floor_violations(texts) == []
+
+
+def test_floor_nothing_main_removes_survives_on_any_surface() -> None:
+    """The piece-level differential, all 12 surfaces and the dict path, on a
+    third of tier 1 (the slow tier runs the rest and a quarter of tier 2):
+    every piece of a value the redactor removed is removed here too, and
+    every dict it kept a dict stays one."""
+    bad = []
+    for row in G.corpus(1)[::3]:
+        main = G.observe(row, _main_e, _main_p, _main_process)
+        ours = G.observe(row, _ours_e, _ours_p, _ours_process)
+        for surface in G.worse_surfaces(row, main, ours):
+            bad.append(f"{surface}: {row.get('t', row.get('o'))!r}")
+    assert bad == [], bad[:20]
+
+
+def test_floor_with_the_default_400_character_cut() -> None:
+    """`sanitize_auth_diagnostic` cuts after both passes. The cut output is
+    still the redactor's output with markers written in: every stretch of
+    it outside a marker occurs, in order, in the redactor's FULL output.
+    (It can show text past the redactor's own 400-character window when an
+    additive marker is shorter than what it replaced -- text the redactor
+    kept, only cut.)"""
+    texts = _texts(G.corpus(1)[::5]) + _board_texts()
+    texts.append(
+        "-----BEGIN RSA PRIVATE KEY-----\n"
+        + "A" * 300
+        + "\n-----END RSA PRIVATE KEY-----"
+        + " word" * 120
+        + " TAIL"
+    )
+    bad = [
+        t[:80]
+        for t in texts
+        if not _is_additive_of(sanitize_auth_diagnostic(t), _main_e(t))
+    ]
+    assert bad == [], bad[:10]
+
+
+@pytest.mark.slow
+def test_floor_on_tier_2() -> None:
+    rows = [row for i, row in enumerate(G.corpus(1)) if i % 3] + G.corpus(2)[
+        len(G.corpus(1)) :: 4
+    ]
+    assert _floor_violations(_texts(rows)) == []
+    bad = [
+        row.get("t", row.get("o"))
+        for row in rows
+        if G.worse_surfaces(
+            row,
+            G.observe(row, _main_e, _main_p, _main_process),
+            G.observe(row, _ours_e, _ours_p, _ours_process),
+        )
+    ]
+    assert bad == [], bad[:20]
+
+
+def test_floor_mutant_a_restoring_pass_is_caught(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    """If the additive pass ever put back text the base pass removed, the
+    floor check fails (here: an additive pass that returns the raw input)."""
+    import pmcp.auth
+
+    monkeypatch.setattr(
+        pmcp.auth, "redact_additive", lambda text, **_: "password=hunter2x"
+    )
+    assert _floor_violations(["password=hunter2x"]) != []
+
+
+# =================================================================== JSON ==== #
+
+
+def test_json_documents_stay_documents() -> None:
+    """Wherever the base output of a JSON text parses, the final output
+    parses too, on both surfaces; and a structured result the base
+    `process_output` returned as a dict still comes back a dict."""
+    bad = []
+    policy = PolicyManager()
+    for obj in _json_fuzz_corpus():
+        for indent in (None, 2):
+            text = json.dumps(obj, indent=indent)
+            for label, ours, base in (
+                ("E", _ours_e(text), _main_e(text)),
+                ("P", policy.redact_secrets(text), _main_p(text)),
+            ):
+                try:
+                    json.loads(base)
+                except json.JSONDecodeError:
+                    continue
+                try:
+                    json.loads(ours)
+                except json.JSONDecodeError:
+                    bad.append(f"{label} {text[:100]!r}")
+        if isinstance(_main_process(obj), dict) and not isinstance(
+            _ours_process(obj), dict
+        ):
+            bad.append(f"POd {obj!r}"[:120])
+    assert bad == [], bad[:10]
+
+
+def test_a_span_across_a_string_boundary_is_confined_to_the_string() -> None:
+    """The clipping itself: a span that crosses a delimiting quote and a
+    separator (no additive rule makes one on the corpora -- they stop at
+    quotes -- so the guard is exercised directly) is applied inside the
+    string only, and the document stays a document."""
+    text = '{"a": "xsecretx", "b": 1}'
+    start = text.index("secretx")
+    span = [(start, text.index('"b"') + 1, A.REDACTED)]
+    assert json.loads(A.redact_additive(text, extra=span)) == {
+        "a": "x[REDACTED]",
+        "b": 1,
+    }
+
+
+def test_json_mutant_without_clipping_breaks_a_document(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    monkeypatch.setattr(A, "_clip_to_json_strings", lambda text, spans: spans)
+    with pytest.raises(json.JSONDecodeError):
+        test_a_span_across_a_string_boundary_is_confined_to_the_string()
+
+
+def test_object_keys_are_left_alone_so_entries_never_collide() -> None:
+    """Two random-looking keys redacted to the same marker collapsed a map to
+    its last entry (rev 15's board). The additive pass writes only into
+    string VALUES of a document."""
+    obj = {"Xk9mQ2vLp3RtY7wBaaQ1": 1, "Pq7rS2tUv9WxY3zAbC4d": 2}
+    assert _ours_process(obj) == obj
+    doc = json.dumps({"Xk9mQ2vLp3RtY7wBaaQ1": "Pq7rS2tUv9WxY3zAbC4d"})
+    assert json.loads(_ours_e(doc)) == {"Xk9mQ2vLp3RtY7wBaaQ1": "[REDACTED]"}
+
+
+def test_object_keys_mutant_clipping_into_keys(monkeypatch: pytest.MonkeyPatch) -> None:
+    """Rev 15's clipping, which let spans into object keys, collides them."""
+    monkeypatch.setattr(A, "_is_object_key", lambda text, end: False)
+    with pytest.raises(AssertionError):
+        test_object_keys_are_left_alone_so_entries_never_collide()
+
+
+# ================================================================ markers ==== #
+
+
+def _four_surfaces() -> dict[str, tuple[Callable[[str], str], Callable[[str], str]]]:
+    """(ours, the redactor's) for E, P, POs and POd, each as text."""
+    policy = PolicyManager()
+
+    def ours_pod(text: str) -> str:
+        return json.dumps(_ours_process({"t": text}))
+
+    def main_pod(text: str) -> str:
+        return json.dumps(_main_process({"t": text}))
+
+    return {
+        "E": (_ours_e, _main_e),
+        "P": (policy.redact_secrets, _main_p),
+        "POs": (lambda t: str(_ours_process(t)), lambda t: str(_main_process(t))),
+        "POd": (ours_pod, main_pod),
+    }
+
+
+_MARKER_TEXTS = [
+    "see https://h.example/cb?token=abc123def456&page=2 now",
+    "https://api.example.com/v1/items?token=abc&page=3&limit=50",
+    "https://h/?api_key=k1&password=p2&secret=s3&pwd=p4&passwd=p5&x=1",
+    "password=[REDACTED] then token: [REDACTED] and Bearer [REDACTED]",
+    "token=%5BREDACTED%5D&next=https://h/?q=1",
+    "[REDACTED]abc123def456 [REDACTED] ghp_16C7e42F292c6912E7710c838347Ae178B4a",
+]
+
+
+def _marker_problems(texts: list[str]) -> list[str]:
+    """On all four surfaces: every marker of the redactor's output is still
+    there, whole and with its spelling, in order; no `REDACTED` appears
+    outside a whole marker; and (E, P) a second additive pass over the
+    output keeps every one of its markers and only adds. (It is monotone,
+    not idempotent: next to a marker it wrote, a second pass may mark a
+    scheme word or a value prefix the first pass left.)"""
+    bad = []
+    for text in texts:
+        for label, (ours, main) in _four_surfaces().items():
+            base, out = main(text), ours(text)
+            base_markers = _MARKER_SPLIT_RE.findall(base)
+            out_markers = _MARKER_SPLIT_RE.findall(out)
+            position = 0
+            for marker in base_markers:
+                found = out.find(marker, position)
+                if found < 0:
+                    bad.append(
+                        f"{label}: lost or re-spelled {marker} in {text!r} -> {out!r}"
+                    )
+                    break
+                position = found + len(marker)
+            if "REDACTED" in _MARKER_SPLIT_RE.sub("", out):
+                bad.append(f"{label}: a split marker in {out!r}")
+            if len(out_markers) < len(base_markers):
+                bad.append(f"{label}: fewer markers in {out!r}")
+            again = (
+                A.redact_additive(out)
+                if label == "E"
+                else PolicyManager()._redact_additive(out)
+                if label == "P"
+                else out
+            )
+            # run again, the additive pass only adds: it keeps every marker
+            # of its own output whole, in order and in its spelling, and
+            # every other stretch it keeps comes from that output
+            position = 0
+            for marker in out_markers:
+                found = again.find(marker, position)
+                if found < 0:
+                    bad.append(f"{label}: a second pass lost {marker} in {out!r}")
+                    break
+                position = found + len(marker)
+            if not _is_additive_of(again, out):
+                bad.append(f"{label}: a second pass restored text in {out!r}")
+    return bad
+
+
+def test_markers_are_kept_whole_and_spelled_as_written_on_every_surface() -> None:
+    texts = _MARKER_TEXTS + _texts(G.corpus(1)[::7]) + _board_texts()
+    assert _marker_problems(texts) == [], _marker_problems(texts)[:10]
+
+
+def test_markers_mutant_a_policy_value_run_through_a_marker(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    """Rev 15's policy pass (no marker skip, no stop at `&` in a query, and
+    a merge that swallowed markers) re-spelled the URL marker and lost the
+    rest of the query."""
+    import pmcp.policy.policy as P
+
+    monkeypatch.setattr(P, "starts_with_marker", lambda value: False)
+    monkeypatch.setattr(P, "_url_query_ranges", lambda text: [])
+    monkeypatch.setattr(A, "_MARKER_RE", re.compile(r"(?!)"))
+    assert _marker_problems(_MARKER_TEXTS[:2]) != []
+
+
+def test_a_policy_value_in_a_query_ends_at_the_next_parameter() -> None:
+    policy = PolicyManager()
+    out = policy.redact_secrets("x https://h/?q=1&token=abc123def456zz&page=2#top y")
+    assert "abc123def456zz" not in out and "&page=2" in out, out
+
+
+# ============================================================= what it adds ==== #
+
+
+@pytest.mark.parametrize(("label", "text", "secret", "survive"), CREDENTIALS)
+def test_the_additive_rules_remove_each_credential(
+    label: str, text: str, secret: str, survive: list[str]
+) -> None:
+    for surface in (_ours_e, _ours_p):
+        out = surface(text)
+        assert secret not in out, (label, out)
+
+
+def test_prose_the_redactor_keeps_the_additive_rules_keep_too() -> None:
+    """The additive rules' own false positives: none on the prose corpus or
+    on 2 000 generated prose lines (the base pass's own stand)."""
+    assert _ours_e(PROSE) == _main_e(PROSE)
+    assert _ours_p(PROSE) == _main_p(PROSE)
+    rng = random.Random(9234)
+    for _ in range(2000):
+        text = _random_prose(rng)
+        assert _ours_e(text) == _main_e(text), text
+        assert _ours_p(text) == _main_p(text), text
+
+
+def test_keyed_passwords_from_every_printable_character_are_removed() -> None:
+    rng = random.Random(1234)
+    for password in _random_passwords(rng, 300):
+        for text, encoded, probe in _keyed_forms(password):
+            if len(probe) < 4 or probe in text.replace(encoded, "", 1):
+                continue
+            for surface in (_ours_e, _ours_p):
+                assert probe not in surface(text), (text, surface(text))
+
+
+def test_an_upper_case_scheme_does_not_hide_userinfo() -> None:
+    for text in ("HTTPS://user:s3cr3tpass@h.example/p", "Http://u:hunter2x@h.example/"):
+        for surface in (_ours_e, _ours_p):
+            out = surface(text)
+            assert "s3cr3tpass" not in out and "hunter2x" not in out, out
+
+
+def test_operator_pattern_padding_is_kept_whole() -> None:
+    """The first-separator split kept a base64 secret and replaced its
+    padding; the additive split redacts the whole match."""
+    policy = PolicyManager()
+    policy._redaction_regexes = [re.compile(r"[A-Za-z0-9+/]{16,}={1,2}")]
+    assert "abcdabcdabcdabcdabcd" not in policy.redact_secrets(
+        "x abcdabcdabcdabcdabcd== y"
+    )
+
+
+# ========================================================= linear, counted ==== #
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
+    A.WORK = [0]
+    try:
+        run(text)
+        return A.WORK[0]
+    finally:
+        A.WORK = None
+
+
+def _peak(run: Callable[[str], object], text: str) -> int:
+    tracemalloc.start()
+    try:
+        run(text)
+        return tracemalloc.get_traced_memory()[1]
+    finally:
+        tracemalloc.stop()
+
+
+#: Growth allowed per 4x of input: linear, plus the n log n of sorting
+#: spans, plus 5%. A quadratic term gives 16x.
+_PER_4X = 4 * (16 / 14) * 1.05
+
+#: Every shape four review rounds found (the additive rules' share of
+#: them), and the families derived from the rules' loops.
+LINEAR_SHAPES: dict[str, Callable[[int], str]] = {
+    "closers after a Bearer value": lambda n: "Bearer x" + ")" * n + "a",
+    "escaped quotes after Authorization": lambda n: "Authorization: x" + '\\"' * (n // 2) + "a",
+    "line breaks before a Bearer value": lambda n: "x Bearer" + "\n" * n + " abc",
+    "line breaks before a keyword value": lambda n: "token" + "\n" * n + " abc12",
+    "keyword matches after resource names": lambda n: "arn:x " * (n // 12) + "password=abc123 " * (n // 32),
+    "keyword lists after resource names": lambda n: "arn:x " * (n // 12) + 'tokens: ["a1b2c3d4"] ' * (n // 42),
+    "keys inside resource names": lambda n: "arn:x:secret=abc123 " * (n // 20),
+    "backslash run before escapes": lambda n: "\\" * (n // 2) + "\\u00e9password=x" * 8,
+    "empty query pairs": lambda n: "https://h/?" + "&" * n + "a",
+    "secret-keyed query pairs": lambda n: "https://h/?" + "password=x&" * (n // 11),
+    "re-encoded query value": lambda n: "https://h/?q=+" + "sk-abcdef/" * (n // 10),
+    "key words after Bearer": lambda n: "x Bearer" + "api_key" * (n // 7) + '"',
+    "joined key run": lambda n: "token-" * (n // 6) + "=abc123def",
+    "word boundaries": lambda n: "a-" * (n // 2),
+    "operator run": lambda n: "password" + ":=" * (n // 2),
+    "markers in the input": lambda n: "password=" + "[REDACTED]" * (n // 10),
+    "Bearer values in a JSON document": lambda n: json.dumps({f"k{i}": "Bearer x" for i in range(n // 20)}),
+    "tokens in a JSON document": lambda n: json.dumps({f"k{i}": "ghp_abcdefghij1234" for i in range(n // 30)}),
+    "long joined keys": lambda n: ("a" * 170 + "_token" * 14 + "=Hunter2abc9 ") * (n // 267),
+    "an unterminated keyword list": lambda n: "tokens: [" + " " * n + "x",
+    "a list in a JSON leaf": lambda n: json.dumps({"t": "tokens: [" + " " * n}),
+}  # fmt: skip
+
+
+def _work_superlinear(shape: Callable[[int], str], sizes: tuple[int, ...]) -> list[str]:
+    found = []
+    for label, run in _entry_points().items():
+        counts = [_work(run, shape(n)) for n in sizes]
+        for small, large in zip(counts, counts[1:]):
+            if large > _PER_4X * max(small, 1):
+                found.append(f"{label}: {counts}")
+    return found
+
+
+@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
+def test_the_additive_pass_does_linear_work(name: str) -> None:
+    """16 KB -> 64 KB on every entry point: the work count grows linearly.
+    Deterministic, so one step of 4x shows a quadratic term (16x); the slow
+    tier adds the step to 256 KB."""
+    assert _work_superlinear(LINEAR_SHAPES[name], (16_384, 65_536)) == []
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
+def test_the_additive_pass_does_linear_work_to_256_kb(name: str) -> None:
+    assert _work_superlinear(LINEAR_SHAPES[name], (16_384, 65_536, 262_144)) == []
+
+
+def _memory_superlinear(
+    shape: Callable[[int], str], labels: tuple[str, ...], sizes: tuple[int, ...]
+) -> list[str]:
+    found = []
+    for label in labels:
+        run = _entry_points()[label]
+        run(shape(1_024))  # first-use allocations out of the way
+        peaks = [_peak(run, shape(n)) for n in sizes]
+        for small, large in zip(peaks, peaks[1:]):
+            # a constant allowance for list over-allocation at small sizes;
+            # a quadratic term is megabytes over it
+            if large > _PER_4X * small + 512 * 1024:
+                found.append(f"{label}: {peaks}")
+    return found
+
+
+@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
+def test_every_entry_point_uses_linear_memory(name: str) -> None:
+    """Peak traced memory of the whole engine call, 8 KB -> 32 KB: linear,
+    up to a fixed 512 KiB allowance. The slow tier adds 128 KB and the
+    dict path."""
+    assert _memory_superlinear(LINEAR_SHAPES[name], ("E",), (8_192, 32_768)) == []
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
+def test_every_entry_point_uses_linear_memory_to_128_kb(name: str) -> None:
+    assert (
+        _memory_superlinear(LINEAR_SHAPES[name], ("E", "POd"), (8_192, 32_768, 131_072))
+        == []
+    )
+
+
+def _rev12_in_resource_name(text: str) -> Callable[[int], bool]:
+    ranges = [(m.start(), m.end()) for m in A._RESOURCE_NAME_RE.finditer(text)]
+
+    def inside(position: int) -> bool:
+        A.work(len(ranges))  # asking every name
+        return any(a <= position < b for a, b in ranges)
+
+    return inside
+
+
+def test_linearity_mutant_asking_every_resource_name(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    shape = LINEAR_SHAPES["keyword lists after resource names"]
+    run = _entry_points()["P"]
+    monkeypatch.setattr(A, "_in_resource_name", _rev12_in_resource_name)
+    small, large = _work(run, shape(2_048)), _work(run, shape(8_192))
+    assert large > _PER_4X * small
+
+
+_SWEEP_CHUNKS = 8
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("chunk", range(_SWEEP_CHUNKS))
+def test_generated_shapes_do_linear_work(chunk: int) -> None:
+    """Every 8th shape derived from the redactor's regular expressions and
+    the additive rules' loops (`tests/_redaction_shapes.py`), screened on the
+    work count at 4 KB -> 16 KB on every entry point in worker processes; a
+    flagged shape is re-measured at 16 KB -> 256 KB and must be linear."""
+    names = sorted(_all_shapes())[chunk :: _SWEEP_CHUNKS * 8]
+    ctx = multiprocessing.get_context("spawn")
+    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
+        flagged = [
+            n
+            for found in pool.imap_unordered(_screen, names, chunksize=25)
+            for n in found
+        ]
+    shapes = _all_shapes()
+    bad = {}
+    for name in flagged:
+        for label, run in _entry_points().items():
+            counts = [_work(run, shapes[name](n)) for n in (16_384, 65_536, 262_144)]
+            if any(b > _PER_4X * max(a, 1) for a, b in zip(counts, counts[1:])):
+                bad[name] = (label, counts)
+    assert bad == {}, bad
+
+
+def _all_shapes() -> dict[str, Callable[[int], str]]:
+    from tests import _redaction_shapes as S
+
+    return {**S.shapes(S.redactor_patterns()), **S.compositions(), **S.atom_repeats()}
+
+
+_SCREEN: dict[str, Callable[[int], str]] = {}
+
+
+def _screen(name: str) -> list[str]:
+    if not _SCREEN:
+        _SCREEN.update(_all_shapes())
+    bound = 4 * (14 / 12) * 1.05
+    for run in _entry_points().values():
+        if _work(run, _SCREEN[name](16_384)) > bound * max(
+            _work(run, _SCREEN[name](4_096)), 1
+        ):
+            return [name]
+    return []
+
+
+# ============================================================ board inputs ==== #
+
+
+def _board_texts() -> list[str]:
+    """Inputs four review rounds used against the replay design, kept as a
+    differential corpus: multi-pair and wide-separator grids, joiner-glued
+    schemes, punctuation inside header values, URLs, OAuth bodies, resource
+    names, long keys and numbers."""
+    texts: list[str] = []
+    for k1 in (
+        "password",
+        "api_key",
+        "token",
+        "secret",
+        "code",
+        "Authorization",
+        "cookie",
+    ):
+        for op in ("=", ":", ": "):
+            for brk in ("\n", "\t", "\r\n", "\n\n", " \n"):
+                for k2 in ("password", "token", "session"):
+                    texts.append(f"{k1}{op}{brk}{k2}: hunter2x")
+    for pre in ("--", "-", "x-", "X-", "x_", "access_", ""):
+        for v in ("hunter2x", "s3cr3tvalue", "abc-def_1.2", "Zm9vOmJhcg=="):
+            texts.append(f"mycli {pre}bearer {v} --verbose")
+    for head in ("abcdef", "q7Zp2Lk9Wx4R"):
+        for ch in "\"'()[]{}<>=|\\!@#$%^&*`?/:":
+            texts.append(f"x Bearer {head}{ch}SECRETPART end")
+            texts.append(f"Authorization: Bearer {head}{ch}SECRETPART end")
+    texts += [
+        "see https://user:pa@ssw0rdXq@host.example/p ok",
+        "https://h.example:99999/" + "a" * 420 + "?q=MYSECRETVAL",
+        "https://h/?q=+" + "aaaaaaaaaa.bbbbbbbbbb.cccccccccc/" * 20,
+        "https://H.EXAMPLE:0443/p?token=abc",
+        "grant_type=urn:ietf:params:oauth:grant-type:token-exchange&client_secret=Hunter2abcX9",
+        "connect urn:db;user=admin;password=Hunter2abcX9",
+        "urn:secret:Hunter2abcX9",
+        "DATABASE_PASSWORD_FOR_REPLICATION_USER_ACCOUNT=Hunter2abcX9",
+        "passwordForTheProductionDatabaseServer=Hunter2abcX9",
+        "Bearer " + "1" * 24,
+        "Bearer 0x" + "ab" * 32,
+        "code=482913",
+        "x Bearer abcdef=SECRETPART end",
+        "token_type=Bearer expires_in=3600",
+    ]
+    return texts
+
+
+def _fold_variants() -> list[str]:
+    """Every key word with each of its letters replaced by every character
+    Python's `re.IGNORECASE` treats as equal to it, plus the multi-character
+    folds (`ß` for `ss`, `ẞ`, `ﬁ`/`ﬂ`/`ﬀ` ligatures) that full case
+    folding has and simple folding does not."""
+    ascii_class = re.compile("[a-z0-9_-]", re.IGNORECASE)
+    fold: dict[str, set[str]] = {}
+    for letter in string.ascii_lowercase:
+        fold[letter] = {letter.upper()}
+    for i in range(0x80, 0x110000):
+        char = chr(i)
+        if ascii_class.fullmatch(char):
+            for letter in string.ascii_lowercase:
+                if re.fullmatch(letter, char, re.IGNORECASE):
+                    fold[letter].add(char)
+    texts = []
+    for key in sorted(A.ADDITIVE_SECRET_KEYS) + ["api_key", "apikey", "private_key"]:
+        for index, letter in enumerate(key):
+            for other in sorted(fold.get(letter.lower(), set())):
+                texts.append(key[:index] + other + key[index + 1 :])
+        texts.append(key.replace("ss", "\u00df").replace("s", "\u017f", 1))
+        texts.append(key.replace("ss", "\u1e9e"))
+        texts.append(key.replace("fi", "\ufb01").replace("ff", "\ufb00"))
+    return texts
+
+
+def test_the_key_prefilter_never_hides_a_keyword_match() -> None:
+    """The keyword passes are skipped only when the prefilter finds no key.
+    The prefilter IS their key alternation under their flag, so it can only
+    miss what they miss: checked here against the passes' own patterns, on
+    every key with every case-fold variant of each letter and the
+    multi-character folds, each in a separator and a whitespace context."""
+    for key in _fold_variants():
+        for text in (f"{key}=hunter22x", f"{key} abc123def456", f'{key}: ["a1b2c3d4"]'):
+            matched = any(
+                pattern.search(text) is not None
+                for pattern in (A._KEYWORD_SEP_RE, A._KEYWORD_LIST_RE, A._KEYWORD_WS_RE)
+            )
+            if matched:
+                assert A._may_hold_a_key(text), text
+
+
+def test_multi_character_folds_do_not_match_on_this_interpreter() -> None:
+    """Python's `re` applies simple case folding only: `(?i)password` does not
+    match `paßword` (nor `paẞword`), so neither the redactor's keyword rule nor
+    the additive rules read it as a key -- the prefilter changes nothing
+    there (a reported multi-character-fold gap, checked and not present)."""
+    assert re.search("(?i)password", "pa\u00dfword") is None
+    assert re.search("(?i)password", "pa\u1e9eword") is None
+    for surface in (_ours_e, _main_e):
+        assert "hunter2" in surface("pa\u00dfword=@hunter2")
+
+
+# ============================================= regex quantifier structure ==== #
+
+
+def _quantifier_shapes() -> dict[str, Callable[[int], str]]:
+    from tests import _redaction_shapes as S
+
+    return S.quantifier_shapes(S.redactor_patterns())
+
+
+_CAPS = {4_096: 1.0, 16_384: 2.0, 65_536: 5.0}
+
+
+def _over_cap(name: str, sizes: tuple[int, ...], leaf: bool = True) -> list[str]:
+    import time
+
+    shape = _QSHAPES[name] if _QSHAPES else _quantifier_shapes()[name]
+    over = []
+    surfaces = dict(_entry_points())
+    if leaf:
+        surfaces["E-leaf"] = lambda t: sanitize_auth_diagnostic(
+            json.dumps({"t": t}), max_length=None
+        )
+    for size in sizes:
+        text = shape(size)
+        for label, run in surfaces.items():
+            started = time.perf_counter()
+            run(text)
+            elapsed = time.perf_counter() - started
+            if elapsed > _CAPS[size]:
+                over.append(f"{name} {label} {size}: {elapsed:.2f}s")
+    return over
+
+
+_QSHAPES: dict[str, Callable[[int], str]] = {}
+
+
+def _screen_quantifier(name: str) -> list[str]:
+    if not _QSHAPES:
+        _QSHAPES.update(_quantifier_shapes())
+    return _over_cap(name, (4_096,), leaf=False)
+
+
+def test_no_regex_backtracks_on_its_own_quantifier_structure() -> None:
+    """Every quantifier of every redactor regex, reached by the shortest text
+    that leads to it, one unit repeated to 4 KB, and each of six failing
+    tails: on the engine, in worker processes, under a 1 s cap (rev 15's
+    list body took 30 s here; now every shape takes milliseconds). The work
+    counter cannot see a regex's own backtracking; this can."""
+    names = sorted(_quantifier_shapes())
+    assert len(names) > 500
+    ctx = multiprocessing.get_context("spawn")
+    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
+        over = [
+            o
+            for found in pool.imap_unordered(_screen_quantifier, names, chunksize=10)
+            for o in found
+        ]
+    assert over == [], over[:10]
+
+
+@pytest.mark.slow
+@pytest.mark.parametrize("chunk", range(4))
+def test_no_regex_backtracks_at_64_kb_on_any_surface(chunk: int) -> None:
+    """The same shapes at 4, 16 and 64 KB on every entry point and inside a
+    JSON string leaf, under generous caps (1, 2 and 5 s)."""
+    names = sorted(_quantifier_shapes())[chunk::4]
+    ctx = multiprocessing.get_context("spawn")
+    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
+        over = [
+            o
+            for found in pool.imap_unordered(_full_quantifier, names, chunksize=5)
+            for o in found
+        ]
+    assert over == [], over[:10]
+
+
+def _full_quantifier(name: str) -> list[str]:
+    if not _QSHAPES:
+        _QSHAPES.update(_quantifier_shapes())
+    return _over_cap(name, (4_096, 16_384, 65_536))
+
+
+def test_list_backtracking_mutant(monkeypatch: pytest.MonkeyPatch) -> None:
+    """Rev 15's list body (three `\\s*` that could split one run) restored:
+    `tokens: [` + 2 KB of spaces and no `]` is over the cap again."""
+    old_body = (
+        r"(?P<list>\[\s*(?:(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|-?[0-9][0-9.eE+-]*"
+        r"|null|true|false)\s*,\s*)*(?:(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'"
+        r"|-?[0-9][0-9.eE+-]*|null|true|false))?\s*,?\s*\])"
+    )
+    monkeypatch.setattr(
+        A, "_KEYWORD_LIST_RE", re.compile(A._KEYWORD_KEY_SEP + old_body, re.IGNORECASE)
+    )
+    import time
+
+    started = time.perf_counter()
+    sanitize_auth_diagnostic("tokens: [" + " " * 2048 + "x", max_length=None)
+    assert time.perf_counter() - started > 1.0
+
+
+# ======================================= values the redactor partly marked ==== #
+#
+# The redactor's keyword rule accepts whitespace as a separator, so a key word
+# it lists, followed by a word, writes a marker INSIDE a strong key's value
+# (`{"password": "Secret Garden 2024!"}` -> `"Secret [REDACTED] 2024!"`). Rev 16
+# dropped any additive span overlapping a marker, and the rest of the value
+# showed (rev 16's board, F1). Derived from the redactor's own key list.
+
+_STRONG_KEYS = sorted(
+    k for k in A.ADDITIVE_SECRET_KEYS if k not in A.WEAK_SECRET_KEYS and k != "code"
+)
+_BASE_KEY_WORDS = sorted({*M.AUTH_DIAGNOSTIC_SECRET_KEYS, "api_key"})
+_VALUE_TEMPLATES = ("{w} Garden 2024!", "the {w} was 1999 mild", "x9 {w}=q7Zp2 tail")
+
+
+def _value_leftovers(value: str) -> str:
+    return _MARKER_SPLIT_RE.sub("", value).strip()
+
+
+def _partly_marked_failures() -> list[str]:
+    bad = []
+    policy = PolicyManager()
+    for key in _STRONG_KEYS:
+        for word in _BASE_KEY_WORDS:
+            for template in _VALUE_TEMPLATES:
+                value = template.format(w=word)
+                if _MARKER_SPLIT_RE.search(_main_e(json.dumps({key: value}))) is None:
+                    continue  # the redactor wrote no marker in it: not this class
+                doc = json.dumps({key: value})
+                for label, out in (
+                    ("E", _ours_e(doc)),
+                    ("P", policy.redact_secrets(doc)),
+                    ("POd", json.dumps(_ours_process({key: value}))),
+                ):
+                    left = _value_leftovers(json.loads(out)[key])
+                    if left:
+                        bad.append(f"{label} {doc} -> {left!r}")
+                plain = f'{key}: "{value}"'
+                out = _ours_e(plain)
+                inner = out[out.index('"') + 1 : out.rindex('"')]
+                if _value_leftovers(inner):
+                    bad.append(f"E {plain} -> {out}")
+    return bad
+
+
+def test_a_value_the_redactor_partly_marked_is_redacted_whole() -> None:
+    """For every strong key and every key word the redactor reacts to, a
+    value holding that word (then a word or `=`) keeps no character outside
+    the markers, on E, P and the structured path; the markers stay as
+    written."""
+    bad = _partly_marked_failures()
+    assert bad == [], bad[:10]
+
+
+def _rev16_merge(text: str, spans: list[A.Span]) -> list[A.Span]:
+    """Rev 16's merge: a span overlapping any marker was dropped."""
+    markers = [m.span() for m in A._MARKER_RE.finditer(text)]
+    kept = [
+        (start, end, r)
+        for start, end, r in spans
+        if not any(m0 < end and start < m1 for m0, m1 in markers)
+    ]
+    return A._merge_unmarked(text, kept)
+
+
+def test_partly_marked_mutant_rev_16_merge(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setattr(A, "merge_redaction_spans", _rev16_merge)
+    assert _partly_marked_failures() != []
+
+
+_HEX_TICKET = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
+
+
+@pytest.mark.parametrize("header", ["Cookie", "cookie", "Set-Cookie", "set-cookie"])
+@pytest.mark.parametrize(
+    "first", ["csrftoken=abc123", "sid=abc123", "session=abc123", "token=x1y2z3"]
+)
+@pytest.mark.parametrize("name", [".ASPXAUTH", "auth", "ticket", "remember_me", "ID"])
+def test_a_cookie_header_keeps_no_ticket(header: str, first: str, name: str) -> None:
+    value = f"{first}; {name}={_HEX_TICKET}; Path=/"
+    obj = {"headers": {header: value}}
+    for out in (
+        _ours_e(json.dumps(obj)),
+        _ours_p(json.dumps(obj)),
+        json.dumps(_ours_process(obj)),
+    ):
+        assert _HEX_TICKET not in out, out
+
+
+def test_a_pem_block_with_a_marker_inside_is_redacted_whole() -> None:
+    body = "\n".join(
+        [
+            "MIIEpAIBAAKCAQEA3Tz2mr7SZiAMfQyuvBjM9Oi",
+            "Zq1x0sid",
+            "eXno5n8Iq7Zp2Lk9Wx4RAbCdEf01",
+        ]
+    )
+    block = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"
+    assert _MARKER_SPLIT_RE.search(_main_e(block)) is not None  # the case at issue
+    for surface in (_ours_e, _ours_p):
+        out = surface(block)
+        for line in body.split("\n"):
+            assert line not in out, out
+        assert "eXno5n8I" not in out, out
+
+
+def test_bearer_value_lookahead_mutant() -> None:
+    """A lookahead that asks whether a run of whitespace escapes is followed
+    by a value rescans the run at every split of the separator: quadratic on
+    `bearer` + many `\\n` escapes in a JSON string (the slow-tier sweep found
+    it during rev 17). The one-character form is linear."""
+    import time
+
+    text = json.dumps({"t": "bearer" + "\n" * 16_384})
+    started = time.perf_counter()
+    sanitize_auth_diagnostic(text, max_length=None)
+    assert time.perf_counter() - started < 1.0
+    one_char = "(?!" + A._JSON_SPACE_ESCAPE + ")"
+    assert one_char in A._BEARER_RE.pattern
+    slow = re.compile(
+        A._BEARER_RE.pattern.replace(
+            one_char,
+            "(?!(?:" + A._JSON_SPACE_ESCAPE + r")+(?:[\s,;\"'()\[\]{}<>]|$))",
+        ),
+        A._BEARER_RE.flags,
+    )
+    started = time.perf_counter()
+    list(slow.finditer(json.dumps({"t": "bearer" + "\n" * 16_384})))
+    assert time.perf_counter() - started > 0.3
```
<!-- PATCH-END -->
