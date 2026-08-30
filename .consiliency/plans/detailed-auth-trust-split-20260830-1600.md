# Detailed plan: stop vouching for URLs a downstream server supplied

> **Revision 2 (2026-08-30).** Boarded 2 DISAGREE. Rev 1 named the wrong types
> and would have broken a frozen operator contract. Both verified below.

## Task

Close Consiliency/pmcp#211. `sanitize_public_auth_url` applies one permissiveness
to inputs of very different provenance, and pmcp presents the result as
validated. A downstream server can hand back a URL on an internal host and have
pmcp show it to the operator as though pmcp checked it.

## What #210 already closed — measured today, post-merge

#211 was filed before #210 landed. Re-checking the untrusted elicitation path
now:

```
https://0xA9FEA9FE/x              rejected     <- #210 closed this
https://metadata.google.internal/x  ACCEPTED   <- remains
http://127.0.0.1/cb                 ACCEPTED   <- remains
https://auth.vendor.com/x           ACCEPTED   <- legitimate, must stay
```

So the numeric-form relay is gone. **What is left is names, and loopback HTTP.**
That is a materially smaller issue than filed, and the plan is scoped to it.

Two other open questions from the issue, now answered:

- **The JWKS URL is purely operator-supplied.** It reaches
  `transport/http.py:281` only from `cli.py:2295`'s `--oauth-jwks-url`. It does
  **not** arrive from a manifest or registry entry. The issue speculated it
  might; it does not.
- **`fetch_json_metadata` still has no production caller** — only its own
  definition at `auth.py:756`.

## The shape of the fix

**Do not reject names on the untrusted paths.** A board round established that
rejecting unverifiable names there would refuse
`https://auth.vendor.com/...` from a well-behaved server — killing the feature
the check exists to protect. Rejecting is right for a *fetch* and wrong for a
*relay*.

The harm is not that pmcp accepts the URL. It is that pmcp **presents it as
validated**. So:

- **Fail closed on fetches.** Anything pmcp will retrieve must be verifiably
  public.
- **Stop vouching on relays.** Keep passing the URL through, but stop implying
  pmcp checked where it points.

### `src/pmcp/auth.py` (modify)

- `fetch_json_metadata` (`:756`) — **delete it**, or give it an explicit
  fetch-trust gate. It is a live `urlopen` primitive with no production caller.
  Deleting is preferred: a loaded gun with no user is a liability, and it can be
  restored from git with its trust decision made deliberately. **If it is kept**,
  it must reject anything that is not a verifiably-public literal, since it
  fetches.
- `sanitize_url_elicitation_url` (`:485`) — modify — **split it by provenance.**
  **WAS WRONG (rev 1):** it said "stop passing `allow_loopback_http=True`",
  full stop. Verified: this helper is **shared** between the downstream-payload
  path (`auth.py:736`) and the **operator-supplied** acknowledgement path
  (`handlers.py:4592`, `parsed.elicitation_url`). Removing loopback globally
  would break the operator's local-OAuth flow, which `plans/phase-plan-v4-elicit.md`
  records as a frozen contract. The remote path must lose loopback HTTP; the
  operator path must keep it. Two policies, two call sites, regression tests for
  both.
- A `verified` signal on the returned value — add — whether the host was
  verified as a public literal or merely accepted as an unresolved name. It must
  **default to unverified**: a default of "verified" reintroduces vouching at any
  call site the change misses.

### Presentation (modify)

- **`UrlElicitationInfo` (`types.py:163`) is the primary relay surface and rev 1
  omitted it entirely.** Its docstring reads *"URL-mode elicitation details **safe
  to display to users**"* — the type asserts precisely the claim this issue
  disproves. It carries `url`, `message`, `next_step` and no verification field,
  and `handlers.py:4610` constructs it with an unconditional open-the-URL
  instruction. Add the signal here first; without it AC 3 cannot be met on the
  surface that actually reaches an operator. Correct the docstring too.
- `cli.py` — where `url_elicitations` are shown — must state that an unverified
  URL came from the server and its destination was not checked, in **both** the
  human text and `--json`.
- **`AuthMetadataInfo` needs a PER-URL signal, not one object-level bool.** It
  carries five independent URL fields; a single flag cannot be honest when one is
  a public literal and another is a name. Rev 1 specified one signal per object.
- `AuthChallengeInfo.resource_metadata_url` — same per-URL treatment.

## Documentation impact

- `CHANGELOG.md` — `### Changed`: pmcp no longer implies it verified where a
  server-supplied auth URL points, and no longer accepts a loopback HTTP URL from
  a downstream server.
- `README.md` / `SECURITY.md` — the #210 wording already says the check filters
  IP literals. Extend to state that a server-supplied URL is relayed unverified
  and is presented as such.

## Dependencies & order

1. The `verified` signal on `UrlElicitationInfo` first, defaulting to
   unverified, with unit tests — it is the surface that reaches an operator.
2. The provenance split on `sanitize_url_elicitation_url` — remote loses
   loopback HTTP, operator keeps it — with regression tests both ways.
3. `fetch_json_metadata` — delete or gate.
4. Presentation changes.
5. Docs.

## Verification

```bash
uv run pytest -q tests/test_auth.py
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/
```

`/tmp/package.json` and `/tmp/node_modules` on this host make ~107 npm-identity
tests fail locally in a way that reproduces on a clean `main` export
(`tests/conftest.py`). Verify against `main` before blaming this diff.

## Acceptance criteria

- [ ] A **downstream** elicitation payload carrying `http://127.0.0.1/cb` is
      **rejected**, proven by a test failing against `main`. **And the operator
      path at `handlers.py:4592` still accepts it** — both directions, or the
      change breaks a frozen contract. `test_sanitize_url_elicitation_url_allows_loopback_http`
      inverts for the remote path only.
- [ ] `https://auth.vendor.com/...` from a downstream server is **still
      accepted** — the feature must survive. A change that rejects names must not
      pass.
- [ ] A relayed URL whose host is an unresolved name is marked **unverified** on
      `UrlElicitationInfo`, and the gateway output, the CLI human text **and**
      `--json` all say so. Asserted on emitted text, not on a field's presence.
      Rev 1's criteria exercised only elicitation and CLI, which would let the
      challenge and metadata surfaces stay misleading while every test passed.
- [ ] `AuthMetadataInfo` with a **mix** — one public literal, one name — reports
      them differently. A single object-level flag cannot pass this.
- [ ] The signal defaults to **unverified** — proven by constructing the type
      without it and asserting the unverified presentation.
- [ ] A relayed URL whose host is a verified public literal is **not** marked
      unverified — so the signal distinguishes, rather than labelling everything.
- [ ] `fetch_json_metadata` is either gone, or rejects a non-public literal.
      Asserted either way, so "we left it alone" cannot pass.
- [ ] Full suite green.

## Non-goals

- **Resolving hostnames.** Rejected across three board rounds: TOCTOU, a lookup
  on caller-supplied input, and it is not SSRF defence without connection-time IP
  pinning.
- **The trailing-dot IPv4 form** (`169.254.169.254.`), which takes the name path.
  It is name-shaped; if the `verified` signal is right, it is relayed unverified
  like any other name.
- Re-opening #210's literal classification. It is closed and pinned by 22 tests.

## Execution Policy

- execute: effort=medium, reason=small diff, but it changes what pmcp claims to
  an operator, and the risk is over-rejecting a legitimate auth flow
