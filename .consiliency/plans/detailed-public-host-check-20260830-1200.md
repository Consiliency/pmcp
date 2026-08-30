# Detailed plan: fix the IP-literal classification, and stop the docs overclaiming

> **Revision 3 (2026-08-30).** Two board rounds. Rev 1 claimed no
> attacker-influenced path reached a fetch or relay — disproved. Rev 2 proposed a
> trust split — the board showed it would kill the elicitation feature and that
> its own acceptance criterion would **freeze two live classification bugs**.
> Scope narrowed on the operator's instruction: land the self-contained
> IP-literal fix here; the trust split moves to **#211** with the evidence.

## Task

Close Consiliency/pmcp#210's verifiable half. `_is_public_auth_host` classifies
two non-public address ranges as public, and three user-facing sentences promise
more than the code checks.

## What is actually broken — measured

```
100.64.0.1      CGNAT / RFC 6598 shared      public=True   <-- wrong
fec0::1         IPv6 site-local (RFC 3879)   public=True   <-- wrong
169.254.169.254 link-local IMDS              public=False  correct
::ffff:10.0.0.5 IPv4-mapped RFC1918          public=False  correct
8.8.8.8         genuinely public             public=True   correct
```

`_is_public_auth_host` tests `is_private or is_loopback or is_link_local or
is_multicast or is_unspecified`. That misses **RFC 6598 shared address space**
(`100.64.0.0/10`, used by carrier-grade NAT and by some cloud metadata paths) and
**deprecated IPv6 site-local** (`fec0::/10`). Both are non-public and both pass.

**A seat also claimed `::ffff:169.254.169.254` passes. It does not** — verified
above; Python's `is_private` handles the IPv4-mapped form. Recorded so the next
reader does not re-derive it.

**WAS WRONG (rev 2):** its acceptance criterion said "IP literals still behave as
today", which would have **pinned both bugs as intended behaviour**. That is the
same entrenching error the board caught in rev 1's criterion, in the criterion
written to replace it.

## Changes

### `src/pmcp/auth.py` (modify)

- `_is_public_auth_host` (`:127`) — modify — **define public positively** rather
  than by subtracting a list of bad ranges. A denylist of address properties has
  now missed two ranges; the next added range will be missed the same way.
  Prefer `is_global` where the stdlib offers it, falling back to an explicit
  check that includes shared (`100.64.0.0/10`) and site-local (`fec0::/10`).
  Whatever form, the rule must be stated so a reader can see what "public" means
  without enumerating exclusions.
- The docstring — modify — state plainly that **a DNS name is accepted without
  resolution**. That limitation is real and stays; it is #211's subject, and it
  must be visible here rather than implied.
- `sanitize_public_auth_url`'s error message (`:143`) — modify — stop claiming
  the host "must be public". Say what was checked: a non-public **IP literal**
  was rejected. Do not imply a name was verified.

### `tests/test_auth.py` (modify)

- Extend the existing public-URL test with the two fixed ranges and the
  already-correct ones, each named: `100.64.0.1` and `fec0::1` **rejected**;
  `169.254.169.254`, `::ffff:10.0.0.5`, `10.0.0.5`, `127.0.0.1`, `localhost`
  rejected; `8.8.8.8` and a public DNS name accepted.
- Add a test asserting the **name limitation** explicitly — that a DNS name is
  accepted unresolved — with a comment pointing at #211. Pinning a limitation is
  only right when it is *documented as a limitation*; rev 2's mistake was pinning
  it as correctness.

## Documentation impact

- `CHANGELOG.md` — add — `### Fixed`: two non-public IP ranges were accepted by
  the auth-URL host check.
- `README.md:168`, `README.md:173`, `SECURITY.md:39` — modify — all three promise
  a "public host" or claim private/link-local/loopback/multicast hosts are
  rejected. True only for IP literals. Qualify each. **`README:173` was found by a
  seat and is absent from rev 2's list**; `SECURITY.md` was absent from rev 1's.

## Dependencies & order

1. The classification fix and its unit tests.
2. The message and docstring.
3. The three documentation sentences.
4. CHANGELOG.

## Verification

```bash
uv run pytest -q tests/test_auth.py -k public_auth_url
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/
```

This host's `/tmp/package.json` and `/tmp/node_modules` make the npm identity
tests fail locally in a way that reproduces on a clean `main` export
(`tests/conftest.py`). Verify against `main` before blaming this diff, and do not
claim a green local suite that was not green.

## Acceptance criteria

- [ ] `100.64.0.1` and `fec0::1` are **rejected**, proven by a test that fails
      against `main`. These are the defect; without a RED-on-main demonstration
      the fix is unproven.
- [ ] Every literal listed under "What is actually broken" keeps its correct
      verdict — in particular `8.8.8.8` and `::ffff:10.0.0.5`, so the fix is not a
      blanket rejection.
- [ ] A public DNS name is still accepted, and a test says **in its name** that
      this is a known limitation tracked by #211 — not a correctness assertion.
- [ ] No error message claims a host "must be public" or was verified public.
      Asserted on message text.
- [ ] `README.md:168`, `README.md:173` and `SECURITY.md:39` no longer promise
      more than IP-literal filtering.
- [ ] Full suite green.

## Non-goals

- **The trust split, the fetch primitive, and the relay path** — all moved to
  **#211**, which carries the board's evidence and the open design questions.
- **DNS resolution**, and **suffix denylists**. Both rejected with reasons
  recorded in #211.

## Execution Policy

- execute: effort=low, reason=a classification predicate, its tests, and three
  documentation sentences; the subtlety is in not pinning the bugs as behaviour
