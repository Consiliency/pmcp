# Detailed plan: make the public-host check mean what it says

## Task

Close Consiliency/pmcp#210. `_is_public_auth_host` returns `True` — "public",
therefore allowed — for **every hostname that is not an IP literal**, so a gate
whose error reads *"Public auth URL host must be public"* admits
`metadata.google.internal`.

## Severity — corrected downward from the issue text

The issue was filed from reading, and said severity "depends on whether an
attacker can influence the auth URL, which I have not established". Established
now, and it is **lower** than the issue implies. This is hardening and honesty,
not a live vulnerability. Recording that plainly matters more than the fix: an
overstated finding wastes the next reader's time and erodes trust in the ones
that are real.

Traced every call site:

| site | input provenance | what happens to it |
|---|---|---|
| `transport/http.py:281` | operator config (`resource_server_jwks_url`) | validated, then **fetched** via `AsyncJWKS` |
| `auth.py:205` | same JWKS URL | stored redacted for display; `_raw_url` fetched |
| `auth.py:468` | **untrusted** — a remote server's `WWW-Authenticate` header | stored on `AuthChallengeInfo` |
| `auth.py:519` | operator config / manifest / registry entries | surfaced in public metadata |
| `auth.py:366`, `:639` | elicitation URLs | redacted for display |
| `cli_commands/doctor.py:80` | operator config | printed |

Two findings from that trace:

- **The only fetched URL is operator-supplied.** An operator can already point
  their own gateway anywhere; a private DNS name there is a configuration choice,
  not an attack. `AsyncJWKS._fetch` also already sets `allow_redirects=False`,
  which is evidence this class was thought about.
- **The genuinely untrusted value is inert.** `AuthChallengeInfo.resource_metadata_url`
  is parsed from a remote header, sanitised — and then **never referenced again
  anywhere in `src/`**. Verified by grep. It is not fetched and not relayed.

So there is **no verified attacker-controlled path to a fetch or a relay today.**

What remains is real but narrower: a validation boundary that explicitly handles
"untrusted authorization metadata" (`auth.py:519`'s own docstring) makes a
promise it does not keep, and one input class — `protected_resource_metadata_url`
carried on **registry entries** (`manifest/registry.py:355`) — is remote-sourced,
so the boundary is doing untrusted-input work even where today's code does not
reach a fetch. *(Reasoned, not verified: I did not trace whether a registry
entry's value can reach a client-visible header.)*

## Changes

**The fix is to make the guarantee honest, not to resolve DNS.** Resolution at
validation time buys little here — the only fetched URL is operator-supplied —
while adding a TOCTOU gap, a DNS lookup on a caller-supplied string, and a new
failure mode to define. That is a poor trade for a gate that is not currently
load-bearing against an attacker.

### `src/pmcp/auth.py` (modify)

- `_is_public_auth_host` (`:127`) — modify — keep the behaviour, fix the
  **contract**. Rename to something that states what it checks, e.g.
  `_is_non_public_ip_literal` inverted, or keep the name and add a docstring that
  says: this rejects IP literals that are private, loopback, link-local,
  multicast or unspecified, and **accepts any DNS name without resolving it**.
  A reader must be able to learn the limit from the function, not by testing it.
- `sanitize_public_auth_url` (`:143`) — modify — the raised message currently
  reads *"Public auth URL host must be public."* Narrow it to what was actually
  checked, e.g. *"Public auth URL host must not be a private, loopback,
  link-local or multicast IP address."* The current wording is what makes a
  reader believe a name was vetted.
- The module docstring or a comment near the gate — add — state the deliberate
  decision **not** to resolve, and why (TOCTOU, DNS lookup on caller-supplied
  input, and that the only fetched URL is operator-supplied). Without this, the
  next reader re-derives the question or "fixes" it by adding resolution.

### `tests/test_auth.py` (modify)

- `test_sanitize_public_auth_url_rejects_invalid_and_non_public_urls` (`:738`) —
  extend — add the **accepted** cases as explicit, named assertions:
  `metadata.google.internal`, `internal.corp` and a plain
  `example.com` all pass. Today that behaviour is real but unwritten, so a future
  change to resolution would silently alter it with nothing going red.
  A test that pins a *limitation* is how the limitation stays a decision.
- Add the rejected IP-literal cases if not already covered: `10.0.0.5`,
  `127.0.0.1`, `169.254.169.254`, `localhost`. Verified all four currently reject.

## Documentation impact

- `CHANGELOG.md` — add — `### Changed`, one line: the error message narrowed to
  describe what is actually validated. No behaviour change, so not `### Fixed` —
  claiming a fix would overstate it exactly as the issue did.
- `README.md` — modify **only if** it documents the public-URL requirement for
  auth metadata; check before editing.

## Dependencies & order

1. Tests first, pinning both current behaviours — accepted names and rejected
   literals — **against unchanged code**. They must pass before anything changes;
   that is what makes them a description of today rather than of the edit.
2. Docstring, message, and comment.
3. CHANGELOG.

## Verification

```bash
uv run pytest -q tests/test_auth.py -k public_auth_url
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/
```

Note on this host: `/tmp/package.json` and `/tmp/node_modules` make the npm
identity tests fail locally in a way that reproduces on a clean `main` export
(`tests/conftest.py` documents it). Verify against `main` before attributing any
failure to this diff, and do not claim a green local suite that was not green.

## Acceptance criteria

- [ ] `sanitize_public_auth_url` still **rejects** `10.0.0.5`, `127.0.0.1`,
      `169.254.169.254` and `localhost`, and still **accepts**
      `metadata.google.internal` and `example.com` — both directions asserted by
      name, so the limitation is pinned rather than incidental.
- [ ] The raised message no longer claims the host "must be public"; it names the
      IP-literal classes actually rejected. Asserted on the message text.
- [ ] `_is_public_auth_host`'s docstring states that a DNS name is accepted
      unresolved, and the non-resolution decision is recorded with its reasons.
- [ ] No behaviour change: the full test suite passes with no test modified other
      than the additions above.

## Non-goals

- **Resolving hostnames.** Explicitly rejected above, with reasons. If a future
  change makes an untrusted URL reachable by a fetch or a client-visible relay,
  that decision should be revisited — and the recorded reasoning is what will
  make that revisit possible.
- Allowlisting auth hosts. Viable if the set is ever knowable; it is not today.
- Auditing the `registry.py` provenance question named above. It is reasoned, not
  verified, and deserves its own look rather than a guess folded in here.

## Execution Policy

- execute: effort=low, reason=a message, a docstring, and tests that pin existing
  behaviour; the substance is in being accurate about what is and is not checked
