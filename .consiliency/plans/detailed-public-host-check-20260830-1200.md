# Detailed plan: make the public-host check mean what it says

> **Revision 2 (2026-08-30).** Boarded **2 DISAGREE / 1 AGREE**. The DISAGREEs
> disproved rev 1's central claim — that no attacker-influenced path reaches a
> fetch or a relay. Rev 1's severity table also **mislabelled a fetch as
> display**. Scope changed on the operator's instruction: reject non-public names
> on the untrusted paths, rather than only documenting the gap.

## Task

Close Consiliency/pmcp#210. `_is_public_auth_host` returns `True` — "public",
therefore allowed — for **every hostname that is not an IP literal**, so a gate
whose error reads *"Public auth URL host must be public"* admits
`metadata.google.internal`.

## Severity — corrected, twice

Rev 1 traced the call sites and concluded there was "no verified
attacker-controlled path to a fetch or a relay today". **That was wrong**, and
two seats showed why. Both corrections are verified here:

**1. `auth.py:639` is a fetch, not display.** `fetch_json_metadata` calls
`sanitize_public_auth_url(url, allow_loopback_http=True)` and then **`urlopen`s
the result**. Rev 1's table listed it under "redacted for display". It is
currently called only from `tests/test_auth.py`, so it is a *latent* fetch
primitive rather than a live path — but the classification was simply wrong, and
a latent fetch behind a gate that does not check names is exactly the thing that
becomes live when someone wires it up.

**2. The untrusted URL is relayed, not inert.** Rev 1 argued
`AuthChallengeInfo.resource_metadata_url` was inert because grep found no
internal consumers. Being unread inside `src/` is not the same as being inert: it
is a field on a public dataclass, and the sibling elicitation path
(`sanitize_url_elicitation_url` → `url_elicitations`) carries a **downstream
server's URL to the operator**, having passed a check that reports it as public.
pmcp is the vouching party. That is a confused-deputy relay, and it is the exact
thing the "must be public" wording promises against.

**3. The user-facing promise is broader than the code, in two files.**
`README.md:168` and `SECURITY.md:39` both require the URL to be `https` **"on a
public host"**. Rev 1 made the README conditional and did not mention
`SECURITY.md` at all. Narrowing only the exception text would leave the same
overstatement in the security policy.

**The remaining true part of rev 1:** the only *fetched* URL reachable in
production today (`resource_server_jwks_url` → `AsyncJWKS`) is operator-supplied,
and that fetch already sets `allow_redirects=False`. So the operator path is not
where the risk is — which is what makes a trust-split the right shape.

## Changes

**Split the gate by trust level.** The seam already exists and is currently
pointed the wrong way: `sanitize_url_elicitation_url` (`auth.py:363`) is a named
entry point for the untrusted elicitation path, and it delegates to
`sanitize_public_auth_url(url, allow_loopback_http=True)` — *more* permissive
than the operator path, not less.

**No DNS resolution**, for the reasons rev 1 gave and the board upheld: TOCTOU,
a lookup on caller-supplied input, and it is not real SSRF defence anyway
(that needs connection-time IP pinning). The question is what a no-network rule
can honestly assert.

### `src/pmcp/auth.py` (modify)

- `_public_host_verdict(hostname)` — add — returns a three-way result rather than
  a bool: `PRIVATE_LITERAL` (an IP literal in a private/loopback/link-local/
  multicast/unspecified range, or `localhost`), `PUBLIC_LITERAL`, or
  `UNVERIFIED_NAME` (any DNS name — because without resolving, that is the
  strongest honest statement). Rev 1's bug was collapsing the third case into
  "public".
- `sanitize_public_auth_url` (`:143`) — modify — take a `trust` argument.
  **Operator paths** accept `UNVERIFIED_NAME` (unchanged behaviour — an operator
  can already point their gateway anywhere). **Untrusted paths reject it.**
- `sanitize_url_elicitation_url` (`:363`) — modify — pass the untrusted trust
  level, and **stop passing `allow_loopback_http=True`**. A downstream server
  should not be able to hand the operator a loopback URL that pmcp presents as
  validated.
- The `WWW-Authenticate` parse (`:468`) — modify — same untrusted level.
- Error messages — modify — say which rule rejected the input, and never claim a
  name was verified public.

**The honest limit, to be stated in the code and the CHANGELOG:** rejecting
`UNVERIFIED_NAME` on untrusted paths means those paths accept **only IP literals
in public ranges**. That is strict — it will reject legitimate public hostnames
from well-behaved downstream servers. That is a real cost and the board should
weigh it; the alternative is an operator allowlist for the untrusted paths, which
is more work and more configuration. **Do not soften this into a suffix denylist**
(`.internal`, `.local`, `.corp`): a denylist fails open on a custom internal TLD,
which is the same fail-open shape this repo has now fixed five times.

## Documentation impact

- `CHANGELOG.md` — add — `### Changed`, one line: the error message narrowed to
  describe what is actually validated. No behaviour change, so not `### Fixed` —
  claiming a fix would overstate it exactly as the issue did.
- `README.md:168` — modify — **required**, not conditional. It says the URL must
  be `https` "on a public host"; that is only true for IP literals.
- `SECURITY.md:39` — modify — **required**. Same wording, in the security policy,
  which is the worst place to overclaim. Rev 1 missed this file entirely.

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

- [ ] On an **untrusted** path (`sanitize_url_elicitation_url`, and the
      `WWW-Authenticate` parse), `https://metadata.google.internal/...` and
      `https://internal.corp/...` are **rejected**.
      **WAS WRONG (rev 1):** it required these to be *accepted*, which two seats
      independently objected to — pinning them would have codified an
      attacker-influenced relay as intended behaviour.
- [ ] On an **operator** path (`resource_server_jwks_url`, doctor), a public DNS
      name such as `auth.example.com` is still **accepted** — this must not
      become a blanket rejection that breaks every real deployment.
- [ ] IP literals still behave as today on both paths: `10.0.0.5`, `127.0.0.1`,
      `169.254.169.254`, `localhost` rejected; a public literal accepted.
- [ ] `sanitize_url_elicitation_url` no longer permits loopback HTTP — asserted
      directly, since rev 1 did not notice it was passing `allow_loopback_http=True`.
- [ ] No error message claims a hostname "must be public" or was verified public.
      Asserted on message text.
- [ ] `README.md:168` and `SECURITY.md:39` no longer promise a public host
      without qualification.
- [ ] Full suite green, and the existing `test_auth.py` public-URL tests still
      pass or are updated with a stated reason per change.

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
