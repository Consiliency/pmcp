# Escaped URL tails after redaction (Consiliency/pmcp#401)

## The problem

The redactor's URL pass (`pmcp.auth._sanitize_base`) matches
`https?://[^\s"'<>]+`, strips a trailing `).,;` run and rewrites the rest with
`redact_auth_url`. Inside JSON text a URL runs into the `\"` that escapes its
closing quote. The URL pass then encodes that backslash as `%5C` (and `)`, `,`,
`;` before it as `%29`, `%2C`, `%3B`). The result is no longer valid JSON, so
`process_output` falls back to a string and a benign search result such as

```text
<Card href="https://www.youtube.com/watch?v=kQmXtrmQ5Zg">
```

comes back damaged.

## What changed

Restoration runs **after** every redaction pass, in the two public entry points
only: `sanitize_auth_diagnostic` and `PolicyManager.redact_secrets`. Each call
uses a fresh per-call record held in a `ContextVar` that is reset in a
`finally`. `_sanitize_base` returns exactly main's text. While it runs, it
records two things:

- the exact text after the URL pass (the snapshot);
- for each eligible URL match, its span in that snapshot, the bytes the URL pass
  emitted there, and the original matched text.

A direct caller of `_sanitize_base` without a record gets main's output and
nothing is restored. If `_sanitize_base` runs twice under one record, the
record is poisoned and nothing is restored.

### Eligibility

A URL match `whole` is recorded only when all of these hold:

- `core = whole.rstrip(").,;\\")` differs from `whole`. Main strips only
  `).,;`; the backslash is added deliberately, because it is the JSON escape of
  the closing quote.
- The URL pass changed the match.
- `core` fully matches this ASCII grammar:

  ```text
  https?://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~/-]*)?(?:\?[A-Za-z0-9._~=&-]*)?
  ```

  This is a conservative proof boundary, not a URL grammar. No `%`, `+`,
  userinfo, port or fragment reaches restoration.
- `redact_auth_url(core) == core`: the core is unchanged by URL redaction and
  normalization. This rejects secret query keys, dropped pairs and altered
  hosts.
- The URL pass's output starts with `core`.

The only difference a restore can make is therefore the original trailing
`).,;\` run. No query value or other core byte is recovered.

### The survival certificate

Restoration happens only if the final text, after the Authorization, Bearer,
keyword, JWT, operator-pattern and additive passes, equals the snapshot
byte-for-byte. Then no later pass changed anything and every recorded offset is
exact. At each recorded span the canonical bytes must still be present; the raw
match replaces them through an ordered slice/join. There is no `str.replace`
and no search, so a literal canonical `%5C` URL elsewhere in the text is never
touched.

This includes the redactions added for Consiliency/pmcp#478 (escaped views,
carried through the base pass's recorded edits, and the Authorization
credential): they are applied by the additive pass, after the snapshot, so
they cancel the restore; they never re-expose anything. Recording the base
pass's edits does not change its output.

Any change made by a **post-URL pass**, anywhere in the text, restores nothing:
the whole text stays main's output. Redactions made by the URL pass itself
(a secret query value, userinfo, a fragment) are part of the snapshot. They do
not cancel restoration of other, eligible URLs in the same text; the redacted
URL itself is ineligible.

### Truncation

`sanitize_auth_diagnostic` checks the certificate against the full, uncut text.
With a nonnegative `max_length` it then rebuilds only main's
`text[:max_length]` and restores only spans wholly inside it. A span the cut
reaches into keeps main's partial canonical bytes (for example `…abc%` or
`…abc%5`), so a cut diagnostic is not always a prefix of the uncut one. Nothing
past main's cut is ever pulled into view. A negative `max_length` returns
main's text for the caller's own slice.

## Why secrecy cannot decrease

Every redaction pass sees exactly main's intermediate text and produces exactly
main's output; restoration is the last step. The certificate allows no recovery
if any later pass changed anything. An eligible core is unchanged by URL
redaction and is already printed in clear by main. A restore can only put back
the punctuation/backslash run main itself encoded at the end of that core. It
never recovers a removed value, a marker, surrounding text, a literal URL
elsewhere or anything beyond main's cut. Operator patterns run before the
certificate and cancel it when they change anything.

This patch preserves main's secrecy decisions. It does not claim that main
identifies every possible secret: an input on which main already leaks is a
baseline defect, not something this change fixes.

## Known limitations

- Values with `%XX` escapes or `+` keep main's output (for example
  `q=a+b` becomes `q=a%20b%5C`). So do secret keys, userinfo, ports, fragments
  and encoded delimiters.
- A text with any change by a post-URL pass keeps main's output in full, even
  where a benign URL elsewhere survived unchanged. Restoring through a changed
  snapshot would need separate, reviewed provenance tracking.
- Such texts can still fall back from JSON to a string in `process_output`.
- **The dict-to-string trade (Consiliency/pmcp#478, accepted; follow-up
  Consiliency/pmcp#483).** When a `process_output(dict)` result holds both an
  eligible URL tail and a value that Consiliency/pmcp#478 now redacts, the restore is
  cancelled, main's `%5C"` stays, the document no longer parses and the result
  is main's pre-Consiliency/pmcp#401 *string*, with the secret withheld.
  Measured on 324 structured cells (12 forms, 9 keys, depths 0-2): main
  returns a dict in 279 and Consiliency/pmcp#478 in 252; all 27 changes are
  the form
  `{"<key>": "https://example.test/a?v=1\\", "b": "c"}`, where the URL is the
  secret-keyed value main left in clear. No result went from string to dict.
  Main already returns a string for the same reason whenever a depth-0 secret
  sits beside an eligible URL, so callers must handle `str`. The trade is
  pinned by tests (`tests/test_redaction_478_escaped.py`, which also show
  main's dict when the Consiliency/pmcp#478 spans are removed), so a
  provenance-tracked restore has to flip them on purpose.

## Escaped text and Authorization credentials (Consiliency/pmcp#478)

The additive context rules (keyword, keyword list, Authorization, Bearer, URL
query) also read up to four decoded layers of JSON string escapes of the
**raw** input, so `\"token\": \"…\"` is read as `"token": "…"`. They read the
raw input, not the base pass's output, because the URL pass runs over escapes
in escaped text and re-encodes or drops the next line's key
(`?q=1\ntoken:` becomes `%5Cntoken%3A`). Each span maps back over whole
escapes and is carried to the final text through the edits every earlier pass
recorded (the base pass's, then each policy pattern's). An
Authorization-credential rule reads the raw text and every view.

The credential rule takes a token68 credential after the scheme word, or an
auth-param list: two or more comma-joined `name=value` parameters, or one
quoted parameter with a known Digest, OAuth, AWS4 or HTTP Signatures name. A
bare value may hold `;` (AWS4's `SignedHeaders=host;x-amz-date`), and a comma
may have one obs-fold on either side (RFC 7616 lays out one parameter per
line).

This partly addresses Consiliency/pmcp#417: its repro (a secret-named query
key in a URL spelled with `\/` inside a JSON string) is withheld at every
depth, and the document stays a document. Other spellings in that issue are
not covered here.

What stays out, by design:

- the context-free shapes (`_shape_spans`), which read the base output;
- the default and operator policy patterns: they are not matched against the
  decoded views (owner decision; Consiliency/pmcp#482). As at depth 0, the
  URL query rule still asks them about a percent-decoded query value of a
  URL found in a view, which can only add redaction;
- escape depth 5 and more, counted from the raw input of a public entry
  point, and non-JSON escape dialects (`\'`, `\xNN`, `\U0001F600`, `\u{…}`).

Residuals. Each item is pinned by a test in
`tests/test_redaction_478_escaped.py` on every entry point at depths 0-2
(`test_disclosed_residuals`, `test_disclosed_over_redactions`), except where
noted, so a fix has to flip it on purpose. Main keeps every kept value too:

- an all-letter, single-case or Titlecase credential of 8 or more letters
  followed by another word on its line
  (`Authorization: Basic qzhunterwombat was rejected`), and one of 7 letters
  or fewer (`Authorization: Basic qzhuntr`) (owner decision: the narrow
  prose guard);
- a credential that is a number, a date or a time (`Authorization: Pin
  12345678`, `2026-11-07`, `10:30:15`);
- a malformed token68 split by a comma (`Basic Zm9v,OmJhcg`: `OmJhcg` reads
  as the next list item);
- a single unquoted auth-param after the scheme
  (`Authorization: Digest response=ab12cd34ef`,
  `Authorization: AWS4-HMAC-SHA256 Signature=ab12cd34ef`): the owner's rule
  takes two or more comma-joined parameters, or one quoted parameter with a
  known name. A truncated header can end up like this;
- auth-params joined by spaces instead of commas (not RFC 7616):
  only a first, quoted, known-name parameter is taken, and the rest stay;
- over-redaction: a token68-shaped word after the scheme is redacted even in
  prose (`Authorization: denied user_id`), and so are two or more
  comma-joined `name=value` fields after it
  (`authorization=none user=bob, retry=2`) and one quoted field with a known
  auth-param name (`authorization=none realm="internal"`);
- inside the depth cap, two contrived shapes are withheld at depth 0 but not
  at depth 1, as on main (`test_disclosed_escaped_residuals`): a second
  `Authorization` key read as the first one's quoted value
  (`Authorization: Basic "Authorization": Basic keyid:…, x`), and a Bearer
  value that opens with `\\"`, where the view rules stop;
- a URL as a credential: a token68 run holding `://` is not taken;
- applying a redactor twice is idempotent on the tested corpora
  (`test_idempotent_on_the_named_corpora`), but not in general: a second pass
  can merge `[REDACTED][REDACTED]` or re-encode a marker inside a URL.
  Nothing is re-exposed.

Cost: work and memory are linear, counted (the work counter) and measured
(`tracemalloc` peaks) to 256 KiB. Peak memory per character is flat with
size; on the measured adversarial shapes it stayed under 90 bytes (main, on
the same shapes: under 60). CPU stays within the slow tier's ratio canary:
6x the pre-Consiliency/pmcp#478 pipeline on the same input, at 256 KiB. A
separate probe at 1 MiB measured about 0.9-2.6x on every shape it tried.

Percent/`+` normalization and broader structured redaction are separate work
(Consiliency/pmcp#407, Consiliency/pmcp#423).
