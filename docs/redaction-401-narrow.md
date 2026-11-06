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

Percent/`+` normalization and broader structured redaction are separate work
(Consiliency/pmcp#407, Consiliency/pmcp#423).
