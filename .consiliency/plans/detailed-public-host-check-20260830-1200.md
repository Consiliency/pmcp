# Detailed plan: fix the IP-literal classification, and stop the docs overclaiming

> **Revision 4 (2026-08-30).** Rev 3 boarded 2 DISAGREE. Its prescribed recipe —
> "prefer `is_global`" — **would have left `fec0::1` broken and regressed the
> existing multicast test**. A rule I then drafted myself was wrong in both
> directions too. Rev 4 carries a rule verified against 19 literals, 0
> mismatches.
>
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

- `_is_public_auth_host` (`:127`) — modify — **unwrap, then classify positively.**
  The verified rule, 19 literals, 0 mismatches on Python 3.10:

  ```python
  NAT64 = ip_network("64:ff9b::/96")

  def _unwrap(a):
      if isinstance(a, IPv6Address):
          if a.ipv4_mapped is not None:
              return a.ipv4_mapped
          if a in NAT64:                      # embeds v4 in the low 32 bits
              return IPv4Address(int(a) & 0xFFFFFFFF)
      return a

  addr = _unwrap(ip_address(hostname))
  return (addr.is_global
          and not getattr(addr, "is_site_local", False)
          and not addr.is_multicast)
  ```

  **WAS WRONG (rev 3):** "prefer `is_global`, falling back to an explicit check".
  `is_global` exists on every supported Python, so the fallback never runs — and
  `is_global` is **`True` for `fec0::1`**, leaving one of the two named defects in
  place. Two seats caught it.

  **Also wrong — a rule I drafted after rev 3**, recorded because it is the
  instructive failure: `is_global and not is_site_local and not is_reserved`
  **rejects `::ffff:8.8.8.8`** (every IPv4-mapped address is `is_reserved`) and
  **accepts `224.0.0.1` and `ff02::1`** (multicast is `is_global` on 3.10),
  which would have regressed the existing multicast test at
  `tests/test_auth.py:751`. Getting it wrong in both directions is why the rule
  above is stated as verified output rather than reasoning.

  Three mapped/embedded forms drove the design, none of which the issue named:
  `::ffff:100.64.0.1` (mapped CGNAT — `is_private` unwraps but CGNAT is not
  IPv4-private, so it stayed public), and `64:ff9b::a00:5` / `64:ff9b::7f00:1`
  (NAT64 embedding `10.0.0.5` and `127.0.0.1`). NAT64 wrapping a genuinely public
  address — `64:ff9b::808:808` → `8.8.8.8` — must still be **accepted**, which is
  what makes unwrapping the right move rather than rejecting the prefix wholesale.
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

- [ ] **All five newly-rejected literals** fail against `main` and pass here:
      `100.64.0.1`, `::ffff:100.64.0.1`, `fec0::1`, `64:ff9b::a00:5`,
      `64:ff9b::7f00:1`. RED-on-main required for each — rev 3 named only the
      first and third, and a fix satisfying rev 3 could still admit the other three.
- [ ] **All five must-accept literals** still pass: `8.8.8.8`, `93.184.216.34`,
      `2001:4860:4860::8888`, `::ffff:8.8.8.8`, and `64:ff9b::808:808`. The last
      two are the over-rejection traps — a rule using `is_reserved` fails both.
- [ ] **The existing multicast/link-local rejections still hold**: `224.0.0.1`,
      `ff02::1`, `169.254.169.254`, `10.0.0.5`, `::ffff:10.0.0.5`, `127.0.0.1`,
      `localhost`, `fc00::1`, `2001:db8::1`, `::`. A rule built on bare
      `is_global` regresses the first two.
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
